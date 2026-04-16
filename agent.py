# 1. python 内置标准库
import ast                                # 解析Python源代码为抽象语法树，用于分析/修改代码结构
import inspect                            # 检查函数、类、模块的信息（比如获取函数源码、参数）
import os                                 # 操作系统交互：读取环境变量、文件路径、创建文件夹等
import re                                 # 正则表达式：文本查找、替换、匹配（比如提取关键词、过滤内容）
from string import Template               # 字符串模板：方便批量替换文本中的变量
import sys
from typing import Any, List, Callable, Tuple  # 类型注解：标注变量/函数类型，让代码更易读、防错

# 2. 第三方库 (需要用pip安装才能使用)
import click                              # 命令行工具：快速创建可在终端运行的命令、参数、选项
from dotenv import load_dotenv            # 加载.env文件：把私密配置（密钥、账号）存在文件里，不写死在代码
import httpx                              # HTTP客户端：支持代理配置，用于绕过地区限制
import platform                           # 获取系统信息：判断是Windows、Mac还是Linux
from google import genai

# 3. 自定义模块 (项目中自己写的)
from internal_mcp.client import MCPClientManager
from internal_mcp.config import load_mcp_server_configs
from internal_mcp.registry import MCPToolRegistry
from prompt_template import react_system_prompt_template, plan_system_prompt_template, subagent_system_prompt_template, direct_answer_system_prompt_template

from memory import MemoryStore
from team import TeammateManager
from hooks import HookRunner, build_default_hook_runner
from tools import read_file, write_to_file, run_terminal_command, list_directory, search_in_files, web_search, query_knowledge_base
from skills import get_skill_registry, match_skill

