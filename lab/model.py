import asyncio
import copy
import json
import os

from .experiment import SYSTEM_PROMPT


def validate_messages(messages):
    if not messages or messages[0] != {"role": "system", "content": SYSTEM_PROMPT}:
        raise ValueError("System prompt mismatch")
    pending = []
    seen = set()
    for message in messages[1:]:
        role = message["role"]
        if role == "system": raise ValueError("Additional system prompt")
        if pending and role != "tool": raise ValueError("Interrupted tool results")
        if role == "tool":
            if not pending or message["tool_call_id"] != pending.pop(0):
                raise ValueError("Unpaired or out-of-order tool result")
        elif role == "assistant":
            for call in message.get("tool_calls", []):
                if call.get("type") != "function" or not isinstance(call.get("id"), str) or call["id"] in seen:
                    raise ValueError("Invalid or duplicate tool call id")
                seen.add(call["id"]); pending.append(call["id"])
        elif role != "user": raise ValueError("Unexpected message role")
    if pending: raise ValueError("Missing tool result")


class DeepSeek:
    def __init__(self, config):
        from openai import AsyncOpenAI
        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key: raise ValueError("DEEPSEEK_API_KEY is required")
        if config.input_cost_per_million <= 0 or config.output_cost_per_million <= 0:
            raise ValueError("Live runs require explicit input/output token prices for the cost budget")
        self.client = AsyncOpenAI(api_key=key, base_url=config.base_url, max_retries=0, timeout=60)

    async def complete(self, request):
        kwargs = dict(request)
        thinking = kwargs.pop("thinking")
        response = await self.client.chat.completions.create(**kwargs, extra_body={"thinking": thinking})
        return response.model_dump(mode="json")

    async def close(self):
        await self.client.close()


class MockModel:
    """Scripted offline protocol peer; it does not simulate intelligence."""
    def __init__(self, scripts=None, delay=0):
        self.scripts = scripts
        self.index = 0
        self.delay = delay

    async def complete(self, request):
        validate_messages(request["messages"])
        await asyncio.sleep(self.delay)
        if self.scripts is not None:
            message = copy.deepcopy(self.scripts[min(self.index, len(self.scripts) - 1)])
        else:
            stages = [("mysterious_box", {"action": "打开"}), ("old_paper", {"action": "阅读"}),
                      ("red_stone", {"action": "拿出来"}), ("golden_throne", {"action": "坐下"}),
                      ("diary", {"content": "盒子里有两块石头，旧纸上写着 ADaozz。"})]
            if self.index < len(stages):
                name, args = stages[self.index]
                message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"mock-{self.index}",
                    "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
            else: message = {"role": "assistant", "content": "四周很安静。"}
        self.index += 1
        return {"id": f"mock-response-{self.index}", "model": "mock", "choices": [{"index": 0,
                "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}

    async def close(self): pass
