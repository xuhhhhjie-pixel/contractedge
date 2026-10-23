def render(config, state=None):
    if not config["enabled"]:
        return {"name": config["default_name"], "mode": "stable"}
    if state is None:
        state = {"name": config["selected_name"]}
    # configuration reload leaves cached name stale
    return {"name": state["name"], "mode": "stable", "state": state}
