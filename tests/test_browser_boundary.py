import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import pytest
from lab.tools.computer import Computer, parse, valid_url


@pytest.mark.parametrize("action", ["bash ls", "eval 1+1", "upload /etc/passwd", "download x", "attach ws://x",
    "打开 file:///etc/passwd", "打开 data:text/html,x", "打开 javascript:alert(1)", "按 Control+L",
    "press F12", "点击 e1; rm -rf /", "在 ../x 输入 x", "执行 cat .env", "type --filename=/tmp/x"])
def test_forbidden_actions_never_parse(action):
    with pytest.raises(ValueError): parse(action)


@pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254", "http://[::1]", "https://10.0.0.1",
    "https://example.com.evil.com", "https://example.com@127.0.0.1", "https://example.com:444", "https://demo.playwright.dev\\@localhost"])
def test_private_local_or_untrusted_urls_are_denied(url):
    with pytest.raises(ValueError): valid_url(url, lambda *a, **k: [])


def test_dns_rebinding_answer_is_denied():
    def resolver(*a, **k): return [(2, 1, 6, "", ("127.0.0.1", 443))]
    with pytest.raises(ValueError): valid_url("https://example.com", resolver)
    def public(*a, **k): return [(2, 1, 6, "", ("93.184.215.14", 443))]
    assert valid_url("https://example.com", public) == "https://example.com"


async def test_option_injection_refused_before_subprocess(store):
    c = Computer(store)
    async def unexpected(args): raise AssertionError("No process should start")
    c.command = unexpected
    assert await c.perform("在 e6 输入 --config=/etc/passwd") == "电脑没有执行这个操作。"


def test_guard_denies_redirects_subrequests_and_downloads(tmp_path):
    guard = Path(__file__).resolve().parents[1] / "lab/tools/browser_guard.cjs"
    # Exercise the installed executor hook with a controlled Playwright route peer, without network.
    script = r'''
const assert = require('node:assert/strict');
const dns = require('node:dns').promises;
dns.lookup = async () => [{address:'93.184.215.14'}];
const fs = require('node:fs'); fs.writeFileSync = () => {};
const guard = require(process.argv[1]);
(async () => {
  let handle, websockets;
  const context={routeWebSocket:async(p,h)=>{websockets=h}, route:async(p,h)=>{handle=h}, on:()=>{}};
  await guard.default({page:{context:()=>context,on:()=>{}}});
  for (const [url,method,status,headers,expectedFetch] of [
    ['https://example.com','GET',302,{},true],
    ['http://169.254.169.254/','GET',200,{},false],
    ['file:///etc/passwd','GET',200,{},false],
    ['https://example.com','POST',200,{},false],
    ['https://example.com','GET',200,{'content-disposition':'attachment'},true]]) {
    let aborted=false, fetched=false;
    await handle({request:()=>({url:()=>url,method:()=>method}),abort:async()=>{aborted=true},
      fetch:async opts=>{fetched=true;assert.equal(opts.maxRedirects,0);return {
        status:()=>status,headers:()=>headers,dispose:async()=>{}}},
      fulfill:async()=>{throw Error('Unsafe response was fulfilled')}});
    assert.equal(aborted,true);assert.equal(fetched,expectedFetch);
  }
  let closed=false;websockets({close:()=>{closed=true}});assert.equal(closed,true);
  dns.lookup=async()=>[{address:'10.0.0.1'}];assert.equal(await guard.permitted('https://example.com'),false);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    result = subprocess.run(["node", "-e", script, str(guard)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt-in public demo browser integration")
async def test_real_browser_session_snapshot_input_click_navigation_screenshot(store):
    c = Computer(store)
    try:
        opened = await c.perform("打开 https://demo.playwright.dev/todomvc/")
        assert "电脑没有执行" not in opened
        page = await c.perform("查看当前页面")
        ref = re.search(r'textbox[^\n]*\[ref=(e\d+)\]', page)
        assert ref, page
        assert "电脑没有执行" not in await c.perform(f"在 {ref[1]} 输入 offline boundary test")
        assert "电脑没有执行" not in await c.perform("按 Enter")
        assert "offline boundary test" in await c.perform("查看当前页面")
        page = await c.perform("查看当前页面")
        checkbox = re.search(r'checkbox[^\n]*\[ref=(e\d+)\]', page)
        assert checkbox, page
        assert "电脑没有执行" not in await c.perform(f"点击 {checkbox[1]}")
        assert "Screenshot" in await c.perform("截图")
        assert list(c.root.glob("*.png"))
        assert "电脑没有执行" not in await c.perform("打开 https://example.com")
        assert "电脑没有执行" not in await c.perform("返回")
        assert "demo.playwright.dev" in c.state["url"]
        before = c.state.copy()
        assert await c.perform("打开 http://127.0.0.1") == "电脑没有执行这个操作。"
        assert c.state == before
        assert "电脑没有执行" not in await c.perform("关闭浏览器")
    finally: await c.close()
