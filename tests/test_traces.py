from dataclasses import replace
import json
import pytest
from lab.inbox import deliver, enqueue
from lab.loop import AgentLoop
from lab.model import MockModel
from lab.report import replay, report
from lab.tools.objects import Tools
from lab.watcher import Watcher
from .conftest import call


async def test_replay_and_report_reconstruct_state_and_causal_timeline(store):
    tools = Tools(store)
    await tools.execute(call("mysterious_box", "打开", "c1"))
    Watcher(store).step(100)
    deliver(store, 100, "turn-next")
    await tools.execute(call("diary", "a note", "c2"))
    events = store.traces()
    replayed = replay(events)
    assert replayed["world"] == store.world()
    assert replayed["diary"] == (store.root / "diary.md").read_text()
    assert replayed["version"] == store.meta("version")
    lookup = {e["event_id"]: e for e in events}
    delivery = next(e for e in events if e["type"] == "event.delivered")
    seen = lookup[delivery["parent_event_id"]]
    changed = lookup[seen["parent_event_id"]]
    started = lookup[changed["parent_event_id"]]
    assert (seen["type"], changed["type"], started["type"]) == ("watcher.event_seen", "world.state_changed", "tool.call_started")
    assert report(events)["tool_calls"] == 2
    assert report(events)["state_changes"] == 2


async def test_audit_export_repairs_torn_tail_without_losing_committed_events(store):
    store.emit("runtime", "test.before")
    path = store.root / "trace.jsonl"
    with path.open("ab") as f: f.write(b'{"sequence":')
    store.emit("runtime", "test.after")
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines == store.traces()
    assert [x["sequence"] for x in lines] == list(range(1, len(lines) + 1))


async def test_no_credentials_in_request_response_tool_diary_or_audit(store, monkeypatch):
    key = "private-test-value-12345"
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    store.emit("agent", "test", {"authorization": "Bearer " + key, "nested": {"value": key}})
    await Tools(store).execute(call("diary", key))
    assert key not in (store.root / "trace.jsonl").read_text()
    assert key not in (store.root / "diary.md").read_text()
    assert key not in str(store.traces())


async def test_fixed_seed_same_world_and_trace_derived_usage(store):
    with store.transaction() as tx: store.set_meta(tx, "config", replace(store.config, max_model_calls=6).to_dict())
    await AgentLoop(store, MockModel(), Tools(store)).run()
    result = report(store.traces())
    assert result["model_calls"] == 6 and result["usage"]["total_tokens"] == 120
    assert replay(store.traces())["world"] == store.world()


@pytest.mark.parametrize("mode,kind", [("A", "decision_opportunity"), ("B", "decision_opportunity"), ("C", "heartbeat")])
async def test_ablation_matched_decision_opportunities(store, mode, kind):
    with store.transaction() as tx: store.set_meta(tx, "config", replace(store.config, mode=mode).to_dict())
    await Tools(store).execute(call("mysterious_box", "打开"))
    w = Watcher(store); w.step(100); w.step(220)
    rows = store.db.execute("SELECT kind FROM inbox").fetchall()
    assert kind in [r[0] for r in rows]
    assert ("discovery" in [r[0] for r in rows]) == (mode != "A")
