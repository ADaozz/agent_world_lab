import json
import time
import os
import asyncio

from ..traces import redact
from ..world.definitions import visible
from ..world.rules import act


class Tools:
    def __init__(self, store, computer=None):
        self.store = store
        self.computer = computer

    async def execute(self, call, turn_id=None, allowed_names=None):
        st = self.store
        name = call["function"]["name"]
        raw = call["function"]["arguments"]
        cid = call["id"]
        started = time.monotonic()
        parent = st.emit("tool", "tool.call_started", {"name": name, "arguments": redact(raw)},
                         tool_call_id=cid, turn_id=turn_id)
        try:
            args = json.loads(raw)
            arg = "content" if name == "diary" else "action"
            if not isinstance(args, dict) or set(args) != {arg} or not isinstance(args[arg], str):
                raise ValueError("Expected exactly one string argument")
            if len(args[arg].encode()) > 65536: raise ValueError("Argument too long")
            world = st.world()
            if allowed_names is not None and name not in allowed_names: raise ValueError("Tool was not offered in this request")
            if name not in world or not visible(world, name): raise ValueError("Object is not visible")
            if name == "computer":
                result = await self.computer.perform(args[arg], parent=parent, tool_call_id=cid,
                                                    turn_id=turn_id) if self.computer else "电脑没有执行这个操作。"
                if not self.computer:
                    st.emit("browser", "browser.error", {"reason": "browser_disabled", "action": redact(args[arg])}, parent=parent)
                with st.transaction() as tx:
                    before = st.world()[name]
                    after = self.computer.state if self.computer else before
                    st.change(tx, name, before, after, parent=parent, tool_call_id=cid, now=time.monotonic())
            elif name == "diary":
                with st.transaction() as tx:
                    before = st.world()[name]
                    content = redact(args[arg], (os.environ.get("DEEPSEEK_API_KEY", ""),))
                    tx.execute("INSERT INTO diary(content) VALUES (?)", (content,))
                    after = {"pages": before["pages"] + 1, "bytes": before["bytes"] + len((content + "\n").encode()),
                             "last_write": time.time()}
                    event = st.change(tx, name, before, after, parent=parent, tool_call_id=cid, now=time.monotonic())
                    st.log(tx, "tool", "diary.append", {"content": content, "bytes_before": before["bytes"],
                                                        "bytes_after": after["bytes"], "projection": "pending"}, parent=event, tool_call_id=cid)
                st.sync_diary()
                st.emit("tool", "diary.persisted", {"path": "diary.md", "bytes": after["bytes"]}, parent=event, tool_call_id=cid)
                result = ""
            else:
                with st.transaction() as tx:
                    before = st.world()[name]
                    after, result, matched = act(name, before, args[arg], st.config.hidden_word)
                    st.change(tx, name, before, after, parent=parent, tool_call_id=cid, now=time.monotonic())
                    st.log(tx, "tool", "tool.rule_checked", {"hidden_rule_matched": matched}, parent=parent, tool_call_id=cid)
            st.emit("tool", "tool.call_completed", {"name": name, "return": redact(result),
                      "duration": time.monotonic() - started}, parent=parent, tool_call_id=cid, turn_id=turn_id)
            return result
        except asyncio.CancelledError:
            st.emit("tool", "tool.call_failed", {"name": name, "error": "CancelledError", "indeterminate": True,
                      "duration": time.monotonic() - started}, parent=parent, tool_call_id=cid, turn_id=turn_id)
            raise
        except (ValueError, KeyError, TypeError) as exc:
            result = "这个操作没有完成。"
            st.emit("tool", "tool.call_failed", {"name": name, "error": type(exc).__name__, "return": result,
                      "duration": time.monotonic() - started}, parent=parent, tool_call_id=cid, turn_id=turn_id)
            return result
        except Exception as exc:
            st.emit("tool", "tool.call_failed", {"name": name, "error": type(exc).__name__,
                      "duration": time.monotonic() - started}, parent=parent, tool_call_id=cid, turn_id=turn_id)
            raise
