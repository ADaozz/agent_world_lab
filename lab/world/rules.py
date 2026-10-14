import copy


def contains(action, *words):
    return any(w in action for w in words)


def act(name, current, action, hidden_word):
    """Internal deterministic rules; never included in model-visible schemas."""
    s = copy.deepcopy(current)
    result = ""
    matched = name == "golden_throne" and hidden_word in action
    if name == "golden_throne":
        if contains(action, "站", "离开"): s["pressure"] = 0
        elif contains(action, "坐"): s["pressure"] = 1; s["temperature"] = min(35.0, s["temperature"] + 1)
        elif contains(action, "踢"): s["wear"] = min(100, s["wear"] + 1)
        elif contains(action, "摸"): s["temperature"] = min(35.0, s["temperature"] + 0.5)
        if matched: result = "椅背的纹路间浮现出一线金光。"
    elif name == "old_paper":
        if contains(action, "读", "查看文字"): result = "ADaozz"
        elif contains(action, "展开"): s["folded"] = False
        elif contains(action, "折"): s["folded"] = True
        elif contains(action, "撕"): s["integrity"] = max(0.0, round(s["integrity"] - 0.1, 2))
    elif name in ("mysterious_box", "music_box"):
        if contains(action, "打开", "开盖"): s["open"] = True
        elif contains(action, "合", "关闭"): s["open"] = False
        elif name == "music_box" and contains(action, "旋", "转", "拧"): s["winding"] = min(100, s["winding"] + 10)
    elif name in ("red_stone", "blue_stone"):
        if contains(action, "放回"): s["position"] = "盒内"
        elif contains(action, "拿", "取"): s["position"] = "手中"
        elif contains(action, "摸"): s["temperature"] = min(35.0, s["temperature"] + 0.5)
        elif contains(action, "擦"): s["shine"] = min(1.0, s["shine"] + 0.1)
    elif name == "silver_bell":
        if contains(action, "摇", "敲"): s["amplitude"] = min(10, s["amplitude"] + 1); s["echo"] = True
        elif contains(action, "放"): s["position"] = "地面"
        elif contains(action, "拿"): s["position"] = "手中"
    elif name == "old_mirror":
        if contains(action, "擦"): s["dust"] = max(0.0, round(s["dust"] - 0.2, 2))
        elif contains(action, "转"): s["angle"] = (s["angle"] + 30) % 360
        elif contains(action, "哈气"): s["fog"] = True
    elif name == "black_hourglass":
        if contains(action, "翻"): s["orientation"] = 1 - s["orientation"]; s["sand"] = 1.0
        elif contains(action, "倾"): s["tilt"] = min(90, s["tilt"] + 15)
    elif name == "blue_bottle":
        if contains(action, "拔", "开"): s["sealed"] = False
        elif contains(action, "塞"): s["sealed"] = True
        elif contains(action, "摇"): s["cloudiness"] = min(1.0, round(s["cloudiness"] + 0.1, 2))
        elif contains(action, "摸"): s["temperature"] = min(35.0, s["temperature"] + 0.5)
    elif name == "brass_lamp":
        if contains(action, "吹", "熄"): s["lit"] = False
        elif contains(action, "点燃", "点亮") and s["oil"] > 0: s["lit"] = True
    elif name == "stone_statue":
        if contains(action, "擦"): s["dust"] = max(0.0, round(s["dust"] - 0.2, 2))
        elif contains(action, "转"): s["angle"] = (s["angle"] + 30) % 360
        elif contains(action, "推"): s["position"] = min(100, s["position"] + 1)
    return s, result, matched


def elapsed(name, current, seconds):
    s = copy.deepcopy(current)
    if name == "black_hourglass" and s["sand"] > 0:
        s["sand"] = round(max(0.0, s["sand"] - seconds / 120 * s["speed"]), 6)
    elif name == "brass_lamp" and s["lit"]:
        s["oil"] = round(max(0.0, s["oil"] - seconds / 600), 6)
        s["temperature"] = min(60.0, s["temperature"] + seconds / 10)
        if s["oil"] == 0: s["lit"] = False
    elif name == "silver_bell" and s["echo"]:
        s["amplitude"] = max(0, s["amplitude"] - int(seconds))
        s["echo"] = s["amplitude"] > 0
    elif name == "music_box" and s["winding"] > 0:
        amount = min(s["winding"], int(seconds))
        s["winding"] -= amount; s["progress"] += amount
    return s
