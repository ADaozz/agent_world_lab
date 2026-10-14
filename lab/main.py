import argparse
import asyncio
from contextlib import nullcontext
import fcntl
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .experiment import Config, digest
from .loop import AgentLoop
from .model import DeepSeek, MockModel
from .report import replay, report
from .storage import Store
from .tool_registry import schemas
from .tools.computer import Computer
from .tools.objects import Tools


def duration(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", value)
    if not match: raise argparse.ArgumentTypeError("Duration must be like 30m or 120s")
    return float(match[1]) * {"": 1, "s": 1, "m": 60, "h": 3600}[match[2]]


def locate(runs, run_id):
    if not re.fullmatch(r"run-[A-Za-z0-9-]+", run_id): raise ValueError("Invalid run id")
    path = Path(runs).resolve() / run_id
    if path.is_symlink() or not (path / "state.sqlite").is_file(): raise ValueError("Unknown run id")
    return path


async def execute(st, resume=False):
    agent_lock = open(st.root / ".agent.lock", "a")
    try:
        fcntl.flock(agent_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        agent_lock.close()
        raise RuntimeError("Another agent already owns this run") from None
    computer = None
    model = None
    watcher = None
    ended = "failed"
    try:
        with nullcontext(agent_lock):
            if st.meta("phase") != "idle": raise ValueError("Previous operation is indeterminate; use inspect/replay")
            config = st.config
            model = MockModel() if config.mock else DeepSeek(config)
            if config.mock: model.index = st.meta("calls")
            computer = Computer(st) if config.browser_enabled else None
            with st.transaction() as tx:
                st.set_meta(tx, "status", "running")
                st.log(tx, "runtime", "runtime.started", {"pid": os.getpid(), "mock": config.mock,
                           "tools": schemas(st.world()), "schema_hash": digest(schemas(st.world()))})
            watcher = await asyncio.create_subprocess_exec(sys.executable, "-m", "lab.watcher", "--run-dir", str(st.root),
                       stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            loop_task = asyncio.create_task(AgentLoop(st, model, Tools(st, computer)).run(resume=resume))
            monitor = asyncio.create_task(watcher.wait())
            try:
                done, _ = await asyncio.wait((loop_task, monitor), return_when=asyncio.FIRST_COMPLETED)
                if monitor in done:
                    loop_task.cancel()
                    await asyncio.gather(loop_task, return_exceptions=True)
                    raise RuntimeError("Watcher exited while the agent was running")
                reason = await loop_task
                ended = "ended"
            finally:
                if not loop_task.done():
                    loop_task.cancel(); await asyncio.gather(loop_task, return_exceptions=True)
                monitor.cancel(); await asyncio.gather(monitor, return_exceptions=True)
    except BaseException as exc:
        st.emit("runtime", "run.failed", {"error": type(exc).__name__})
        raise
    finally:
        with st.transaction() as tx: st.set_meta(tx, "status", ended)
        if watcher and watcher.returncode is None:
            try: await asyncio.wait_for(watcher.wait(), 2)
            except asyncio.TimeoutError:
                watcher.terminate(); await watcher.wait()
        if computer: await computer.close()
        if model: await model.close()
        if ended == "ended": st.emit("runtime", "run.ended", {"reason": reason})
        agent_lock.close()


def main():
    parser = argparse.ArgumentParser(description="Agent World Lab: independent auditable runs")
    parser.add_argument("--runs-dir", default="runs")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--max-duration", type=duration, default=1800)
    for arg, default in (("max-model-calls", 60), ("max-tool-calls", 200), ("max-tools-per-turn", 20),
                         ("max-total-tokens", 100000), ("max-events-per-minute", 120), ("max-consecutive-noop-turns", 10)):
        run.add_argument("--" + arg, type=int, default=default)
    run.add_argument("--max-cost", type=float, default=1)
    run.add_argument("--input-cost-per-million", type=float, default=0)
    run.add_argument("--output-cost-per-million", type=float, default=0)
    run.add_argument("--mode", choices=("A", "B", "C"), default="C")
    run.add_argument("--heartbeat-seconds", type=float, default=120)
    browsing = run.add_mutually_exclusive_group()
    browsing.add_argument("--browser", action="store_true", dest="browser")
    browsing.add_argument("--no-browser", action="store_false", dest="browser")
    run.set_defaults(browser=None)
    run.add_argument("--mock", action="store_true")
    run.add_argument("--resume", metavar="RUN_ID")
    for cmd in ("inspect", "logs", "report", "replay"):
        p = sub.add_parser(cmd)
        p.add_argument("--run-id", required=True)
        if cmd == "logs": p.add_argument("-n", type=int, default=20)
        if cmd == "replay": p.add_argument("--no-llm", action="store_true", required=True)
    reset = sub.add_parser("reset", help="Create a new isolated world; existing runs are preserved")
    reset.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    st = None
    try:
        if args.command in ("run", "reset"):
            if args.command == "run" and args.resume:
                st = Store(locate(args.runs_dir, args.resume))
            else:
                values = vars(args)
                fields = Config.__dataclass_fields__
                config_args = {k: v for k, v in values.items() if k in fields}
                config_args.update(model=os.environ.get("MODEL", "deepseek-flash"),
                                   base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                                   thinking=os.environ.get("THINKING", "disabled"),
                                   browser_enabled=values.get("browser") if values.get("browser") is not None else not values.get("mock", True))
                st = Store.create(args.runs_dir, Config(**config_args))
                try:
                    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
                    dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout)
                except OSError: commit, dirty = None, True
                st.emit("runtime", "runtime.provenance", {"git_commit": commit, "dirty": dirty,
                        "python": sys.version, "dependencies": {x: importlib.metadata.version(x) for x in ("agent-world-lab", "openai")},
                        "schemas": schemas(st.world()), "schema_hash": digest(schemas(st.world()))})
            print(st.run_id, flush=True)
            if args.command == "run": asyncio.run(execute(st, resume=bool(args.resume)))
        else:
            st = Store(locate(args.runs_dir, args.run_id))
            events = st.traces()
            if args.command == "inspect":
                result = {"run_id": st.run_id, "status": st.meta("status"), "config": st.config.to_dict(),
                          "world": st.world(), "version": st.meta("version"), "phase": st.meta("phase")}
            elif args.command == "logs": result = events[-max(0, args.n):] if args.n > 0 else []
            elif args.command == "report": result = report(events)
            else:
                result = replay(events)
                result["matches_database"] = result["world"] == st.world() and result["version"] == st.meta("version")
                result["matches_diary"] = result["diary"] == (st.root / "diary.md").read_text(encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        from .traces import redact
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Run failed; inspect the trace for the error type"
        print(f"{type(exc).__name__}: {redact(detail, (os.environ.get('DEEPSEEK_API_KEY', ''),))}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if st: st.close()


if __name__ == "__main__": main()
