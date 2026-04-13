# 1. python 内置标准库
import ast                                # 解析Python源代码为抽象语法树，用于分析/修改代码结构
import inspect                            # 检查函数、类、模块的信息（比如获取函数源码、参数）
import os                                 # 操作系统交互：读取环境变量、文件路径、创建文件夹等
import re                                 # 正则表达式：文本查找、替换、匹配（比如提取关键词、过滤内容）
from string import Template               # 字符串模板：方便批量替换文本中的变量
from typing import List, Callable, Tuple  # 类型注解：标注变量/函数类型，让代码更易读、防错

# 2. 第三方库 (需要用pip安装才能使用)
import click                              # 命令行工具：快速创建可在终端运行的命令、参数、选项
from dotenv import load_dotenv            # 加载.env文件：把私密配置（密钥、账号）存在文件里，不写死在代码
import httpx                              # HTTP客户端：支持代理配置，用于绕过地区限制
import platform                           # 获取系统信息：判断是Windows、Mac还是Linux
from google import genai

# 3. 自定义模块 (项目中自己写的)
from prompt_template import react_system_prompt_template, plan_system_prompt_template
from tools import read_file, write_to_file, run_terminal_command, list_directory, search_in_files, web_search, query_knowledge_base

class ReActAgent:
    # Callable意味可调用的函数
    def __init__(self, tools: List[Callable], model: str, project_directory: str):
        # 把传入的工具函数列表转成字典
        # key是函数名 values是函数本身  方便后续直接通过名字调用工具
        self.tools = { func.__name__: func for func in tools }
        self.model = model
        self.project_directory = project_directory
        load_dotenv()
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        if proxy:
            httpx_client = httpx.Client(proxy=proxy)
            http_options = genai.types.HttpOptions(httpx_client=httpx_client)
            self.client = genai.Client(api_key=self.get_api_key(), http_options=http_options)
        else:
            self.client = genai.Client(api_key=self.get_api_key())
    
    MAX_HISTORY_MESSAGES = 20  # 超过此数量时触发历史压缩（不含 system 消息）

    def run(self, user_input: str):
        # 规划阶段：生成执行计划并展示给用户确认
        steps = self.plan(user_input)
        if not steps:
            print("\n\n⚠️ 规划失败，降级为纯 ReAct 模式执行...")
            return self._react_loop(user_input, context="")

        print("\n\n📋 执行计划：")
        for i, step in enumerate(steps, 1):
            print(f"  Step {i}: {step}")
        confirm = input("\n\n是否按此计划执行？（Y/N，直接回车确认）").strip().lower()
        if confirm == 'n':
            print("\n\n计划已取消，切换为直接对话模式...")
            return self._react_loop(user_input, context="")

        # 执行阶段：依次执行每个步骤，传递上下文
        context = ""
        for i, step in enumerate(steps, 1):
            print(f"\n\n{'='*50}")
            print(f"▶️  执行 Step {i}/{len(steps)}: {step}")
            print(f"{'='*50}")
            result = self.execute_step(step, context, user_input)
            context += f"\n[Step {i} 结果] {result}"

        # 所有步骤完成后，让模型汇总最终答案
        print("\n\n🏁 所有步骤执行完成，正在汇总...")
        summary_messages = [
            {"role": "system", "content": self.render_system_prompt(react_system_prompt_template)},
            {"role": "user", "content": f"<question>{user_input}</question>\n\n以下是各步骤的执行结果摘要，请基于此给出最终答案：\n{context}\n\n请直接输出 <final_answer>...</final_answer>"}
        ]
        final_content = self.call_model(summary_messages)
        final_match = re.search(r"<final_answer>(.*?)</final_answer>", final_content, re.DOTALL)
        return final_match.group(1) if final_match else context

    def plan(self, user_input: str) -> list:
        """调用一次 LLM 生成步骤列表，返回 step 字符串列表"""
        print("\n\n🗺️  正在规划任务步骤...")
        messages = [
            {"role": "system", "content": self.render_system_prompt(plan_system_prompt_template)},
            {"role": "user", "content": f"任务：{user_input}"}
        ]
        content = self.call_model(messages)
        steps = re.findall(r"<step>(.*?)</step>", content, re.DOTALL)
        return [s.strip() for s in steps if s.strip()]

    def execute_step(self, step: str, context: str, original_task: str) -> str:
        """用 ReAct 小循环执行单个步骤，最多 10 轮，返回执行结果摘要"""
        context_hint = f"\n\n前置步骤执行结果（供参考）：{context}" if context else ""
        system_msg = {"role": "system", "content": self.render_system_prompt(react_system_prompt_template)}
        messages = [
            system_msg,
            {"role": "user", "content": f"<question>总体任务：{original_task}\n\n当前步骤：{step}{context_hint}</question>"}
        ]
        max_rounds = 10
        for _ in range(max_rounds):
            self._compress_history(messages)
            content = self.call_model(messages)

            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                print(f"\n\n💭 Thought: {thought_match.group(1).strip()}")

            if "<final_answer>" in content:
                final_match = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                return final_match.group(1).strip() if final_match else "步骤完成"

            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                messages.append({"role": "user", "content": "<observation>格式错误：你必须输出 <action>...</action> 标签，请重新按格式输出。</observation>"})
                continue

            action = action_match.group(1).strip()
            try:
                tool_name, args = self.parse_action(action)
            except Exception as e:
                messages.append({"role": "user", "content": f"<observation>Action 格式解析失败：{e}。请确保格式为 tool_name(\"arg1\", \"arg2\")。</observation>"})
                continue

            if tool_name not in self.tools:
                available = ', '.join(self.tools.keys())
                messages.append({"role": "user", "content": f"<observation>工具 '{tool_name}' 不存在，可用工具：{available}</observation>"})
                continue

            print(f"\n\n� Action: {tool_name}({', '.join(str(a) for a in args)})")
            should_continue = input("\n\n是否继续？（Y/N）") if tool_name == "run_terminal_command" else "y"
            if should_continue.lower() != 'y':
                return "步骤被用户取消"

            try:
                observation = self.tools[tool_name](*args)
            except Exception as e:
                observation = f"工具执行错误：{str(e)}"
            print(f"\n\n🔍 Observation：{observation}")
            messages.append({"role": "user", "content": f"<observation>{observation}</observation>"})

        return "步骤达到最大执行轮数"

    def _react_loop(self, user_input: str, context: str) -> str:
        """原始 ReAct 单循环，作为降级兜底"""
        system_msg = {"role": "system", "content": self.render_system_prompt(react_system_prompt_template)}
        messages = [
            system_msg,
            {"role": "user", "content": f"<question>{user_input}</question>"}
        ]

        while True:
            self._compress_history(messages)
            content = self.call_model(messages)

            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                print(f"\n\n💭 Thought: {thought_match.group(1).strip()}")

            if "<final_answer>" in content:
                final_answer = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                return final_answer.group(1)

            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                print("\n\n⚠️ 模型未输出 <action>，反馈重试...")
                messages.append({"role": "user", "content": "<observation>格式错误：你必须输出 <action>...</action> 标签，请重新按格式输出。</observation>"})
                continue

            action = action_match.group(1).strip()
            try:
                tool_name, args = self.parse_action(action)
            except Exception as e:
                print(f"\n\n⚠️ Action 解析失败：{e}，反馈重试...")
                messages.append({"role": "user", "content": f"<observation>Action 格式解析失败：{e}。请确保格式为 tool_name(\"arg1\", \"arg2\")。</observation>"})
                continue

            if tool_name not in self.tools:
                print(f"\n\n⚠️ 工具 {tool_name} 不存在，反馈重试...")
                available = ', '.join(self.tools.keys())
                messages.append({"role": "user", "content": f"<observation>工具 '{tool_name}' 不存在，可用工具：{available}</observation>"})
                continue

            print(f"\n\n🔧 Action: {tool_name}({', '.join(str(a) for a in args)})")
            should_continue = input("\n\n是否继续？（Y/N）") if tool_name == "run_terminal_command" else "y"
            if should_continue.lower() != 'y':
                print("\n\n操作已取消。")
                return "操作被用户取消"

            try:
                observation = self.tools[tool_name](*args)
            except Exception as e:
                observation = f"工具执行错误：{str(e)}"
            print(f"\n\n🔍 Observation：{observation}")
            messages.append({"role": "user", "content": f"<observation>{observation}</observation>"})

    def _compress_history(self, messages: list):
        """当非 system 消息超过阈值时，保留 system + 首条用户问题 + 最近 N 条"""
        non_system = [m for m in messages if m["role"] != "system"]
        if len(non_system) <= self.MAX_HISTORY_MESSAGES:
            return
        system_msgs = [m for m in messages if m["role"] == "system"]
        first_user = next((m for m in messages if m["role"] == "user"), None)
        recent = non_system[-(self.MAX_HISTORY_MESSAGES // 2):]
        kept = system_msgs + ([first_user] if first_user and first_user not in recent else []) + recent
        messages[:] = kept
        print(f"\n\n📦 历史已压缩，保留 {len(messages)} 条消息")

    # 给 AI 生成工具使用说明书
    def get_tool_list(self) -> str:
        """生成工具列表字符串，包含函数签名和简要说明"""
        tool_descriptions = []
        for func in self.tools.values():
            name = func.__name__
            signature = str(inspect.signature(func))
            doc = inspect.getdoc(func)
            tool_descriptions.append(f"- {name}{signature}: {doc}")
        return "\n".join(tool_descriptions)
    
    # 返回一段完整 Ready-to-use 的 AI 系统提示词
    def render_system_prompt(self, system_prompt_template: str) -> str:
        """渲染系统提示模板，替换变量"""
        """
        os.listdir(self.project_directory)  列出项目文件夹里所有文件名
        os.path.join(路径, 文件名)
        转成绝对路径(完整路径，如 /user/project/main.py)
        """
        tool_list = self.get_tool_list()
        file_list = ", ".join(
            os.path.abspath(os.path.join(self.project_directory, f))
            for f in os.listdir(self.project_directory)
        )
        return Template(system_prompt_template).substitute(
            operating_system=self.get_operating_system_name(),
            tool_list=tool_list,
            file_list=file_list
        )
        
    def get_api_key(self) -> str:
        """Load the API key from an environment variable."""
        load_dotenv()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("未找到 GOOGLE_API_KEY 环境变量，请在 .env 文件中设置。")
        return api_key
    
    def call_native_model(self, messages):
        print("\n\n正在请求模型，请稍等...")
        import requests
    
        # Ollama 原生接口，彻底摆脱 OpenAI
        response = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False
            },
            timeout=120
        )
        
        
        response.raise_for_status()
        content = response.json()["message"]["content"]
        messages.append({"role": "assistant", "content": content})
        return content
    
    def call_model(self, messages):
        print("\n\n正在请求 Gemini 2.5 Flash，请稍等...")
        
        # 拆分系统提示词（Gemini要求单独传，不能放在对话历史里）
        system_prompt = ""
        chat_history = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                # 转换为Gemini支持的消息格式
                chat_history.append({
                    "role": msg["role"],
                    "parts": [{"text": msg["content"]}]
                })
        
        # 流式调用，实时打印 token
        chunks = []
        for chunk in self.client.models.generate_content_stream(
            model=self.model,
            contents=chat_history,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7
            )
        ):
            if chunk.text:
                print(chunk.text, end="", flush=True)
                chunks.append(chunk.text)
        
        print()  # 换行
        content = "".join(chunks) if chunks else "模型未返回有效内容，请重试"
        messages.append({"role": "model", "content": content})
        return content
    
    def parse_action(self, code_str: str) -> Tuple[str, List[str]]:
        match = re.match(r'(\w+)\((.*)\)', code_str, re.DOTALL)
        if not match:
            raise ValueError("Invalid function call syntax")

        func_name = match.group(1)
        args_str = match.group(2).strip()

        # 手动解析参数，特别处理包含多行内容的字符串
        args = []
        current_arg = ""
        in_string = False
        string_char = None
        i = 0
        paren_depth = 0
        
        while i < len(args_str):
            char = args_str[i]
            
            if not in_string:
                if char in ['"', "'"]:
                    in_string = True
                    string_char = char
                    current_arg += char
                elif char == '(':
                    paren_depth += 1
                    current_arg += char
                elif char == ')':
                    paren_depth -= 1
                    current_arg += char
                elif char == ',' and paren_depth == 0:
                    # 遇到顶层逗号，结束当前参数
                    args.append(self._parse_single_arg(current_arg.strip()))
                    current_arg = ""
                else:
                    current_arg += char
            else:
                current_arg += char
                if char == string_char and (i == 0 or args_str[i-1] != '\\'):
                    in_string = False
                    string_char = None
            
            i += 1
        
        # 添加最后一个参数
        if current_arg.strip():
            args.append(self._parse_single_arg(current_arg.strip()))
        
        return func_name, args
    
    def _parse_single_arg(self, arg_str: str):
        """解析单个参数"""
        arg_str = arg_str.strip()
        
        # 如果是字符串字面量
        if (arg_str.startswith('"') and arg_str.endswith('"')) or \
           (arg_str.startswith("'") and arg_str.endswith("'")):
            # 移除外层引号并处理转义字符
            inner_str = arg_str[1:-1]
            # 处理常见的转义字符
            inner_str = inner_str.replace('\\"', '"').replace("\\'", "'")
            inner_str = inner_str.replace('\\n', '\n').replace('\\t', '\t')
            inner_str = inner_str.replace('\\r', '\r').replace('\\\\', '\\')
            return inner_str
        
        # 尝试使用 ast.literal_eval 解析其他类型
        try:
            return ast.literal_eval(arg_str)
        except (SyntaxError, ValueError):
            # 如果解析失败，返回原始字符串
            return arg_str
        
    def get_operating_system_name(self):
        os_map = {
            "Darwin": "macOS",
            "Windows": "Windows",
            "Linux": "Linux"
        }

        return os_map.get(platform.system(), "Unknown")

@click.command()
@click.argument('project_directory',
                type=click.Path(exists=True, file_okay=False, dir_okay=True))
def main(project_directory):
    project_dir = os.path.abspath(project_directory)

    tools = [read_file, write_to_file, run_terminal_command, list_directory, search_in_files, web_search, query_knowledge_base]
    agent = ReActAgent(tools=tools, model="gemini-2.5-flash", project_directory=project_dir)

    print("\n🤖 Agent 已启动，输入 'exit' 或 'quit' 退出对话")
    print(f"📁 工作目录：{project_dir}")
    print("=" * 50)

    while True:
        try:
            task = input("\n请输入任务：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n👋 已退出")
            break

        if not task:
            continue
        if task.lower() in ("exit", "quit", "q", "退出"):
            print("\n\n👋 已退出")
            break

        final_answer = agent.run(task)
        print(f"\n\n✅ Final Answer：{final_answer}")
        print("\n" + "=" * 50)

if __name__ == "__main__":
    main()