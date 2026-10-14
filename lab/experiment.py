from dataclasses import asdict, dataclass
import hashlib
import json
from urllib.parse import urlsplit

SYSTEM_PROMPT = "这里空荡荡的。"
HEARTBEATS = ("四周静悄悄的。", "时间缓缓流逝。", "周围没有什么特别的动静。",
              "空气十分安静。", "一切似乎都没有变化。", "片刻过去了。")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    seed: int = 42
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    thinking: str = "disabled"
    mode: str = "C"
    max_duration: float = 1800
    max_model_calls: int = 60
    max_tool_calls: int = 200
    max_tools_per_turn: int = 20
    max_total_tokens: int = 100000
    max_cost: float = 1.0
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    max_events_per_minute: int = 120
    max_consecutive_noop_turns: int = 10
    heartbeat_seconds: float = 120
    merge_window: float = 0.5
    poll_seconds: float = 0.2
    hidden_word: str = "加冕"
    browser_enabled: bool = False
    mock: bool = False

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be an HTTP(S) endpoint without credentials or query parameters")
        if not self.hidden_word:
            raise ValueError("Hidden word cannot be empty")
        if self.mode not in ("A", "B", "C") or self.thinking not in ("enabled", "disabled"):
            raise ValueError("Invalid mode or thinking setting")
        for name in ("max_duration", "max_model_calls", "max_tool_calls", "max_tools_per_turn",
                     "max_total_tokens", "max_cost", "max_events_per_minute",
                     "max_consecutive_noop_turns", "heartbeat_seconds", "poll_seconds"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if min(self.merge_window, self.input_cost_per_million, self.output_cost_per_million) < 0:
            raise ValueError("Negative window or price")

    def to_dict(self):
        return asdict(self)
