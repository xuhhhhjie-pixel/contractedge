def render(config, state=None):
    name = config["default_name"]
    if config["enabled"]:
        name = "default"
    return {"name": name, "mode": "stable"}
