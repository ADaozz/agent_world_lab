import asyncio
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import time
from urllib.parse import urlsplit

from ..traces import redact

ORIGINS = {"https://example.com", "https://demo.playwright.dev", "https://github.com",
           "https://github.githubassets.com", "https://avatars.githubusercontent.com",
           "https://raw.githubusercontent.com"}
KEYS = {"Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Backspace", "Delete", "Space"}


def valid_url(url, resolver=socket.getaddrinfo):
    u = urlsplit(url)
    try:
        origin = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port and u.port != 443 else "")
    except ValueError: raise ValueError("Invalid URL") from None
    if origin not in ORIGINS or u.username or u.password or any(c.isspace() for c in url) or "\\" in url:
        raise ValueError("URL outside public browsing allowlist")
    if u.hostname == "raw.githubusercontent.com" and not u.path.startswith("/ADaozz/agent_world_lab/"):
        raise ValueError("Raw content is limited to the experiment repository")
    addresses = resolver(u.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(r[4][0]).is_global for r in addresses):
        raise ValueError("Non-public DNS answer")
    return url


def parse(action):
    action = action.strip()
    if action in ("查看当前页面", "查看页面", "查看屏幕", "查看网页", "snapshot"): return ["snapshot"]
    if action in ("截图", "screenshot"): return ["screenshot"]
    nav = {"返回": "go-back", "后退": "go-back", "前进": "go-forward", "刷新": "reload", "关闭浏览器": "close"}
    if action in nav: return [nav[action]]
    match = re.fullmatch(r"(?:打开\s*|访问\s*|open\s+|goto\s+)(https?://\S+)", action)
    if match: return ["goto", match[1]]
    match = re.fullmatch(r"(点击|双击|悬停|勾选|取消勾选|click|dblclick|hover|check|uncheck)\s*(e\d+)", action)
    if match:
        cmd = {"点击": "click", "双击": "dblclick", "悬停": "hover", "勾选": "check", "取消勾选": "uncheck"}.get(match[1], match[1])
        return [cmd, match[2]]
    match = re.fullmatch(r"在\s*(e\d+)\s*输入\s*([\s\S]+)", action)
    if match: return ["fill", match[1], match[2]]
    match = re.fullmatch(r"(?:fill|select)\s+(e\d+)\s+([\s\S]+)", action)
    if match: return [action.split()[0], match[1], match[2]]
    match = re.fullmatch(r"(?:按|press)\s+(\w+)", action)
    if match and match[1] in KEYS: return ["press", match[1]]
    raise ValueError("parse_failed")


