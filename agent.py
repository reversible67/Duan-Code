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
from prompt_template import react_system_prompt_template
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
    
    def run(self, user_input: str):
        messages = [
            {"role": "system", "content": self.render_system_prompt(react_system_prompt_template)},
            {"role": "user", "content": f"<question>{user_input}</question>"}
        ]

        while True:

            # 请求模型
            content = self.call_model(messages)

            # 检测 Thought
            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                thought = thought_match.group(1)
                print(f"\n\n💭 Thought: {thought}")

            # 检测模型是否输出 Final Answer，如果是的话，直接返回
            if "<final_answer>" in content:
                final_answer = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                return final_answer.group(1)

            # 检测 Action
            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                raise RuntimeError("模型未输出 <action>")
            action = action_match.group(1)
            tool_name, args = self.parse_action(action)

            print(f"\n\n🔧 Action: {tool_name}({', '.join(args)})")
            # 只有终端命令才需要询问用户，其他的工具直接执行
            should_continue = input(f"\n\n是否继续？（Y/N）") if tool_name == "run_terminal_command" else "y"
            if should_continue.lower() != 'y':
                print("\n\n操作已取消。")
                return "操作被用户取消"

            try:
                # 函数执行逻辑
                observation = self.tools[tool_name](*args)
            except Exception as e:
                observation = f"工具执行错误：{str(e)}"
            print(f"\n\n🔍 Observation：{observation}")
            obs_msg = f"<observation>{observation}</observation>"
            # 把历史对话 + 执行结果全部给 AI 继续推理 继续干活
            messages.append({"role": "user", "content": obs_msg})
    
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
        
        # 调用Gemini新版API（和官网完全一致）
        response = self.client.models.generate_content(
            model=self.model,
            contents=chat_history,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7
            )
        )

        content = response.text if response.text else "模型未返回有效内容，请重试"
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

    task = input("请输入任务：")

    final_answer = agent.run(task)

    print(f"\n\n✅ Final Answer：{final_answer}")

if __name__ == "__main__":
    main()