import asyncio
import fcntl
from dataclasses import replace
import time
import pytest
from lab.experiment import SYSTEM_PROMPT
from lab.inbox import enqueue, deliver
from lab.loop import AgentLoop
from lab.main import execute
from lab.model import MockModel, validate_messages
from lab.tools.objects import Tools
from .conftest import call


def assistant(*calls):
    return {"role": "assistant", "content": None, "tool_calls": list(calls)} if calls else {"role": "assistant", "content": "静悄悄的。"}


async def test_events_during_model_are_buffered_until_all_tool_results(store):
    with store.transaction() as tx:
        store.set_meta(tx, "config", replace(store.config, max_model_calls=2).to_dict())
    started = asyncio.Event()
    release = asyncio.Event()
    class BlockedModel(MockModel):
        async def complete(self, request):
            if self.index == 0:
                started.set(); await release.wait()
            return await super().complete(request)
    model = BlockedModel([assistant(call("golden_throne", "坐", "c1"), call("old_paper", "阅读", "c2")), assistant()])
    task = asyncio.create_task(AgentLoop(store, model, Tools(store)).run())
    await started.wait()
    with store.transaction() as tx:
        enqueue(store, tx, "state", "银铃响了。", time.time(), critical=True)
    await asyncio.sleep(.02)
    assert not task.done() and model.index == 0
    assert store.meta("messages") == [{"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "[环境观察] 四周静悄悄的。"}]
    release.set()
    assert await task == "max_model_calls"
    messages = store.meta("messages")
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "tool", "tool", "user", "assistant"]
    assert messages[3]["content"] == "" and messages[4]["content"] == "ADaozz"
    validate_messages(messages)


async def test_idle_wakes_from_heartbeat_with_distinct_watcher_process(store):
    with store.transaction() as tx:
        store.set_meta(tx, "config", replace(store.config, heartbeat_seconds=.15,
             poll_seconds=.02, max_model_calls=12, max_duration=2, max_consecutive_noop_turns=20).to_dict())
    await execute(store)
    events = store.traces()
    assert any(e["type"] == "timer.heartbeat_generated" for e in events)
    assert any(e["type"] == "event.delivered" and e["data"]["kind"] == "heartbeat" for e in events)
    assert store.meta("calls") == 12
    assert store.meta("phase") == "idle"


async def test_per_batch_budget_pairs_results_without_executing_excess(store):
    with store.transaction() as tx:
        store.set_meta(tx, "config", replace(store.config, max_tools_per_turn=1).to_dict())
    model = MockModel([assistant(call("golden_throne", "坐", "c1"), call("mysterious_box", "打开", "c2"))])
    assert await AgentLoop(store, model, Tools(store)).run() == "max_tools_per_turn"
    assert store.world()["golden_throne"]["pressure"] == 1
    assert not store.world()["mysterious_box"]["open"]
    validate_messages(store.meta("messages"))
    assert any(e["type"] == "tool.call_skipped" for e in store.traces())


async def test_api_failure_is_logged_and_resume_refuses_uncertain_request(store):
    class Failed:
        async def complete(self, request): raise RuntimeError("fake private SDK failure")
    with pytest.raises(RuntimeError): await AgentLoop(store, Failed(), Tools(store)).run()
    assert store.meta("phase") == "model_inflight"
    assert any(e["type"] == "llm.error" for e in store.traces())
    with pytest.raises(ValueError, match="indeterminate"):
        await AgentLoop(store, MockModel(), Tools(store)).run(resume=True)
    assert "fake private SDK failure" not in (store.root / "trace.jsonl").read_text()


async def test_competing_agent_cannot_change_active_run_status(store):
    with store.transaction() as tx: store.set_meta(tx, "status", "running")
    with open(store.root / ".agent.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = store.traces()
        with pytest.raises(RuntimeError, match="already owns"):
            await execute(store)
        assert store.meta("status") == "running"
        assert store.traces() == before


async def test_duration_budget_cancels_local_wait_without_retrying_provider(store):
    with store.transaction() as tx:
        store.set_meta(tx, "config", replace(store.config, max_duration=.03).to_dict())
    model = MockModel(delay=10)
    assert await AgentLoop(store, model, Tools(store)).run() == "max_duration"
    assert store.meta("calls") == 1
    assert store.meta("phase") == "model_inflight"
    assert any(e["type"] == "budget.exhausted" and e["data"]["reason"] == "max_duration" for e in store.traces())


async def test_resume_syncs_current_world_without_replaying_history(store):
    tools = Tools(store)
    await tools.execute(call("mysterious_box", "打开"))
    with store.transaction() as tx:
        store.set_meta(tx, "messages", [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "[环境观察] 四周静悄悄的。"}])
        store.set_meta(tx, "config", replace(store.config, max_model_calls=1).to_dict())
        enqueue(store, tx, "state", "过时的观察", time.time(), critical=True)
    await AgentLoop(store, MockModel([assistant()]), tools).run(resume=True)
    assert store.meta("watcher_cursor") == store.meta("version")
    messages = store.meta("messages")
    assert "过时的观察" not in str(messages)
    assert "药丸" in messages[2]["content"]


def test_protocol_rejects_interleaving_duplicate_and_missing_calls():
    system = {"role": "system", "content": SYSTEM_PROMPT}
    msg = assistant(call("old_paper", "读"))
    for messages in ([system, msg], [system, msg, {"role": "user", "content": "interrupt"}],
                     [system, {"role": "tool", "content": "", "tool_call_id": "unknown"}],
                     [system, assistant(call("old_paper", "读"), call("old_paper", "读"))]):
        with pytest.raises(ValueError): validate_messages(messages)
    with pytest.raises(ValueError): validate_messages([{"role": "system", "content": SYSTEM_PROMPT + "探索。"}])
