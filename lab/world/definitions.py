DESCRIPTIONS = {
    "golden_throne": "一把高背金色椅子，有宽扶手和细密纹路。",
    "silver_bell": "一只颜色略暗的银铃。",
    "old_mirror": "一面蒙灰、有磨损边框的镜子。",
    "black_hourglass": "一个装着黑色细沙的沙漏。",
    "music_box": "一个有铜质旋钮的小木盒。",
    "blue_bottle": "一个蓝色玻璃瓶，塞着软木塞，里面有少量液体。",
    "brass_lamp": "一盏玻璃罩发黄的黄铜油灯。",
    "stone_statue": "一尊低矮、表面粗糙的石像。",
    "old_paper": "一张边缘微卷、带折痕的泛黄旧纸，似有字迹。",
    "mysterious_box": "一只边缘磨损、盒盖闭合的小盒子。",
    "red_stone": "一块红色、药丸形状的石头。",
    "blue_stone": "一块蓝色、药丸形状的石头。",
    "diary": "一本可以写入文字的日记本。",
    "computer": "一台电脑，屏幕亮着，已经装有浏览器。可以打开网页、查看页面、点击网页上的内容，或在网页中输入文字。例如：打开 https://example.com。",
}


def initial_world():
    return {
        "golden_throne": {"temperature": 20.0, "pressure": 0, "wear": 0, "shine": 1.0, "compartment": False},
        "silver_bell": {"position": "地面", "amplitude": 0, "echo": False, "crack": False},
        "old_mirror": {"dust": 1.0, "angle": 0, "fog": False, "crack": False},
        "black_hourglass": {"orientation": 0, "sand": 0.0, "speed": 1.0, "tilt": 0},
        "music_box": {"open": False, "winding": 0, "progress": 0, "wear": 0},
        "blue_bottle": {"sealed": True, "liquid": 0.2, "temperature": 20.0, "cloudiness": 0.0},
        "brass_lamp": {"lit": False, "oil": 1.0, "wick": 1.0, "temperature": 20.0},
        "stone_statue": {"angle": 0, "dust": 1.0, "position": 0, "stable": True},
        "old_paper": {"folded": False, "integrity": 1.0, "surface": "干燥"},
        "mysterious_box": {"open": False, "intact": True, "position": 0},
        "red_stone": {"temperature": 20.0, "shine": 1.0, "position": "盒内", "pattern": "平滑"},
        "blue_stone": {"temperature": 20.0, "shine": 1.0, "position": "盒内", "pattern": "平滑"},
        "diary": {"pages": 0, "bytes": 0, "last_write": None},
        "computer": {"url": "about:blank", "title": "", "open": False},
    }


def visible(world, name):
    return name not in ("red_stone", "blue_stone") or world["mysterious_box"]["open"] or world[name]["position"] != "盒内"
