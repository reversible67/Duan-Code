import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from team import MessageBus, TeammateManager


class DummyThread:
    def __init__(self, alive):
        self._alive = alive

    def is_alive(self):
        return self._alive


class DummyAgent:
    pass


def test_message_bus_send_and_drain():
    temp_dir = tempfile.mkdtemp(prefix="team-bus-")
    try:
        bus = MessageBus(temp_dir)
        bus.send("lead", "alice", "hello")
        msgs = bus.read_inbox("alice")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hello"
        assert bus.read_inbox("alice") == []
        print("✅ MessageBus 发送与 drain 通过")
    finally:
        shutil.rmtree(temp_dir)


def test_spawn_can_resume_registered_member_without_duplicate():
    temp_dir = tempfile.mkdtemp(prefix="team-spawn-")
    try:
        manager = TeammateManager(temp_dir, DummyAgent())
        starts = []

        def fake_start(name, role, prompt):
            starts.append((name, role, prompt))
            manager.shutdown_flags[name] = False
            manager._upsert_member(name, role, "working")
            manager.threads[name] = DummyThread(True)

        manager._start_thread = fake_start
        result1 = manager.spawn("alice", "coder", "write code")
        result2 = manager.spawn("alice", "coder", "write code")
        manager.threads["alice"] = DummyThread(False)
        result3 = manager.spawn("alice", "coder", "resume")

        assert "已创建队友" in result1
        assert "已存在" in result2
        assert "已恢复队友" in result3
        assert len([m for m in manager.config["members"] if m["name"] == "alice"]) == 1
        print("✅ spawn 创建/恢复逻辑通过")
    finally:
        shutil.rmtree(temp_dir)


def test_shutdown_protocol_updates_tracker_and_notifies_lead():
    temp_dir = tempfile.mkdtemp(prefix="team-shutdown-")
    try:
        manager = TeammateManager(temp_dir, DummyAgent())
        manager._upsert_member("alice", "coder", "idle")
        result = manager.request_shutdown("alice")
        req_id = next(iter(manager.shutdown_requests.keys()))
        assert req_id in result
        alice_inbox = manager.read_inbox("alice")
        assert "shutdown_request" in alice_inbox
        response = manager.respond_shutdown("alice", req_id, True, "done")
        assert "批准" in response
        assert manager.shutdown_requests[req_id]["status"] == "approved"
        lead_inbox = manager.read_inbox("lead")
        assert "shutdown_response" in lead_inbox
        print("✅ shutdown 协议通过")
    finally:
        shutil.rmtree(temp_dir)


def test_plan_review_protocol_updates_tracker_and_notifies_member():
    temp_dir = tempfile.mkdtemp(prefix="team-plan-")
    try:
        manager = TeammateManager(temp_dir, DummyAgent())
        manager._upsert_member("alice", "coder", "idle")
        result = manager.submit_plan("alice", "Refactor auth module in two phases")
        req_id = next(iter(manager.plan_requests.keys()))
        assert req_id in result
        lead_inbox = manager.read_inbox("lead")
        assert "plan_approval_request" in lead_inbox
        review = manager.review_plan(req_id, False, "too risky")
        assert "拒绝" in review
        assert manager.plan_requests[req_id]["status"] == "rejected"
        alice_inbox = manager.read_inbox("alice")
        assert "plan_approval_response" in alice_inbox
        print("✅ plan review 协议通过")
    finally:
        shutil.rmtree(temp_dir)


if __name__ == "__main__":
    test_message_bus_send_and_drain()
    test_spawn_can_resume_registered_member_without_duplicate()
    test_shutdown_protocol_updates_tracker_and_notifies_lead()
    test_plan_review_protocol_updates_tracker_and_notifies_member()
    print("\n🎉 test_team 全部通过")
