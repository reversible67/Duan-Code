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
from prompt_template import react_system_prompt_template, plan_system_prompt_template, subagent_system_prompt_template
from hooks import HookRunner, build_default_hook_runner
from tools import read_file, write_to_file, run_terminal_command, list_directory, search_in_files, web_search, query_knowledge_base
from skills import get_skill_registry, match_skill


class SubagentContext:
    """子智能体上下文：独立消息列表 + 限制工具集 + 最大轮数保护"""
    def __init__(self, prompt: str, tools: dict, agent: 'ReActAgent', max_turns: int = 8):
        self.messages = [
            {"role": "system", "content": agent.render_system_prompt(subagent_system_prompt_template)},
            {"role": "user", "content": f"<question>{prompt}</question>"}
        ]
        self.tools = tools          # 子智能体可用工具（不含 task，防递归）
        self.agent = agent          # 引用父智能体，用于调用 dispatch_model 等
        self.max_turns = max_turns

    def run(self) -> str:
        """执行子智能体 ReAct 循环，返回结果摘要"""
        for turn in range(self.max_turns):
            content = self.agent.dispatch_model(self.messages)

            if "<final_answer>" in content:
                match = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                return match.group(1).strip() if match else "子任务完成"

            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                self.messages.append({"role": "user", "content": "<observation>格式错误：必须输出 <action>...</action>，请重新输出。</observation>"})
                continue

            action = action_match.group(1).strip()
            try:
                tool_name, args = self.agent.parse_action(action)
            except Exception as e:
                self.messages.append({"role": "user", "content": f"<observation>Action 解析失败：{e}</observation>"})
                continue

            if tool_name not in self.tools:
                available = ', '.join(self.tools.keys())
                self.messages.append({"role": "user", "content": f"<observation>工具 '{tool_name}' 不存在，可用：{available}</observation>"})
                continue

            observation, should_stop = self.agent._run_tool_with_hooks(
                tool_name,
                args,
                self.messages,
                available_tools=self.tools,
                cancel_message="子任务被取消",
            )
            if should_stop:
                return observation

        return "子智能体达到最大轮数，未能完成任务"


