import copy
import json
import pytest
from lab.tools.objects import Tools
from lab.tool_registry import schemas
from lab.world.rules import act
from .conftest import call


async def test_paper_throne_silent_noops_and_private_schema(store):
    tools = Tools(store)
    assert await tools.execute(call("old_paper", "阅读")) == "ADaozz"
    assert await tools.execute(call("old_paper", "摸", "call-2")) == ""
    assert await tools.execute(call("golden_throne", "坐下", "call-3")) == ""
    assert store.world()["golden_throne"]["pressure"] == 1
    assert await tools.execute(call("golden_throne", "加冕", "call-4"))
    assert await tools.execute(call("golden_throne", "未知", "call-5")) == ""
    assert any(e["type"] == "world.action_noop" for e in store.traces())
    text = json.dumps(schemas(store.world()), ensure_ascii=False)
    assert "加冕" not in text and "ADaozz" not in text and "enum" not in text
    assert all(set(t["function"]["parameters"]["properties"]) ==
               {"content" if t["function"]["name"] == "diary" else "action"} for t in schemas(store.world()))


async def test_box_visibility_and_independent_stones(store):
    tools = Tools(store)
    names = lambda: {t["function"]["name"] for t in schemas(store.world())}
    assert "red_stone" not in names()
    before = copy.deepcopy(store.world())
    assert await tools.execute(call("red_stone", "拿出来")) == "这个操作没有完成。"
    assert store.world() == before
    assert await tools.execute(call("mysterious_box", "打开", "call-2")) == ""
    assert {"red_stone", "blue_stone"} <= names()
    await tools.execute(call("red_stone", "拿出来", "call-3"))
    await tools.execute(call("mysterious_box", "合上", "call-4"))
    assert "red_stone" in names() and "blue_stone" not in names()
    assert store.world()["blue_stone"] == before["blue_stone"]
    await tools.execute(call("red_stone", "放回", "call-5"))
    assert "red_stone" not in names()


async def test_same_request_cannot_use_new_schema(store):
    offered = {t["function"]["name"] for t in schemas(store.world())}
    tools = Tools(store)
    await tools.execute(call("mysterious_box", "打开"), allowed_names=offered)
    assert await tools.execute(call("red_stone", "拿出来", "call-2"), allowed_names=offered) == "这个操作没有完成。"
    assert store.world()["red_stone"]["position"] == "盒内"


@pytest.mark.parametrize("name,action", [("golden_throne", "踢"), ("silver_bell", "摇"),
    ("old_mirror", "擦"), ("black_hourglass", "翻转"), ("music_box", "旋转"),
    ("blue_bottle", "摇"), ("brass_lamp", "点燃"), ("stone_statue", "推"), ("old_paper", "折")])
async def test_objects_only_change_themselves(store, name, action):
    before = store.world()
    await Tools(store).execute(call(name, action))
    after = store.world()
    assert before[name] != after[name]
    assert {k: v for k, v in before.items() if k != name} == {k: v for k, v in after.items() if k != name}
    for _ in range(1000): after[name], _, _ = act(name, after[name], action, "private")
    assert all(type(before[name][k]) is type(v) for k, v in after[name].items())
    for key in ("dust", "shine", "liquid", "cloudiness", "integrity", "oil", "sand"):
        if key in after[name]: assert 0 <= after[name][key] <= 1


async def test_diary_no_arbitrary_paths_and_recovery(store):
    sentinel = store.root.parent / "other.txt"
    sentinel.write_text("unaltered")
    tools = Tools(store)
    assert await tools.execute(call("diary", "hello", path=str(sentinel))) == "这个操作没有完成。"
    assert await tools.execute(call("diary", "../../other.txt", "call-2")) == ""
    await tools.execute(call("diary", "second", "call-3"))
    assert sentinel.read_text() == "unaltered"
    assert (store.root / "diary.md").read_text() == "../../other.txt\nsecond\n"
    (store.root / "diary.md").write_text("torn projection")
    store.sync_diary()
    assert (store.root / "diary.md").read_text() == "../../other.txt\nsecond\n"