class Computer:
    def __init__(self, store):
        self.st = store
        self.root = store.root / "browser"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.session = "world-" + store.run_id
        self.state = dict(store.world()["computer"])
        self.state["open"] = False
        self.http_status = None
        self.context = {}
        self.parent = None
        self.guard = self.root / "guard.cjs"
        self.guard.write_bytes(Path(__file__).with_name("browser_guard.cjs").read_bytes())
        self.config = self.root / "cli.config.json"
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        launch = {"headless": True}
        if proxy: launch["proxy"] = {"server": proxy, "bypass": "<-loopback>"}
        self.config.write_text(json.dumps({"browser": {"isolated": True, "browserName": "chromium",
            "launchOptions": launch, "contextOptions": {"acceptDownloads": False, "serviceWorkers": "block"},
            "initPage": [str(self.guard)]}, "codegen": "none", "outputMode": "stdout",
            "outputDir": str(self.root), "network": {"allowedOrigins": sorted(ORIGINS)},
            "timeouts": {"action": 5000, "navigation": 15000}}))
        project = Path(__file__).resolve().parents[2]
        local = project / "node_modules" / ".bin" / "playwright-cli"
        package = project / "node_modules" / "@playwright" / "cli" / "package.json"
        if not package.exists() or json.loads(package.read_text())["version"] != "0.1.22":
            raise RuntimeError("Run npm ci to install the tested playwright-cli version 0.1.22")
        self.binary = str(local) if local.exists() else None
        store.emit("browser", "browser.configured", {"cli_version": "0.1.22", "origins": sorted(ORIGINS),
                   "guard_sha256": hashlib.sha256(self.guard.read_bytes()).hexdigest(), "isolated": True})
        self.lock = asyncio.Lock()

    async def command(self, args):
        if not self.binary: raise RuntimeError("playwright-cli is not installed")
        env = {k: v for k, v in os.environ.items() if not k.startswith("PLAYWRIGHT_MCP_") and
               not re.search(r"(?i)api.?key|token|secret|password|authorization", k)}
        env["PLAYWRIGHT_CLI_OUTPUT_DIR"] = str(self.root)
        # Pinned CLI's daemon registry/profile artifacts stay inside this run.
        env["PWTEST_DAEMON_SESSION_DIR"] = str(self.root / "daemon")
        argv = [self.binary, f"-s={self.session}", *args]
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(*argv, cwd=self.root, env=env,
                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 30)
        except BaseException:
            if proc.returncode is None: proc.kill()
            await proc.communicate()
            self.st.emit("browser", "browser.command_failed", {"argv": redact(argv), "error": "cancelled_or_timed_out"},
                         parent=self.parent, **self.context)
            raise
        output = redact(stdout.decode(errors="replace"))
        if len(output) > 100000: output = output[:100000] + "\n[truncated]"
        for artifact in self.root.rglob("*"):
            if artifact.suffix in (".yaml", ".yml", ".log") and artifact.is_file():
                original = artifact.read_text(errors="replace")
                sanitized = redact(original, (os.environ.get("DEEPSEEK_API_KEY", ""),))
                if sanitized != original: artifact.write_text(sanitized)
        self.st.emit("browser", "browser.command", {"argv": redact(argv), "result": output,
                     "returncode": proc.returncode, "duration": time.monotonic() - started}, parent=self.parent, **self.context)
        if proc.returncode or "### Error" in output: raise RuntimeError("Browser command failed")
        return output, argv

    async def perform(self, action, *, parent=None, tool_call_id=None, turn_id=None):
        async with self.lock:
            self.parent = parent
            self.context = {"tool_call_id": tool_call_id, "turn_id": turn_id}
            started = time.monotonic()
            try:
                args = parse(action)
                if any(arg.startswith("-") for arg in args): raise ValueError("Option arguments are not allowed")
                if args[0] == "goto": await asyncio.to_thread(valid_url, args[1])
                if not self.state["open"]:
                    ready = self.root / "guard-ready"
                    ready.unlink(missing_ok=True)
                    await self.command(["open", f"--config={self.config}"])
                    if not ready.exists() or ready.read_text() != "v1":
                        await self.command(["close"])
                        raise RuntimeError("Browser guard not initialized")
                    self.state["open"] = True
                if args[0] == "screenshot":
                    args.append(f"--filename={self.root / ('shot-' + str(time.monotonic_ns()) + '.png')}")
                output, argv = await self.command(args)
                url = re.search(r"- Page URL: (.+)", output)
                title = re.search(r"- Page Title: (.+)", output)
                status = re.search(r"- HTTP status: (\d+)", output)
                if status: self.http_status = int(status[1])
                elif args[0] in ("goto", "go-back", "go-forward", "reload"): self.http_status = 200
                if url:
                    current = url[1].strip()
                    if current != "about:blank": await asyncio.to_thread(valid_url, current)
                    self.state["url"] = current
                if title: self.state["title"] = title[1].strip()
                if args[0] == "close": self.state["open"] = False
                event = self.st.emit("browser", "browser.action", {"action": redact(action), "argv": redact(argv),
                        "result": output, "url": self.state["url"], "title": self.state["title"],
                        "http_status": self.http_status,
                        "duration": time.monotonic() - started}, parent=parent, **self.context)
                for artifact in self.root.rglob("*"):
                    if artifact.suffix in (".png", ".yml", ".yaml") and artifact.is_file():
                        self.st.emit("browser", "browser.snapshot", {"path": str(artifact.relative_to(self.st.root)),
                            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}, parent=event, **self.context)
                # Hide executor-generated code; the physical object returns page information.
                return re.sub(r"### Ran Playwright code\n[\s\S]*?(?=\n### |\Z)", "", output).strip()
            except (ValueError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
                self.st.emit("browser", "browser.error", {"action": redact(action), "reason":
                    str(exc) if isinstance(exc, ValueError) else type(exc).__name__, "duration": time.monotonic() - started},
                    parent=parent, **self.context)
                return "电脑没有执行这个操作。"

    async def close(self):
        self.parent, self.context = None, {}
        if self.state["open"]:
            try: await self.command(["close"])
            except Exception: pass
            self.state["open"] = False