class ReActAgent:
    # Callable意味可调用的函数
    def __init__(self, tools: List[Callable], model: str, project_directory: str, hook_runner: HookRunner | None = None):
        # 把传入的工具函数列表转成字典
        # key是函数名 values是函数本身  方便后续直接通过名字调用工具
        self.tools = { func.__name__: func for func in tools }
        self.model = model
        self.project_directory = project_directory
        self.hook_runner = hook_runner or build_default_hook_runner()
        self.session_started = False
        self.skill_registry = get_skill_registry()
        load_dotenv()
        if model.startswith("gemini"):
            proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
            if proxy:
                httpx_client = httpx.Client(proxy=proxy, timeout=httpx.Timeout(60.0, connect=10.0))
                http_options = genai.types.HttpOptions(httpx_client=httpx_client)
                self.client = genai.Client(api_key=self.get_api_key(), http_options=http_options)
            else:
                self.client = genai.Client(api_key=self.get_api_key())
        else:
            self.client = None  # Ollama 模式不需要 Gemini client
        self.tools["load_skill"] = self.load_skill
        # 子智能体可用工具集（不含 task，防递归）
        self.subagent_tools = { name: func for name, func in self.tools.items() if name != "task" }
        # 将 task 方法注册为工具，让父智能体可以调用子智能体
        self.tools["task"] = self.task
        self.session_history: list[dict] = []  # 会话级记忆：记录本次启动中的所有任务和答案
    
    MAX_HISTORY_MESSAGES = 20  # 超过此数量时触发历史压缩（不含 system 消息）
    MAX_SESSION_HISTORY = 5  # 注入上下文时最多使用最近 N 条历史

    def _build_session_context(self) -> str:
        """将最近 N 条会话历史格式化为字符串，注入当前任务上下文"""
        recent = self.session_history[-self.MAX_SESSION_HISTORY:]
        if not recent:
            return ""
        lines = ["以下是本次会话中已完成的历史任务（供参考）："]
        for i, record in enumerate(recent, 1):
            lines.append(f"[历史任务 {i}] 用户：{record['task']}")
            lines.append(f"           结果：{record['answer'][:200]}{'...' if len(record['answer']) > 200 else ''}")
        return "\n".join(lines)

    def run(self, user_input: str):
        original_task = user_input
        selected_skill = None
        hinted_skill = None
        session_hook_message = ""

        if not self.session_started:
            session_result = self.hook_runner.run("SessionStart", {
                "user_input": original_task,
                "project_directory": self.project_directory,
            })
            self.session_started = True
            if session_result["exit_code"] == 1:
                return session_result["message"] or "会话被 Hook 阻止"
            if session_result["exit_code"] == 2:
                session_hook_message = session_result["message"]

        # 显示会话历史摘要（若有）
        session_ctx = self._build_session_context()
        if session_ctx:
            print(f"\n\n 会话记忆已加载（{len(self.session_history)} 条历史）")

        # 优先级1：slash 命令精确触发（/skill-name [可选附加描述]）
        if user_input.startswith("/"):
            parts = user_input[1:].split(None, 1)
            skill_name = parts[0]
            selected_skill = self.skill_registry.get_manifest(skill_name)
            if selected_skill:
                print(f"\n\n Slash 命令触发技能：/{selected_skill['name']} — {selected_skill.get('description', '')}")
                user_input = parts[1] if len(parts) > 1 else selected_skill.get("description", skill_name)
            else:
                available = ', '.join('/' + s['name'] for s in self.skill_registry.list_manifests())
                print(f"\n\n 未找到技能 /{skill_name}，可用技能：{available}")
                return "未知 slash 命令"

        # 优先级2：关键词模糊匹配
        if not selected_skill:
            hinted_skill = match_skill(user_input, self.skill_registry.list_manifests())
            if hinted_skill:
                print(f"\n\n 匹配到相关技能：{hinted_skill['name']} — {hinted_skill.get('description', '')}")

        skill_hint = ""
        if selected_skill:
            skill_hint = (
                f"\n\n已显式选择技能：{selected_skill['name']}。"
                f"开始执行前请先调用 load_skill(\"{selected_skill['name']}\") 读取技能正文，"
                f"仅在需要时再用 read_file 读取该技能列出的附加资源。"
            )
        elif hinted_skill:
            skill_hint = (
                f"\n\n可能相关技能：{hinted_skill['name']}。"
                f"如果它确实适用于当前任务，请先调用 load_skill(\"{hinted_skill['name']}\") 再继续执行。"
            )

        task_for_execution = f"{user_input}{skill_hint}"
        if session_hook_message:
            task_for_execution = f"{task_for_execution}\n\n{session_hook_message}"
        steps = self.plan(task_for_execution)
        if not steps:
            print("\n\n 规划失败，降级为纯 ReAct 模式执行...")
            result = self._react_loop(task_for_execution, context=session_ctx)
            self.session_history.append({"task": original_task, "answer": result})
            return result

        print("\n\n 执行计划：")

        for i, step in enumerate(steps, 1):
            print(f"  Step {i}: {step}")
        confirm = input("\n\n是否按此计划执行？（Y/N，直接回车确认）").strip().lower()
        if confirm == 'n':
            print("\n\n计划已取消，切换为直接对话模式...")
            result = self._react_loop(task_for_execution, context=session_ctx)
            self.session_history.append({"task": original_task, "answer": result})
            return result

        # 执行阶段：依次执行每个步骤，传递上下文（携带会话历史）
        context = session_ctx
        for i, step in enumerate(steps, 1):
            print(f"\n\n{'='*50}")
            print(f"▶️  执行 Step {i}/{len(steps)}: {step}")
            print(f"{'='*50}")
            result = self.execute_step(step, context, task_for_execution)
            context += f"\n[Step {i} 结果] {result}"

        # 所有步骤完成后，让模型汇总最终答案
        print("\n\n 所有步骤执行完成，正在汇总...")
        summary_messages = [
            {"role": "system", "content": self.render_system_prompt(react_system_prompt_template)},
            {"role": "user", "content": f"<question>{user_input}</question>\n\n以下是各步骤的执行结果摘要，请基于此给出最终答案：\n{context}\n\n请直接输出 <final_answer>...</final_answer>"}
        ]
        final_content = self.dispatch_model(summary_messages)
        final_match = re.search(r"<final_answer>(.*?)</final_answer>", final_content, re.DOTALL)
        final_answer = final_match.group(1) if final_match else context
        self.session_history.append({"task": original_task, "answer": final_answer})
        return final_answer

    def plan(self, user_input: str) -> list:
        """调用一次 LLM 生成步骤列表，返回 step 字符串列表"""
        print("\n\n 正在规划任务步骤...")
        session_ctx = self._build_session_context()
        context_hint = f"\n\n{session_ctx}" if session_ctx else ""
        messages = [
            {"role": "system", "content": self.render_system_prompt(plan_system_prompt_template)},
            {"role": "user", "content": f"任务：{user_input}{context_hint}"}
        ]
        content = self.dispatch_model(messages)
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
            content = self.dispatch_model(messages)

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

            observation, should_stop = self._run_tool_with_hooks(tool_name, args, messages, cancel_message="步骤被用户取消")
            if should_stop:
                return observation

        return "步骤达到最大执行轮数"

    def _react_loop(self, user_input: str, context: str) -> str:
        """原始 ReAct 单循环，作为降级兜底"""
        context_hint = f"\n\n{context}" if context else ""
        system_msg = {"role": "system", "content": self.render_system_prompt(react_system_prompt_template)}
        messages = [
            system_msg,
            {"role": "user", "content": f"<question>{user_input}</question>{context_hint}"}
        ]

        max_rounds = 15
        for _ in range(max_rounds):
            self._compress_history(messages)
            content = self.dispatch_model(messages)

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

            observation, should_stop = self._run_tool_with_hooks(tool_name, args, messages, cancel_message="操作被用户取消")
            if should_stop:
                print("\n\n操作已取消。")
                return observation

        return "ReAct 循环达到最大执行轮数"

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

    def _append_observation(self, messages: list, observation: str):
        messages.append({"role": "user", "content": f"<observation>{observation}</observation>"})

    def _run_tool_with_hooks(self, tool_name: str, args: list, messages: list, available_tools: dict | None = None, cancel_message: str = "操作被用户取消") -> tuple[str, bool]:
        tool_map = available_tools or self.tools

        if tool_name not in tool_map:
            available = ', '.join(tool_map.keys())
            observation = f"工具 '{tool_name}' 不存在，可用工具：{available}"
            self._append_observation(messages, observation)
            return observation, False

        if tool_name in ("read_file", "write_to_file") and args:
            if not self._validate_path(str(args[0])):
                observation = f"路径 '{args[0]}' 不在项目目录内，只允许操作 {self.project_directory} 下的文件"
                print(f"\n\n Observation：{observation}")
                self._append_observation(messages, observation)
                return observation, False

        pre = self.hook_runner.run("PreToolUse", {
            "tool_name": tool_name,
            "input": {"args": args},
        })
        if pre["exit_code"] == 1:
            observation = pre["message"] or f"Hook 阻止了工具 {tool_name} 的执行"
            print(f"\n\n Observation：{observation}")
            self._append_observation(messages, observation)
            return observation, False
        if pre["exit_code"] == 2 and pre["message"]:
            self._append_observation(messages, pre["message"])

        print(f"\n\n Action: {tool_name}({', '.join(str(a) for a in args)})")
        should_continue = input("\n\n是否继续？（Y/N）") if tool_name == "run_terminal_command" else "y"
        if should_continue.lower() != 'y':
            return cancel_message, True

        try:
            observation = tool_map[tool_name](*args)
        except Exception as e:
            observation = f"工具执行错误：{str(e)}"

        post = self.hook_runner.run("PostToolUse", {
            "tool_name": tool_name,
            "input": {"args": args},
            "output": observation,
        })
        if post["exit_code"] == 1:
            observation = post["message"] or observation
        elif post["exit_code"] == 2 and post["message"]:
            observation = f"{observation}\n\n{post['message']}"

        print(f"\n\n Observation：{observation}")
        self._append_observation(messages, observation)
        return observation, False

    def task(self, prompt: str) -> str:
        """在独立上下文中执行子任务，返回结果摘要（不污染父上下文）"""
        print(f"\n\n🔹 派生子智能体：{prompt[:80]}{'...' if len(prompt) > 80 else ''}")
        subagent = SubagentContext(prompt=prompt, tools=self.subagent_tools, agent=self)
        result = subagent.run()
        print(f"\n\n🔹 子智能体返回：{result[:100]}{'...' if len(result) > 100 else ''}")
        return result

    def load_skill(self, name: str) -> str:
        """按需加载技能正文，并仅披露附加资源目录"""
        print(f"\n\n📚 加载技能：{name}")
        return self.skill_registry.load_skill(name)

    def get_skill_list(self) -> str:
        """返回轻量技能目录，供系统提示词常驻展示"""
        return self.skill_registry.describe_available()

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
        skill_list = self.get_skill_list()
        file_list = ", ".join(
            os.path.abspath(os.path.join(self.project_directory, f))
            for f in os.listdir(self.project_directory)
        )
        # 把一段带占位符的模板字符串，填上真实数据
        return Template(system_prompt_template).substitute(
            operating_system=self.get_operating_system_name(),
            tool_list=tool_list,
            skill_list=skill_list,
            file_list=file_list
        )
        
    def get_api_key(self) -> str:
        """Load the API key from an environment variable."""
        load_dotenv()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("未找到 GOOGLE_API_KEY 环境变量，请在 .env 文件中设置。")
        return api_key
    
    def dispatch_model(self, messages):
        """根据 self.model 路由到对应的模型调用函数"""
        if self.model.startswith("gemini"):
            return self.call_model(messages)
        else:
            return self.call_native_model(messages)

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
        print("\n\n🤖 ", end="", flush=True)
        
        # 拆分系统提示词（Gemini要求单独传，不能放在对话历史里）
        system_prompt = ""
        chat_history = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                # 转换为Gemini支持的消息格式（Gemini 要求 assistant 角色用 "model"）
                gemini_role = "model" if msg["role"] == "assistant" else msg["role"]
                chat_history.append({
                    "role": gemini_role,
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
        messages.append({"role": "assistant", "content": content})
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
        
    def _validate_path(self, file_path: str) -> bool:
        """校验文件路径是否在项目目录内，防止越权访问"""
        abs_path = os.path.abspath(file_path)
        return abs_path.startswith(self.project_directory)

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
@click.option('--model', default='gemini-2.5-flash',
              show_default=True,
              help='模型名称。gemini-* 使用 Google API；其他值（如 qwen2.5:3b）通过 Ollama 本地运行')
def main(project_directory, model):
    project_dir = os.path.abspath(project_directory)

    tools = [read_file, write_to_file, run_terminal_command, list_directory, search_in_files, web_search, query_knowledge_base]
    agent = ReActAgent(tools=tools, model=model, project_directory=project_dir)

    backend = "Google Gemini API" if model.startswith("gemini") else f"Ollama 本地 ({model})"
    print("\n🤖 Agent 已启动，输入 'exit' 或 'quit' 退出对话")
    print(f"🧠 模型：{model}  ({backend})")
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