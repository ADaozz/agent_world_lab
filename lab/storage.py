"""Run-local durable state. Every mutation and its audit event share one transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .experiment import Config, canonical
from .world.definitions import initial_world
from .traces import redact


class Store:
    def __init__(self, run_dir):
        self.root = Path(run_dir).resolve()
        self.db = sqlite3.connect(self.root / "state.sqlite", timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA synchronous=FULL")
        self.run_id = self.root.name
        self.session_id = "session-" + uuid.uuid4().hex

    @classmethod
    def create(cls, runs, config: Config):
        run_id = "run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:10]
        root = Path(runs).resolve() / run_id
        root.mkdir(parents=True, mode=0o700)
        store = cls(root)
        store.db.executescript('''
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE world(name TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE outbox(version INTEGER PRIMARY KEY, name TEXT, before_state TEXT,
                                after_state TEXT, trace_id TEXT, created REAL);
            CREATE TABLE trace(sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,
                               payload TEXT NOT NULL);
            CREATE TABLE inbox(event_id TEXT PRIMARY KEY, kind TEXT, merge_key TEXT,
                               content TEXT, versions TEXT, parent TEXT, created REAL,
                               critical INTEGER, acked INTEGER DEFAULT 0);
            CREATE TABLE diary(sequence INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT);
        ''')
        with store.transaction() as tx:
            for k, v in {"config": config.to_dict(), "version": 0, "watcher_cursor": 0,
                         "first_discovery": False, "heartbeat_index": 0, "last_heartbeat": 0,
                         "timer_time": 0, "watcher_time": 0, "messages": [], "phase": "idle",
                         "calls": 0, "tools": 0, "tokens": 0, "cost": 0.0, "noop_turns": 0,
                         "schema_hash": None, "status": "created"}.items():
                store.set_meta(tx, k, v)
            store.set_meta(tx, "started_at", 0)
            for name, state in initial_world().items():
                tx.execute("INSERT INTO world VALUES (?,?)", (name, canonical(state)))
            store.log(tx, "runtime", "run.created", {"config": config.to_dict()})
        (root / "diary.md").touch(mode=0o600)
        (root / "metadata.json").write_text(canonical({"run_id": run_id, "config": config.to_dict()}), encoding="utf-8")
        return store

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.flush_trace()

    def meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None: raise KeyError(key)
        return json.loads(row[0])

    def set_meta(self, tx, key, value):
        tx.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, canonical(value)))

    @property
    def config(self):
        return Config(**self.meta("config"))

    def world(self):
        return {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT name,state FROM world ORDER BY name")}

    def log(self, tx, source, kind, data=None, *, parent=None, turn_id=None, tool_call_id=None,
            version_before=None, version_after=None):
        event_id = "evt-" + uuid.uuid4().hex
        payload = {"event_id": event_id, "run_id": self.run_id, "session_id": self.session_id,
                   "source": source, "type": kind, "time_utc": datetime.now(timezone.utc).isoformat(),
                   "monotonic_ns": time.monotonic_ns(), "parent_event_id": parent,
                   "turn_id": turn_id, "tool_call_id": tool_call_id,
                   "world_version_before": version_before, "world_version_after": version_after,
                   "data": redact(data or {}, (os.environ.get("DEEPSEEK_API_KEY", ""),))}
        cur = tx.execute("INSERT INTO trace(event_id,payload) VALUES (?,?)", (event_id, canonical(payload)))
        payload["sequence"] = cur.lastrowid
        tx.execute("UPDATE trace SET payload=? WHERE sequence=?", (canonical(payload), cur.lastrowid))
        return event_id

    def emit(self, source, kind, data=None, **context):
        with self.transaction() as tx:
            return self.log(tx, source, kind, data, **context)

    def change(self, tx, name, before, after, *, parent=None, tool_call_id=None, now=0):
        version = self.meta("version")
        if before == after:
            return self.log(tx, "world", "world.action_noop", {"object": name}, parent=parent,
                            tool_call_id=tool_call_id, version_before=version, version_after=version)
        self.set_meta(tx, "version", version + 1)
        tx.execute("UPDATE world SET state=? WHERE name=?", (canonical(after), name))
        event_id = self.log(tx, "world", "world.state_changed", {
            "object": name, "before": before, "after": after,
            "diff": {k: {"before": before[k], "after": v} for k, v in after.items() if before[k] != v}},
            parent=parent, tool_call_id=tool_call_id, version_before=version, version_after=version + 1)
        tx.execute("INSERT INTO outbox VALUES (?,?,?,?,?,?)",
                   (version + 1, name, canonical(before), canonical(after), event_id, now))
        return event_id

    def flush_trace(self):
        """SQLite is authoritative; serialize concurrent process exports and repair a torn last line."""
        with open(self.root / ".trace.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / "trace.jsonl"
            last = 0
            with open(path, "a+b") as stream:
                stream.seek(0)
                valid_end = 0
                for line in stream:
                    try:
                        item = json.loads(line)
                        if not line.endswith(b"\n") or item["sequence"] != last + 1: break
                        last = item["sequence"]
                        valid_end = stream.tell()
                    except (ValueError, KeyError): break
                stream.seek(valid_end)
                stream.truncate()
                for row in self.db.execute("SELECT payload FROM trace WHERE sequence>? ORDER BY sequence", (last,)):
                    stream.write((row[0] + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())

    def traces(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM trace ORDER BY sequence")]

    def sync_diary(self):
        content = "".join(r[0] + "\n" for r in self.db.execute("SELECT content FROM diary ORDER BY sequence"))
        path = self.root / "diary.md"
        if path.is_symlink(): raise ValueError("Diary projection cannot be a symlink")
        # Projection recovery never repeats a logical append.
        with open(path, "w", encoding="utf-8") as f:
            f.write(content); f.flush(); os.fsync(f.fileno())

    def close(self):
        self.flush_trace()
        self.db.close()