class SubagentContext:
    """子智能体上下文：独立消息列表 + 限制工具集 + 最大轮数保护"""
    def __init__(self, prompt: str, tools: dict, agent: 'ReActAgent', max_turns: int = 8):
        self.messages = [
            {"role": "system", "content": agent.render_system_prompt(subagent_system_prompt_template, tool_map=tools)},
            {"role": "user", "content": f"<question>{prompt}</question>"}
        ]
        self.tools = tools          # 子智能体可用工具（不含 task，防递归）
        self.agent = agent          # 引用父智能体，用于调用 dispatch_model 等
        self.max_turns = max_turns
        self.tool_failures: dict[str, int] = {}

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
                tool_name, args, kwargs = self.agent.parse_action(action)
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
                kwargs,
                self.messages,
                available_tools=self.tools,
                cancel_message="子任务被取消",
            )
            if should_stop:
                return observation
            recovery_result = self.agent._recover_from_tool_failure(tool_name, observation, self.tool_failures, self.messages)
            if recovery_result is not None:
                return recovery_result

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
        self.memory_store = MemoryStore(os.path.join(self.project_directory, ".memory"))
        self.current_memory_section = self.memory_store.build_memory_section()
        self.team_manager = TeammateManager(os.path.join(self.project_directory, ".team"), self)
        self.mcp_client_manager = MCPClientManager()
        self.mcp_registry = MCPToolRegistry(self.mcp_client_manager)
        self.mcp_server_configs = load_mcp_server_configs(self.project_directory)
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
        self.tools["save_memory"] = self.save_memory
        self.tools["spawn_teammate"] = self.spawn_teammate
        self.tools["list_teammates"] = self.list_teammates
        self.tools["send_message"] = self.send_message
        self.tools["broadcast_message"] = self.broadcast_message
        self.tools["read_team_inbox"] = self.read_team_inbox
        self.tools["get_status"] = self.get_status
        self.tools["request_shutdown"] = self.request_shutdown
        self.tools["review_plan"] = self.review_plan
        self.tools.update(self._load_mcp_tools())
        excluded_subagent_tools = {
            "task",
            "spawn_teammate",
            "list_teammates",
            "send_message",
            "broadcast_message",
            "read_team_inbox",
            "request_shutdown",
            "review_plan",
        }
        self.subagent_tools = {
            name: func
            for name, func in self.tools.items()
            if name not in excluded_subagent_tools and not self._is_mcp_tool(name)
        }
        # 将 task 方法注册为工具，让父智能体可以调用子智能体
        self.tools["task"] = self.task
        self.session_history: list[dict] = []  # 会话级记忆：记录本次启动中的所有任务和答案

    MAX_HISTORY_MESSAGES = 20  # 超过此数量时触发历史压缩（不含 system 消息）
    MAX_SESSION_HISTORY = 5  # 注入上下文时最多使用最近 N 条历史
    TOOL_FAILURE_PREFIXES = ("工具执行错误：", "搜索失败：", "知识库查询失败：")
    ALWAYS_HIDDEN_PROMPT_TOOLS = {"save_memory"}
    TEAM_PROMPT_TOOLS = {
        "task",
        "spawn_teammate",
        "list_teammates",
        "send_message",
        "broadcast_message",
        "read_team_inbox",
        "request_shutdown",
        "review_plan",
    }
    GENERAL_QUESTION_TOOLS = {"web_search", "query_knowledge_base"}

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

    def _build_selected_skill_context(self, selected_skill: dict | None) -> str:
        if not selected_skill:
            return ""
        skill_name = selected_skill["name"]
        skill_body = self.skill_registry.load_skill(skill_name)
        return (
            f"\n\n已显式选择技能：{skill_name}。"
            "该技能正文已由系统预加载，请直接遵循其中的步骤、脚本路径和动作示例执行。"
            "注意：技能名不是工具名，不要直接调用 skill 名称或自行发明同名工具；"
            "如果需要执行命令，请严格使用技能正文中给出的现有工具调用格式。"
            f"\n\n{skill_body}"
        )

    def run(self, user_input: str):
        original_task = user_input
        selected_skill = None
        hinted_skill = None
        session_hook_message = ""
        self.current_memory_section = self._load_memory_section(original_task)

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

        special_command_result = self._handle_special_command(user_input)
        if special_command_result is not None:
            return special_command_result

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
        selected_skill_context = self._build_selected_skill_context(selected_skill)
        if selected_skill:
            skill_hint = (
                f"\n\n已显式选择技能：{selected_skill['name']}。"
                "技能正文已经提供在当前任务上下文中，请优先严格遵循其中的说明。"
            )
        elif hinted_skill:
            skill_hint = (
                f"\n\n可能相关技能：{hinted_skill['name']}。"
                f"如果它确实适用于当前任务，请先调用 load_skill(\"{hinted_skill['name']}\") 再继续执行。"
            )

        task_for_execution = f"{user_input}{skill_hint}{selected_skill_context}"
        if session_hook_message:
            task_for_execution = f"{task_for_execution}\n\n{session_hook_message}"
        prompt_tool_map = self._build_prompt_tool_map(task_for_execution, selected_skill, hinted_skill)
        if selected_skill:
            print("\n\n 已显式选择技能，跳过通用任务规划，按技能正文直接执行...")
            result = self._react_loop(task_for_execution, context=session_ctx, tool_map=prompt_tool_map)
            self.session_history.append({"task": original_task, "answer": result})
            return result
        if self._should_skip_planning(task_for_execution):
            print("\n\n 识别为通用问答，跳过任务规划，优先直接回答...")
            result = self._direct_answer(task_for_execution)
            self.session_history.append({"task": original_task, "answer": result})
            return result

        steps = self.plan(task_for_execution, tool_map=prompt_tool_map)
        if not steps:
            print("\n\n 规划失败，降级为纯 ReAct 模式执行...")
            result = self._react_loop(task_for_execution, context=session_ctx, tool_map=prompt_tool_map)
            self.session_history.append({"task": original_task, "answer": result})
            return result

        print("\n\n 执行计划：")

        for i, step in enumerate(steps, 1):
            print(f"  Step {i}: {step}")
        confirm = input("\n\n是否按此计划执行？（Y/N，直接回车确认）").strip().lower()
        if confirm == 'n':
            print("\n\n计划已取消，切换为直接对话模式...")
            result = self._react_loop(task_for_execution, context=session_ctx, tool_map=prompt_tool_map)
            self.session_history.append({"task": original_task, "answer": result})
            return result

        # 执行阶段：依次执行每个步骤，传递上下文（携带会话历史）
        context = session_ctx
        step_results: list[tuple[str, str]] = []
        for i, step in enumerate(steps, 1):
            print(f"\n\n{'='*50}")
            print(f"▶️  执行 Step {i}/{len(steps)}: {step}")
            print(f"{'='*50}")
            result = self.execute_step(step, context, task_for_execution, tool_map=prompt_tool_map)
            step_results.append((step, result))
            context += f"\n[Step {i} 结果] {result}"

        failed_steps = [(step, result) for step, result in step_results if self._is_step_result_failure(result)]
        if failed_steps:
            failure_lines = ["任务未成功完成。以下步骤执行失败："]
            for step, result in failed_steps:
                failure_lines.append(f"- 失败步骤：{step}")
                failure_lines.append(f"  返回结果：{result}")
            final_answer = "\n".join(failure_lines)
            self.session_history.append({"task": original_task, "answer": final_answer})
            return final_answer

        print("\n\n 所有步骤执行完成，正在汇总...")
        summary_messages = [
            {"role": "system", "content": self.render_system_prompt(direct_answer_system_prompt_template)},
            {"role": "user", "content": f"<question>{user_input}</question>\n\n以下是各步骤的真实执行结果，请仅基于这些结果给出最终结论，不要声称任何未明确出现的成功：\n{context}\n\n请只输出 <thought>...</thought> 和 <final_answer>...</final_answer>"}
        ]
        final_content = self.dispatch_model(summary_messages)
        final_match = re.search(r"<final_answer>(.*?)</final_answer>", final_content, re.DOTALL)
        final_answer = final_match.group(1).strip() if final_match else context
        self.session_history.append({"task": original_task, "answer": final_answer})
        return final_answer

    def plan(self, user_input: str, tool_map: dict[str, Callable] | None = None) -> list:
        print("\n\n 正在规划任务...")
        session_ctx = self._build_session_context()
        context_hint = f"\n\n{session_ctx}" if session_ctx else ""
        messages = [
            {"role": "system", "content": self.render_system_prompt(plan_system_prompt_template, tool_map=tool_map)},
            {"role": "user", "content": f"任务：{user_input}{context_hint}"}
        ]
        content = self.dispatch_model(messages)
        steps = re.findall(r"<step>(.*?)</step>", content, re.DOTALL)
        return [s.strip() for s in steps if s.strip()]

    def execute_step(self, step: str, context: str, original_task: str, tool_map: dict[str, Callable] | None = None) -> str:
        context_hint = f"\n\n以下是前面步骤的执行结果，可作为当前步骤的上下文：\n{context}" if context else ""
        available_tools = tool_map or self.tools
        system_msg = {"role": "system", "content": self.render_system_prompt(react_system_prompt_template, tool_map=available_tools)}
        messages = [
            system_msg,
            {"role": "user", "content": f"<question>原始任务：{original_task}\n\n当前步骤：{step}{context_hint}</question>"}
        ]
        max_rounds = 10
        tool_failures: dict[str, int] = {}
        for _ in range(max_rounds):
            self._compress_history(messages)
            content = self.dispatch_model(messages)

            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                print(f"\n\n🧠 Thought: {thought_match.group(1).strip()}")

            if "<final_answer>" in content:
                final_match = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                return final_match.group(1).strip() if final_match else "步骤完成"

            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                messages.append({"role": "user", "content": "<observation>格式错误：你必须输出 <action>...</action> 标签，请重新按格式输出。</observation>"})
                continue

            action = action_match.group(1).strip()
            try:
                tool_name, args, kwargs = self.parse_action(action)
            except Exception as e:
                messages.append({"role": "user", "content": f"<observation>Action 格式解析失败：{e}。请确保格式为 tool_name(\"arg1\", \"arg2\") 或 tool_name(name=\"value\")。</observation>"})
                continue

            observation, should_stop = self._run_tool_with_hooks(tool_name, args, kwargs, messages, available_tools=available_tools, cancel_message="步骤被用户取消")
            if should_stop:
                return observation
            recovery_result = self._recover_from_tool_failure(tool_name, observation, tool_failures, messages)
            if recovery_result is not None:
                return recovery_result

        return "步骤达到最大执行轮数"

    def _react_loop(self, user_input: str, context: str, tool_map: dict[str, Callable] | None = None) -> str:
        context_hint = f"\n\n{context}" if context else ""
        available_tools = tool_map or self.tools
        system_msg = {"role": "system", "content": self.render_system_prompt(react_system_prompt_template, tool_map=available_tools)}
        messages = [
            system_msg,
            {"role": "user", "content": f"<question>{user_input}</question>{context_hint}"}
        ]

        max_rounds = 15
        tool_failures: dict[str, int] = {}
        for _ in range(max_rounds):
            self._compress_history(messages)
            content = self.dispatch_model(messages)

            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                print(f"\n\n🧠 Thought: {thought_match.group(1).strip()}")

            if "<final_answer>" in content:
                final_match = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                return final_match.group(1).strip() if final_match else "任务完成"

            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                print("\n\n格式错误：缺少 <action> 标签，要求模型重试...")
                messages.append({"role": "user", "content": "<observation>格式错误：你必须输出 <action>...</action> 标签，请重新按格式输出。</observation>"})
                continue

            action = action_match.group(1).strip()
            try:
                tool_name, args, kwargs = self.parse_action(action)
            except Exception as e:
                print(f"\n\n⚠️ Action 格式解析失败：{e}，反馈重试...")
                messages.append({"role": "user", "content": f"<observation>Action 格式解析失败：{e}。请确保格式为 tool_name(\"arg1\", \"arg2\") 或 tool_name(name=\"value\")。</observation>"})
                continue

            observation, should_stop = self._run_tool_with_hooks(tool_name, args, kwargs, messages, available_tools=available_tools, cancel_message="操作被用户取消")
            if should_stop:
                print("\n\n操作已取消。")
                return observation
            recovery_result = self._recover_from_tool_failure(tool_name, observation, tool_failures, messages)
            if recovery_result is not None:
                return recovery_result

        return "ReAct 循环达到最大执行轮数"

    def _compress_history(self, messages: list):
        non_system = [m for m in messages if m["role"] != "system"]
        if len(non_system) <= self.MAX_HISTORY_MESSAGES:
            return
        system_msgs = [m for m in messages if m["role"] == "system"]
        first_user = next((m for m in messages if m["role"] == "user"), None)
        recent = non_system[-(self.MAX_HISTORY_MESSAGES // 2):]
        kept = system_msgs + ([first_user] if first_user and first_user not in recent else []) + recent
        messages[:] = kept
        print(f"\n\n🗜️ 历史已压缩，保留 {len(messages)} 条消息")

    def _append_observation(self, messages: list, observation: str):
        messages.append({"role": "user", "content": f"<observation>{observation}</observation>"})

    def _run_tool_with_hooks(self, tool_name: str, args: list, kwargs: dict[str, Any] | None, messages: list, available_tools: dict | None = None, cancel_message: str = "操作被用户取消") -> tuple[str, bool]:
        tool_map = available_tools or self.tools
        kwargs = kwargs or {}

        if tool_name not in tool_map:
            available = ', '.join(tool_map.keys())
            observation = (
                f"工具 '{tool_name}' 不存在，可用工具：{available}。"
                "注意：技能名不是工具名；如果当前任务依赖某个技能，请遵循已加载的技能正文，"
                "或调用 load_skill(\"skill-name\") 读取技能说明后，再使用现有工具完成任务。"
            )
            self._append_observation(messages, observation)
            return observation, False

        file_path = self._extract_path_argument(tool_name, args, kwargs)
        if file_path is not None and tool_name in ("read_file", "write_to_file"):
            if not self._validate_path(str(file_path)):
                observation = f"路径 '{file_path}' 不在项目目录内，只允许操作 {self.project_directory} 下的文件"
                print(f"\n\n Observation：{observation}")
                self._append_observation(messages, observation)
                return observation, False

        pre = self.hook_runner.run("PreToolUse", {
            "tool_name": tool_name,
            "input": {"args": args, "kwargs": kwargs},
        })
        if pre["exit_code"] == 1:
            observation = pre["message"] or f"Hook 阻止了工具 {tool_name} 的执行"
            print(f"\n\n Observation：{observation}")
            self._append_observation(messages, observation)
            return observation, False
        if pre["exit_code"] == 2 and pre["message"]:
            self._append_observation(messages, pre["message"])

        print(f"\n\n Action: {self._format_action_call(tool_name, args, kwargs)}")
        should_continue = input("\n\n是否继续？（Y/N）") if tool_name == "run_terminal_command" else "y"
        if should_continue.lower() != 'y':
            return cancel_message, True

        try:
            observation = tool_map[tool_name](*args, **kwargs)
        except Exception as e:
            observation = f"工具执行错误：{str(e)}"

        post = self.hook_runner.run("PostToolUse", {
            "tool_name": tool_name,
            "input": {"args": args, "kwargs": kwargs},
            "output": observation,
        })
        if post["exit_code"] == 1:
            observation = post["message"] or observation
        elif post["exit_code"] == 2 and post["message"]:
            observation = f"{observation}\n\n{post['message']}"

        print(f"\n\n Observation：{observation}")
        self._append_observation(messages, observation)
        return observation, False

    def _is_tool_failure(self, observation: str) -> bool:
        if observation.startswith(self.TOOL_FAILURE_PREFIXES):
            return True
        return "不在项目目录内" in observation or observation.startswith("工具 '")

    def _is_step_result_failure(self, result: str) -> bool:
        failure_markers = (
            "步骤达到最大执行轮数",
            "ReAct 循环达到最大执行轮数",
            "工具执行错误：",
            "工具 '",
            "不在项目目录内",
            "操作被用户取消",
            "步骤被用户取消",
            "子任务被取消",
        )
        return any(marker in result for marker in failure_markers)

    def _recover_from_tool_failure(self, tool_name: str, observation: str, failure_counts: dict[str, int], messages: list) -> str | None:
        if not self._is_tool_failure(observation):
            return None
        failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1
        if failure_counts[tool_name] < 2:
            return None
        recovery_observation = (
            f"工具 '{tool_name}' 已连续失败 {failure_counts[tool_name]} 次。"
            f"最后一次错误：{observation}。"
            "不要继续重试同一个工具或同类失败工具；如果已有信息足够，请直接输出 <final_answer>；否则明确说明缺失信息和下一步建议。"
        )
        self._append_observation(messages, recovery_observation)
        content = self.dispatch_model(messages)
        final_match = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
        if final_match:
            return final_match.group(1).strip()
        return recovery_observation

    def task(self, prompt: str) -> str:
        print(f"\n\n🔹 派生子智能体：{prompt[:80]}{'...' if len(prompt) > 80 else ''}")
        subagent = SubagentContext(prompt=prompt, tools=self.subagent_tools, agent=self)
        result = subagent.run()
        print(f"\n\n🔹 子智能体返回：{result[:100]}{'...' if len(result) > 100 else ''}")
        return result

    def load_skill(self, name: str) -> str:
        print(f"\n\n📚 加载技能：{name}")
        return self.skill_registry.load_skill(name)

    def save_memory(self, name: str, description: str, mem_type: str, content: str) -> str:
        print(f"\n\n🧠 保存长期记忆：{name} [{mem_type}]")
        result = self.memory_store.save_memory(name, description, mem_type, content)
        self.current_memory_section = self.memory_store.build_memory_section()
        return result

    def _is_general_question(self, user_input: str) -> bool:
        lowered = user_input.lower()
        project_markers = (
            "代码", "文件", "目录", "项目", "仓库", "函数", "类", "模块", "bug", "报错",
            "测试", "运行", "实现", "修改", "重构", "调试", "read_file", "write_to_file",
            ".py", "agent.py", "tools.py", "搜索项目", "查看仓库",
        )
        general_markers = (
            "什么", "如何", "为什么", "建议", "学习", "知识", "路线", "介绍", "区别",
            "原理", "概念", "怎么", "需要补充", "我想转", "适合", "总结",
        )
        return any(marker in user_input or marker in lowered for marker in general_markers) and not any(marker in user_input or marker in lowered for marker in project_markers)

    def _should_skip_planning(self, user_input: str) -> bool:
        return self._is_general_question(user_input)

    def _direct_answer(self, user_input: str) -> str:
        messages = [
            {"role": "system", "content": self.render_system_prompt(direct_answer_system_prompt_template)},
            {"role": "user", "content": f"<question>{user_input}</question>"},
        ]
        content = self.dispatch_model(messages)
        final_match = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
        if final_match:
            return final_match.group(1).strip()
        return content.strip()

    def _is_multi_agent_request(self, user_input: str) -> bool:
        lowered = user_input.lower()
        markers = ("multi-agent", "multi agent", "多 agent", "多智能体", "队友", "researcher", "coder", "tester")
        return any(marker in lowered or marker in user_input for marker in markers)

    def _build_prompt_tool_map(self, user_input: str, selected_skill: dict | None = None, hinted_skill: dict | None = None) -> dict[str, Callable]:
        if self._is_general_question(user_input):
            general_tools = {
                name: func
                for name, func in self.tools.items()
                if name in self.GENERAL_QUESTION_TOOLS
            }
            return general_tools or dict(self.tools)

        hidden_tools = set(self.ALWAYS_HIDDEN_PROMPT_TOOLS)
        if not self._is_multi_agent_request(user_input):
            hidden_tools.update(self.TEAM_PROMPT_TOOLS)
        if not selected_skill and not hinted_skill:
            hidden_tools.add("load_skill")

        return {
            name: func
            for name, func in self.tools.items()
            if name not in hidden_tools
        }

    def _extract_path_argument(self, tool_name: str, args: list, kwargs: dict[str, Any]) -> str | None:
        if tool_name not in ("read_file", "write_to_file"):
            return None
        if "file_path" in kwargs:
            return str(kwargs["file_path"])
        if args:
            return str(args[0])
        return None

    def _format_action_call(self, tool_name: str, args: list, kwargs: dict[str, Any]) -> str:
        parts = [repr(arg) for arg in args]
        parts.extend(f"{key}={repr(value)}" for key, value in kwargs.items())
        return f"{tool_name}({', '.join(parts)})"

    def spawn_teammate(self, name: str, role: str, prompt: str) -> str:
        return self.team_manager.spawn(name, role, prompt)

    def list_teammates(self) -> str:
        return self.team_manager.list_teammates()

    def send_message(self, teammate: str, content: str, msg_type: str = "message") -> str:
        return self.team_manager.send_message("lead", teammate, content, msg_type)

    def broadcast_message(self, content: str, msg_type: str = "message") -> str:
        return self.team_manager.broadcast_message("lead", content, msg_type)

    def read_team_inbox(self, name: str = "lead") -> str:
        return self.team_manager.read_inbox(name)

    def _load_mcp_tools(self) -> dict[str, Callable]:
        tool_specs = self.mcp_client_manager.load_servers(self.mcp_server_configs)
        return self.mcp_registry.load_tools(tool_specs)

    def _is_mcp_tool(self, tool_name: str) -> bool:
        return self.mcp_registry.is_mcp_tool(tool_name)

    def _get_mcp_status_lines(self) -> list[str]:
        states = self.mcp_client_manager.get_server_states()
        connected = sum(1 for state in states if state.connected)
        lines = [
            "# MCP Status",
            f"- 已配置 server：{len(self.mcp_server_configs)}",
            f"- 已连接 server：{connected}",
            f"- 已加载 MCP tools：{len(self.mcp_registry.get_tool_specs())}",
        ]
        for state in states:
            status = "connected" if state.connected else "disconnected"
            detail = f", last_error={state.last_error}" if state.last_error else ""
            lines.append(f"- server {state.name}: {status}, tools={state.tool_count}{detail}")
        return lines

    def get_status(self) -> str:
        members = self.team_manager.config.get("members", [])
        active = [member for member in members if member["status"] == "working"]
        idle = [member for member in members if member["status"] == "idle"]
        shutdown = [member for member in members if member["status"] == "shutdown"]
        pending_shutdown = sum(1 for request in self.team_manager.shutdown_requests.values() if request["status"] == "pending")
        pending_plan = sum(1 for request in self.team_manager.plan_requests.values() if request["status"] == "pending")
        backend = "Google Gemini API" if self.model.startswith("gemini") else f"Ollama 本地 ({self.model})"
        lines = [
            "# Agent Status",
            f"- 模型后端：{backend}",
            "- Multi-agent 支持：已启用",
            f"- 队友总数：{len(members)}",
            f"- working：{len(active)}",
            f"- idle：{len(idle)}",
            f"- shutdown：{len(shutdown)}",
            f"- 待处理 shutdown 请求：{pending_shutdown}",
            f"- 待审批计划：{pending_plan}",
            "- 可用团队命令：/status, /team, /inbox [name]",
        ]
        lines.extend(["", *self._get_mcp_status_lines()])
        return "\n".join(lines)

    def get_multi_agent_usage_guide(self) -> str:
        return (
            "\n🤝 Multi-agent 使用说明\n"
            "- 当前版本已支持 multi-agent；只有创建队友后，才会进入实际团队协作。\n"
            "- 想触发 multi-agent，可直接说：请用多 agent 模式处理这个任务，并创建 researcher/coder/tester 队友。\n"
            "- 查看当前状态：/status\n"
            "- 查看团队成员：/team\n"
            "- 查看收件箱：/inbox 或 /inbox alice\n"
            "- 如需接入外部 MCP server，请在项目目录下准备 .mcp/config.json\n"
        )

    def request_shutdown(self, teammate: str) -> str:
        return self.team_manager.request_shutdown(teammate)

    def review_plan(self, request_id: str, approve: bool, feedback: str = "") -> str:
        return self.team_manager.review_plan(request_id, approve, feedback)

    def _handle_special_command(self, user_input: str) -> str | None:
        if not user_input.startswith("/"):
            return None
        parts = user_input.split(None, 1)
        command = parts[0].lower()
        if command == "/status":
            return self.get_status()
        if command == "/team":
            return self.list_teammates()
        if command == "/inbox":
            target = parts[1].strip() if len(parts) > 1 else "lead"
            return self.read_team_inbox(target)

        return None

    def _make_bound_tool(self, name: str, doc: str, func: Callable) -> Callable:
        func.__name__ = name
        func.__doc__ = doc
        return func

    def build_teammate_tools(self, teammate_name: str) -> dict[str, Callable]:
        excluded = {
            "task",
            "spawn_teammate",
            "list_teammates",
            "broadcast_message",
            "read_team_inbox",
            "request_shutdown",
            "review_plan",
            "send_message",
        }
        tools = {name: func for name, func in self.tools.items() if name not in excluded and not self._is_mcp_tool(name)}
        tools["send_message"] = self._make_bound_tool(
            "send_message",
            "向领导或其他队友发送消息。",
            lambda to, content, msg_type="message": self.team_manager.send_message(teammate_name, to, content, msg_type),
        )
        tools["respond_shutdown"] = self._make_bound_tool(
            "respond_shutdown",
            "响应领导发来的 shutdown request。",
            lambda request_id, approve, reason="": self.team_manager.respond_shutdown(teammate_name, request_id, approve, reason),
        )
        tools["submit_plan"] = self._make_bound_tool(
            "submit_plan",
            "向领导提交计划审批请求。",
            lambda plan: self.team_manager.submit_plan(teammate_name, plan),
        )
        return tools

    def _should_ignore_memory(self, user_input: str) -> bool:
        lowered = user_input.lower()
        return (
            "ignore memory" in lowered
            or "忽略 memory" in user_input
            or "忽略之前的记忆" in user_input
            or "不要参考 memory" in user_input
            or "忽略之前的memory" in lowered
        )

    def _load_memory_section(self, user_input: str) -> str:
        if self._should_ignore_memory(user_input):
            return "- 暂无可用长期记忆"
        return self.memory_store.build_memory_section()

    def get_skill_list(self) -> str:
        """返回轻量技能目录，供系统提示词常驻展示"""
        return self.skill_registry.describe_available()

    # 给 AI 生成工具使用说明书
    def get_tool_list(self, tool_map: dict | None = None) -> str:
        """生成工具列表字符串，包含函数签名和简要说明"""
        tool_descriptions = []
        tools = tool_map or self.tools
        for func in tools.values():
            name = func.__name__
            signature = str(inspect.signature(func))
            doc = inspect.getdoc(func)
            tool_descriptions.append(f"- {name}{signature}: {doc}")
        return "\n".join(tool_descriptions)
    
    # 返回一段完整 Ready-to-use 的 AI 系统提示词
    def render_system_prompt(self, system_prompt_template: str, tool_map: dict | None = None, extra_vars: dict | None = None) -> str:
        """渲染系统提示模板，替换变量"""
        """
        os.listdir(self.project_directory)  列出项目文件夹里所有文件名
        os.path.join(路径, 文件名)
        转成绝对路径(完整路径，如 /user/project/main.py)
        """
        tool_list = self.get_tool_list(tool_map)
        skill_list = self.get_skill_list()
        file_list = ", ".join(
            os.path.abspath(os.path.join(self.project_directory, f))
            for f in os.listdir(self.project_directory)
        )
        # 把一段带占位符的模板字符串，填上真实数据
        variables = dict(
            operating_system=self.get_operating_system_name(),
            tool_list=tool_list,
            skill_list=skill_list,
            memory_section=getattr(self, "current_memory_section", "- 暂无可用长期记忆"),
            file_list=file_list
        )
        if extra_vars:
            variables.update(extra_vars)
        return Template(system_prompt_template).substitute(variables)
        
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
    
    def parse_action(self, code_str: str) -> Tuple[str, list[Any], dict[str, Any]]:
        try:
            expression = ast.parse(code_str, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"Invalid function call syntax: {exc.msg}") from exc

        call = expression.body
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
            raise ValueError("Action 必须是函数调用，例如 tool_name(\"arg\")")

        func_name = call.func.id
        args = [self._parse_action_value(code_str, arg) for arg in call.args]
        kwargs: dict[str, Any] = {}
        for keyword in call.keywords:
            if keyword.arg is None:
                raise ValueError("暂不支持 **kwargs 展开")
            kwargs[keyword.arg] = self._parse_action_value(code_str, keyword.value)

        return func_name, args, kwargs

    def _parse_action_value(self, source: str, node: ast.AST):
        try:
            return ast.literal_eval(node)
        except (ValueError, SyntaxError):
            raw = ast.get_source_segment(source, node)
            if raw is None:
                raise ValueError("参数必须是可解析的字面量")
            return self._parse_single_arg(raw)
    
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
    if os.name == "nt":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    tools = [read_file, write_to_file, run_terminal_command, list_directory, search_in_files, web_search, query_knowledge_base]
    agent = ReActAgent(tools=tools, model=model, project_directory=project_dir)

    backend = "Google Gemini API" if model.startswith("gemini") else f"Ollama 本地 ({model})"
    print("\n🤖 Agent 已启动，输入 'exit' 或 'quit' 退出对话")
    print(f"🧠 模型：{model}  ({backend})")
    print(f"📁 工作目录：{project_dir}")
    print(agent.get_multi_agent_usage_guide())
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