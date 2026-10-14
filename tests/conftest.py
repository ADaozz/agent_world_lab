import json
import pytest
from lab.experiment import Config
from lab.storage import Store


@pytest.fixture
def store(tmp_path):
    st = Store.create(tmp_path, Config(mock=True, max_duration=3, max_model_calls=10, merge_window=0))
    yield st
    st.close()


def call(name, action, cid="call-1", **extra):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(
        {("content" if name == "diary" else "action"): action, **extra}, ensure_ascii=False)}}
