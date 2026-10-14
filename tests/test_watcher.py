import asyncio
from dataclasses import replace
import json
import os
import sys
import time
from lab.inbox import deliver
from lab.storage import Store
from lab.tools.objects import Tools
from lab.watcher import Watcher
from .conftest import call


async def test_discovery_survives_rapid_close_and_restart(store):
    w = Watcher(store)
    tools = Tools(store)
    await tools.execute(call("mysterious_box", "打开"))
    await tools.execute(call("mysterious_box", "合上", "call-2"))
    w.step(100)
    rows = store.db.execute("SELECT * FROM inbox WHERE critical=1").fetchall()
    assert len(rows) == 1 and "一红一蓝" in rows[0]["content"]
    second = Store(store.root)
    Watcher(second).step(101)
    assert second.db.execute("SELECT COUNT(*) FROM inbox WHERE kind='discovery'").fetchone()[0] == 1
    assert second.meta("watcher_cursor") == store.meta("version")
    second.close()
    await tools.execute(call("mysterious_box", "打开", "call-3"))
    w.step(102)
    assert store.db.execute("SELECT COUNT(*) FROM inbox WHERE kind='discovery'").fetchone()[0] == 1


async def test_100_updates_merge_with_complete_causal_trace(store):
    tools = Tools(store)
    for i in range(100): await tools.execute(call("silver_bell", "拿" if i % 2 else "摇", f"c-{i}"))
    Watcher(store).step(100)
    rows = store.db.execute("SELECT * FROM inbox WHERE kind='state'").fetchall()
    assert len(rows) == 1
    assert len(json.loads(rows[0]["versions"])) >= 5
    assert any(e["type"] == "event.aggregated" for e in store.traces())


def test_virtual_120_second_heartbeat_coalesces_and_ack_is_idempotent(store):
    w = Watcher(store)
    w.step(100)
    w.step(219)
    assert store.db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 0
    w.step(220); w.step(340); w.step(460)
    assert store.db.execute("SELECT COUNT(*) FROM inbox WHERE acked=0").fetchone()[0] == 1
    assert deliver(store, 460, "t-1") == 1
    assert deliver(store, 460, "t-1") == 0
    assert len(store.meta("messages")) == 1


async def test_timed_states_are_independent(store):
    tools = Tools(store)
    await tools.execute(call("black_hourglass", "翻转"))
    await tools.execute(call("brass_lamp", "点燃", "call-2"))
    before = store.world()
    # Extend this run's virtual-time limit for a full 120 second advance.
    with store.transaction() as tx: store.set_meta(tx, "config", replace(store.config, max_duration=600).to_dict())
    watcher = Watcher(store); watcher.step(100); watcher.step(220)
    world = store.world()
    assert world["black_hourglass"]["sand"] == 0
    assert world["brass_lamp"]["oil"] < before["brass_lamp"]["oil"]
    assert world["blue_bottle"] == before["blue_bottle"]
    assert any("流尽" in r[0] for r in store.db.execute("SELECT content FROM inbox"))


async def test_watcher_separate_process_restarts_at_cursor(store):
    with store.transaction() as tx: store.set_meta(tx, "status", "running")
    await Tools(store).execute(call("mysterious_box", "打开"))
    async def launch():
        return await asyncio.create_subprocess_exec(sys.executable, "-m", "lab.watcher", "--run-dir", str(store.root))
    async def wait_cursor():
        async with asyncio.timeout(3):
            while store.meta("watcher_cursor") < 1: await asyncio.sleep(.02)
    p = await launch()
    try:
        await wait_cursor()
        assert p.pid != os.getpid()
    finally: p.terminate(); await p.wait()
    p = await launch()
    try:
        await asyncio.sleep(.3)
        assert store.db.execute("SELECT COUNT(*) FROM inbox WHERE kind='discovery'").fetchone()[0] == 1
        starts = [e for e in store.traces() if e["type"] == "watcher.started"]
        assert starts[-1]["data"]["cursor"] == 1
    finally: p.terminate(); await p.wait()
