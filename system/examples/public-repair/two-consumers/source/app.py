def render(config, state=None):
    primary = config["default_name"]
    secondary = config["default_name"]
    if config["enabled"]:
        primary = "default"
        secondary = "default"
    return {"primary": primary, "secondary": secondary, "mode": "stable"}
