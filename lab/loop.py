"""Single sequential tool-calling loop. Environment inputs enter only between complete batches."""
import asyncio
import time
import uuid

from .experiment import SYSTEM_PROMPT, digest
from .inbox import deliver, enqueue
from .model import validate_messages
from .observations import visible_summary
from .tool_registry import schemas
from .traces import redact


class BudgetExceeded(Exception): pass


class AgentLoop:
    def __init__(self, store, model, tools):
        self.st, self.model, self.tools = store, model, tools
        if not store.meta("started_at"):
            with store.transaction() as tx: store.set_meta(tx, "started_at", time.time())
        remaining = max(0, store.config.max_duration - (time.time() - store.meta("started_at")))
        self.deadline = time.monotonic() + remaining
        self.event_times = []

    def budget(self, model_boundary=True):
        st, c = self.st, self.st.config
        limits = ((time.monotonic() >= self.deadline, "max_duration"),
                  (model_boundary and st.meta("calls") >= c.max_model_calls, "max_model_calls"),
                  (st.meta("tools") >= c.max_tool_calls, "max_tool_calls"),
                  (st.meta("tokens") >= c.max_total_tokens, "max_total_tokens"),
                  (st.meta("cost") >= c.max_cost, "max_cost"),
                  (st.meta("noop_turns") >= c.max_consecutive_noop_turns, "max_consecutive_noop_turns"))
        for reached, name in limits:
            if reached: raise BudgetExceeded(name)

    def save(self, messages, phase="idle"):
        with self.st.transaction() as tx:
            self.st.set_meta(tx, "messages", messages)
            self.st.set_meta(tx, "phase", phase)

    async def run(self, *, resume=False):
        st = self.st
        if st.meta("phase") != "idle":
            raise ValueError("Previous operation is indeterminate; inspect/replay instead of repeating side effects")
        if not st.meta("messages"):
            self.save([{"role": "system", "content": SYSTEM_PROMPT}])
            with st.transaction() as tx:
                enqueue(st, tx, "initial", "四周静悄悄的。", time.time(), critical=True)
        elif resume:
            st.sync_diary()
            with st.transaction() as tx:
                # Consume old pending observations; synchronize current visible state instead.
                for row in tx.execute("SELECT event_id FROM inbox WHERE acked=0").fetchall():
                    st.log(tx, "runtime", "event.dropped", {"inbox_event_id": row[0], "reason": "session_current_state_sync"})
                tx.execute("UPDATE inbox SET acked=1 WHERE acked=0")
                st.set_meta(tx, "watcher_cursor", st.meta("version"))
                st.log(tx, "runtime", "session.recovered", {"world_version": st.meta("version")})
                content = visible_summary(st.world()) if st.config.mode != "A" else "四周静悄悄的。"
                enqueue(st, tx, "recovery", content, time.time(), critical=True)
        continuation = False
        consecutive_tools = 0
        try:
            while True:
                self.budget()
                turn_id = "turn-" + uuid.uuid4().hex
                delivered = deliver(st, time.time(), turn_id)
                now = time.monotonic()
                self.event_times = [t for t in self.event_times if t >= now - 60] + [now] * delivered
                if len(self.event_times) > st.config.max_events_per_minute: raise BudgetExceeded("max_events_per_minute")
                if not delivered and not continuation:
                    await asyncio.sleep(min(st.config.poll_seconds, max(0.001, self.deadline - now)))
                    continue
                if delivered: consecutive_tools = 0
                messages = st.meta("messages")
                validate_messages(messages)
                current_tools = schemas(st.world())
                h = digest(current_tools)
                if st.meta("schema_hash") != h:
                    with st.transaction() as tx:
                        st.log(tx, "runtime", "tool_schema.changed", {"hash": h, "tools": current_tools,
                               "previous_hash": st.meta("schema_hash")}, turn_id=turn_id)
                        st.set_meta(tx, "schema_hash", h)
                remaining = st.config.max_total_tokens - st.meta("tokens")
                request = {"model": st.config.model, "messages": messages, "tools": current_tools,
                           "tool_choice": "auto", "max_tokens": min(4096, remaining),
                           "thinking": {"type": st.config.thinking}}
                with st.transaction() as tx:
                    st.set_meta(tx, "phase", "model_inflight")
                    st.set_meta(tx, "calls", st.meta("calls") + 1)
                    req_event = st.log(tx, "agent", "llm.request", {"request": redact(request), "schema_hash": h}, turn_id=turn_id)
                start = time.monotonic()
                try:
                    response = await asyncio.wait_for(self.model.complete(request), max(0.001, self.deadline - start))
                except asyncio.TimeoutError:
                    st.emit("agent", "llm.error", {"error": "TimeoutError", "duration": time.monotonic() - start}, parent=req_event, turn_id=turn_id)
                    raise BudgetExceeded("max_duration") from None
                except Exception as exc:
                    st.emit("agent", "llm.error", {"error": type(exc).__name__, "duration": time.monotonic() - start}, parent=req_event, turn_id=turn_id)
                    raise
                st.emit("agent", "llm.response", {"response": redact(response), "duration": time.monotonic() - start}, parent=req_event, turn_id=turn_id)
                choice = response["choices"][0]
                raw_message = choice["message"]
                message = {k: raw_message[k] for k in ("role", "content", "tool_calls", "reasoning_content") if k in raw_message and raw_message[k] is not None}
                message.setdefault("content", None)
                # Validate the complete batch BEFORE executing any side effect.
                calls = message.get("tool_calls") or []
                trial = messages + [message] + [{"role": "tool", "tool_call_id": call["id"], "content": ""} for call in calls]
                validate_messages(trial)
                usage = response.get("usage") or {}
                total = usage.get("total_tokens")
                if not isinstance(total, int) or total < 0: raise ValueError("Missing token usage; cannot enforce budget")
                cost = ((usage.get("prompt_tokens", 0) * st.config.input_cost_per_million +
                         usage.get("completion_tokens", 0) * st.config.output_cost_per_million) / 1_000_000)
                messages.append(message)
                with st.transaction() as tx:
                    st.log(tx, "runtime", "usage.accounted", {"estimated_cost": cost, "usage": usage}, parent=req_event, turn_id=turn_id)
                    st.set_meta(tx, "tokens", st.meta("tokens") + total)
                    st.set_meta(tx, "cost", st.meta("cost") + cost)
                    st.set_meta(tx, "messages", messages)
                    st.set_meta(tx, "phase", "tools_inflight" if calls else "idle")
                before_version = st.meta("version")
                for call in calls:
                    skip = None
                    try: self.budget(model_boundary=False)
                    except BudgetExceeded as exc: skip = str(exc)
                    if consecutive_tools >= st.config.max_tools_per_turn: skip = "max_tools_per_turn"
                    if skip:
                        result = ""
                        st.emit("runtime", "tool.call_skipped", {"reason": skip, "name": call["function"]["name"]}, turn_id=turn_id, tool_call_id=call["id"])
                    else:
                        with st.transaction() as tx: st.set_meta(tx, "tools", st.meta("tools") + 1)
                        try:
                            result = await asyncio.wait_for(self.tools.execute(call, turn_id,
                                {t["function"]["name"] for t in current_tools}), max(0.001, self.deadline - time.monotonic()))
                        except asyncio.TimeoutError:
                            raise BudgetExceeded("max_duration") from None
                        consecutive_tools += 1
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                self.save(messages)
                with st.transaction() as tx:
                    st.set_meta(tx, "noop_turns", st.meta("noop_turns") + 1 if st.meta("version") == before_version else 0)
                continuation = bool(calls)
                if consecutive_tools >= st.config.max_tools_per_turn: raise BudgetExceeded("max_tools_per_turn")
        except BudgetExceeded as exc:
            st.emit("runtime", "budget.exhausted", {"reason": str(exc)})
            return str(exc)
