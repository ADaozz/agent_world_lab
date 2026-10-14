from .world.definitions import DESCRIPTIONS, visible


def schemas(world):
    tools = []
    for name, description in DESCRIPTIONS.items():
        if not visible(world, name): continue
        arg = "content" if name == "diary" else "action"
        tools.append({"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": {arg: {"type": "string"}},
                           "required": [arg], "additionalProperties": False}}})
    return tools
