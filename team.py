import json
import os
import threading
import time
import uuid

from prompt_template import teammate_system_prompt_template


class MessageBus:
    def __init__(self, team_dir: str):
        self.team_dir = team_dir
        self.inbox_dir = os.path.join(team_dir, "inbox")
        os.makedirs(self.inbox_dir, exist_ok=True)

    def _inbox_path(self, name: str) -> str:
        return os.path.join(self.inbox_dir, f"{name}.jsonl")

    def send(self, sender: str, to: str, content: str, msg_type: str = "message", extra: dict | None = None) -> dict:
        message = {
            "type": msg_type,
            "from": sender,
            "to": to,
            "content": content,
            "timestamp": time.time(),
        }
        if extra:
            message.update(extra)
        with open(self._inbox_path(to), "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")
        return message

    def read_inbox(self, name: str) -> list[dict]:
        path = self._inbox_path(name)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        messages = [json.loads(line) for line in lines]
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        return messages


class TeammateManager:
    def __init__(self, team_dir: str, agent, idle_sleep: float = 0.3, max_turns_per_activation: int = 12):
        self.team_dir = team_dir
        self.agent = agent
        self.idle_sleep = idle_sleep
        self.max_turns_per_activation = max_turns_per_activation
        os.makedirs(self.team_dir, exist_ok=True)
        self.config_path = os.path.join(self.team_dir, "config.json")
        self.bus = MessageBus(self.team_dir)
        self.config = self._load_config()
        self.threads: dict[str, threading.Thread] = {}
        self.shutdown_requests: dict[str, dict] = {}
        self.plan_requests: dict[str, dict] = {}
        self.shutdown_flags: dict[str, bool] = {}

    def _load_config(self) -> dict:
        if not os.path.exists(self.config_path):
            config = {"members": []}
            self._write_config(config)
            return config
        with open(self.config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_config(self, config: dict) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def _save_config(self) -> None:
        self._write_config(self.config)

    def _find_member(self, name: str) -> dict | None:
        for member in self.config["members"]:
            if member["name"] == name:
                return member
        return None

    def _upsert_member(self, name: str, role: str, status: str) -> dict:
        member = self._find_member(name)
        if member:
            member["role"] = role
            member["status"] = status
        else:
            member = {"name": name, "role": role, "status": status}
            self.config["members"].append(member)
        self._save_config()
        return member

    def _set_status(self, name: str, status: str) -> None:
        member = self._find_member(name)
        if not member:
            raise ValueError(f"队友 '{name}' 不存在")
        member["status"] = status
        self._save_config()

    def _request_id(self) -> str:
        return str(uuid.uuid4())[:8]

    def _start_thread(self, name: str, role: str, prompt: str) -> None:
        self.shutdown_flags[name] = False
        self._upsert_member(name, role, "working")
        thread = threading.Thread(target=self._teammate_loop, args=(name, role, prompt), daemon=True)
        self.threads[name] = thread
        thread.start()

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find_member(name)
        if member and member["status"] != "shutdown":
            thread = self.threads.get(name)
            if thread and thread.is_alive():
                return f"队友 '{name}' 已存在，当前状态：{member['status']}"
            self._start_thread(name, role, prompt)
            return f"已恢复队友 '{name}'（角色：{role}）"
        self._start_thread(name, role, prompt)
        return f"已创建队友 '{name}'（角色：{role}）"

    def list_teammates(self) -> str:
        members = self.config.get("members", [])
        if not members:
            return "- 当前没有队友"
        lines = ["# Team Roster"]
        for member in members:
            lines.append(f"- {member['name']} [{member['role']}] status={member['status']}")
        if self.shutdown_requests:
            lines.append("")
            lines.append("# Shutdown Requests")
            for req_id, request in self.shutdown_requests.items():
                lines.append(f"- {req_id}: target={request['target']} status={request['status']}")
        if self.plan_requests:
            lines.append("")
            lines.append("# Plan Requests")
            for req_id, request in self.plan_requests.items():
                lines.append(f"- {req_id}: from={request['from']} status={request['status']} plan={request['plan']}")
        return "\n".join(lines)

    def send_message(self, sender: str, to: str, content: str, msg_type: str = "message") -> str:
        if to != "lead" and not self._find_member(to):
            raise ValueError(f"队友 '{to}' 不存在")
        self.bus.send(sender, to, content, msg_type)
        if to != "lead":
            self._set_status(to, "working")
        return f"消息已发送：{sender} -> {to} [{msg_type}]"

    def broadcast_message(self, sender: str, content: str, msg_type: str = "message") -> str:
        targets = [member["name"] for member in self.config.get("members", []) if member["status"] != "shutdown" and member["name"] != sender]
        if not targets:
            return "没有可广播的队友"
        for target in targets:
            self.bus.send(sender, target, content, msg_type)
            self._set_status(target, "working")
        return f"广播已发送给：{', '.join(targets)}"

    def read_inbox(self, name: str) -> str:
        messages = self.bus.read_inbox(name)
        return json.dumps(messages, ensure_ascii=False, indent=2)

    def request_shutdown(self, teammate: str) -> str:
        member = self._find_member(teammate)
        if not member:
            raise ValueError(f"队友 '{teammate}' 不存在")
        req_id = self._request_id()
        self.shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
        self.bus.send("lead", teammate, "Please shut down gracefully.", "shutdown_request", {"request_id": req_id})
        self._set_status(teammate, "working")
        return f"已发送 shutdown request {req_id} 给 {teammate}（status: pending）"

    def respond_shutdown(self, sender: str, request_id: str, approve: bool, reason: str = "") -> str:
        request = self.shutdown_requests.get(request_id)
        if not request:
            raise ValueError(f"shutdown request '{request_id}' 不存在")
        if request["target"] != sender:
            raise ValueError(f"shutdown request '{request_id}' 不属于 {sender}")
        request["status"] = "approved" if approve else "rejected"
        self.bus.send(sender, "lead", reason, "shutdown_response", {"request_id": request_id, "approve": approve})
        if approve:
            self.shutdown_flags[sender] = True
            self._set_status(sender, "shutdown")
        else:
            self._set_status(sender, "idle")
        return f"shutdown request {request_id} 已{'批准' if approve else '拒绝'}"

    def submit_plan(self, sender: str, plan: str) -> str:
        req_id = self._request_id()
        self.plan_requests[req_id] = {"from": sender, "plan": plan, "status": "pending"}
        self.bus.send(sender, "lead", plan, "plan_approval_request", {"request_id": req_id})
        return f"计划审批请求已提交：{req_id}（status: pending）"

    def review_plan(self, request_id: str, approve: bool, feedback: str = "") -> str:
        request = self.plan_requests.get(request_id)
        if not request:
            raise ValueError(f"plan request '{request_id}' 不存在")
        request["status"] = "approved" if approve else "rejected"
        self.bus.send("lead", request["from"], feedback, "plan_approval_response", {"request_id": request_id, "approve": approve})
        return f"计划审批 {request_id} 已{'批准' if approve else '拒绝'}"

    def _build_inbox_observation(self, messages: list[dict]) -> str:
        return json.dumps(messages, ensure_ascii=False, indent=2)

    def _teammate_loop(self, name: str, role: str, prompt: str) -> None:
        tools = self.agent.build_teammate_tools(name)
        system_prompt = self.agent.render_system_prompt(
            teammate_system_prompt_template,
            tool_map=tools,
            extra_vars={
                "teammate_name": name,
                "teammate_role": role,
            },
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"<question>{prompt}</question>"},
        ]
        idle = False
        turns = 0
        tool_failures: dict[str, int] = {}
        while not self.shutdown_flags.get(name, False):
            inbox_messages = self.bus.read_inbox(name)
            if inbox_messages:
                messages.append({"role": "user", "content": f"<inbox>{self._build_inbox_observation(inbox_messages)}</inbox>"})
                idle = False
                turns = 0
                tool_failures = {}
                self._set_status(name, "working")
            elif idle:
                time.sleep(self.idle_sleep)
                continue

            turns += 1
            if turns > self.max_turns_per_activation:
                self.bus.send(name, "lead", "达到最大执行轮数，已回到 idle 状态。", "task_result")
                self._set_status(name, "idle")
                idle = True
                turns = 0
                continue

            content = self.agent.dispatch_model(messages)

            if "<final_answer>" in content:
                import re
                match = re.search(r"<final_answer>(.*?)</final_answer>", content, re.DOTALL)
                answer = match.group(1).strip() if match else "子任务完成"
                self.bus.send(name, "lead", answer, "task_result")
                self._set_status(name, "idle")
                idle = True
                turns = 0
                continue

            import re
            action_match = re.search(r"<action>(.*?)</action>", content, re.DOTALL)
            if not action_match:
                messages.append({"role": "user", "content": "<observation>格式错误：必须输出 <action>...</action> 或 <final_answer>。</observation>"})
                continue

            action = action_match.group(1).strip()
            try:
                tool_name, args, kwargs = self.agent.parse_action(action)
            except Exception as e:
                messages.append({"role": "user", "content": f"<observation>Action 解析失败：{e}</observation>"})
                continue

            observation, should_stop = self.agent._run_tool_with_hooks(
                tool_name,
                args,
                kwargs,
                messages,
                available_tools=tools,
                cancel_message="队友任务被取消",
            )
            if should_stop:
                self.bus.send(name, "lead", observation, "task_result")
                self._set_status(name, "idle")
                idle = True
                turns = 0
                continue
            recovery_result = self.agent._recover_from_tool_failure(tool_name, observation, tool_failures, messages)
            if recovery_result is not None:
                self.bus.send(name, "lead", recovery_result, "task_result")
                self._set_status(name, "idle")
                idle = True
                turns = 0
                tool_failures = {}
                continue

        self._set_status(name, "shutdown")
