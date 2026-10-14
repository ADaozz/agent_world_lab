from .world.definitions import DESCRIPTIONS


def describe(name, before, after):
    if name == "mysterious_box" and before["open"] != after["open"]:
        if after["open"]:
            return "盒盖打开了。", False
        return "盒盖合上了。", False
    if name == "black_hourglass":
        if before["sand"] > 0 and after["sand"] == 0: return "沙漏中的黑沙已经流尽。", True
        if before["orientation"] != after["orientation"]: return "沙漏翻转了，黑沙缓缓落下。", False
        return None, False
    if name == "brass_lamp":
        if before["lit"] != after["lit"]: return ("油灯亮了。" if after["lit"] else "油灯熄灭了。"), False
        return None, False
    if name == "silver_bell":
        if after["amplitude"] > before["amplitude"]: return "银铃响了，余音轻轻散开。", False
        return None, False
    if name == "music_box":
        if after["winding"] > before["winding"]: return "小木盒里响起轻轻的乐声。", False
        if after["open"] != before["open"]: return ("小木盒打开了。" if after["open"] else "小木盒合上了。"), False
        return None, False
    if name == "old_mirror" and after["dust"] < before["dust"]: return "镜面上的灰尘少了一些。", False
    if name in ("red_stone", "blue_stone") and after["position"] != before["position"]:
        return ("红" if name == "red_stone" else "蓝") + "色石头" + ("回到了盒内。" if after["position"] == "盒内" else "被拿了出来。"), False
    if name == "blue_bottle" and after["sealed"] != before["sealed"]:
        return ("瓶口的软木塞被拔出了。" if not after["sealed"] else "瓶口重新塞住了。"), False
    if name == "old_paper" and after["folded"] != before["folded"]:
        return ("旧纸折了起来。" if after["folded"] else "旧纸展开了。"), False
    if name == "stone_statue" and after["position"] != before["position"]: return "石像移动了一点。", False
    if name == "golden_throne" and after["pressure"] != before["pressure"]:
        return ("金色椅子的座面微微下沉。" if after["pressure"] else "金色椅子的座面恢复了。"), False
    # No observation is invented for changes without a perceptible interpretation.
    return None, False


def visible_summary(world):
    parts = []
    for name in ("mysterious_box", "brass_lamp", "black_hourglass"):
        state = world[name]
        if name == "mysterious_box":
            parts.append("小盒子的盒盖" + ("开着。" if state["open"] else "闭合着。"))
        elif name == "brass_lamp": parts.append("油灯" + ("亮着。" if state["lit"] else "没有亮。"))
        elif state["sand"] > 0: parts.append("黑沙缓缓落下。")
    if world["mysterious_box"]["open"]:
        stones = [color for name, color in (("red_stone", "红"), ("blue_stone", "蓝")) if world[name]["position"] == "盒内"]
        if stones: parts.append("盒内有" + "、".join(stones) + "色的药丸形状石头。")
    for name, color in (("red_stone", "红"), ("blue_stone", "蓝")):
        if world[name]["position"] != "盒内": parts.append(color + "色药丸形状石头在盒外。")
    return "".join(parts)
