"""The agent never calls Watcher.step; the CLI starts this module as a separate process."""
import argparse
import fcntl
import json
import os
import time

from .experiment import HEARTBEATS
from .inbox import enqueue
from .observations import describe
from .storage import Store
from .world.rules import elapsed


class Watcher:
    def __init__(self, store):
        self.st = store

    def step(self, now):
        st = self.st
        config = st.config
        with st.transaction() as tx:
            timer_before = st.meta("timer_time")
            if timer_before == 0:
                st.set_meta(tx, "timer_time", now)
            elif now - timer_before >= 1:
                seconds = min(now - timer_before, config.max_duration)
                tick = st.log(tx, "timer", "timer.tick", {"elapsed": seconds, "clock": now})
                for name, before in st.world().items():
                    after = elapsed(name, before, seconds)
                    if before != after: st.change(tx, name, before, after, parent=tick, now=now)
                st.set_meta(tx, "timer_time", now)
            cursor = st.meta("watcher_cursor")
            rows = tx.execute("SELECT * FROM outbox WHERE version>? ORDER BY version", (cursor,)).fetchall()
            for row in rows:
                before, after = json.loads(row["before_state"]), json.loads(row["after_state"])
                event = st.log(tx, "watcher", "watcher.event_seen", {"version": row["version"], "object": row["name"]}, parent=row["trace_id"])
                content, critical = describe(row["name"], before, after)
                if row["name"] == "mysterious_box" and after["open"] and not st.meta("first_discovery"):
                    st.set_meta(tx, "first_discovery", True)
                    if config.mode != "A":
                        enqueue(st, tx, "discovery", "盒盖打开了。里面静静躺着两块药丸形状的石头，一红一蓝。",
                                now, critical=True, versions=(row["version"],), parent=event)
                    content = None
                if content and config.mode != "A":
                    enqueue(st, tx, "state", content, now, key=row["name"], critical=critical,
                            versions=(row["version"],), parent=event)
                elif content or config.mode == "A":
                    st.log(tx, "watcher", "event.dropped", {"reason": "mode_A_no_state_observations", "version": row["version"]}, parent=event)
                cursor = row["version"]
            st.set_meta(tx, "watcher_cursor", cursor)
            last = st.meta("last_heartbeat")
            if last == 0:
                st.set_meta(tx, "last_heartbeat", now)
            elif now - last >= config.heartbeat_seconds:
                index = st.meta("heartbeat_index")
                st.set_meta(tx, "heartbeat_index", index + 1)
                st.set_meta(tx, "last_heartbeat", now)
                # A/B receive matched non-informative decision opportunities, explicitly labelled in traces.
                kind = "heartbeat" if config.mode == "C" else "decision_opportunity"
                content = HEARTBEATS[(config.seed + index) % len(HEARTBEATS)] if config.mode == "C" else "片刻过去了。"
                event = st.log(tx, "timer", "timer.heartbeat_generated", {"kind": kind, "clock": now})
                enqueue(st, tx, kind, content, now, key="heartbeat", parent=event)
            st.set_meta(tx, "watcher_time", now)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    st = Store(args.run_dir)
    try:
        with open(st.root / ".watcher.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            st.emit("watcher", "watcher.started", {"pid": os.getpid(), "cursor": st.meta("watcher_cursor")})
            watcher = Watcher(st)
            while st.meta("status") == "running":
                watcher.step(time.time())
                time.sleep(st.config.poll_seconds)
            watcher.step(time.time())
            st.emit("watcher", "watcher.stopped", {"cursor": st.meta("watcher_cursor")})
    finally:
        st.close()


if __name__ == "__main__": main()
