from contractedge.experiments.modes import direct_plan_request
from contractedge.model.config import model_config, model_name
from contractedge.model.money import MoneyBudget


def test_nested_recorded_model_config_projects_to_wire_name():
    recorded = {"model": "deepseek-flash", "provider": "openai_compatible",
                "schema_in_prompt": True, "model_revision": "rev-1"}
    assert model_name(recorded) == "deepseek-flash"
    projected = model_config(recorded)
    assert projected["model"] == "deepseek-flash"
    assert projected["model_revision"] == "rev-1"
    assert model_config("deepseek-flash")["model"] == "deepseek-flash"


def test_direct_plan_request_uses_canonical_model_for_money_ledger(tmp_path):
    request = direct_plan_request(
        {"snapshot_id": "s", "input_digest": "i", "source_origin": "test",
         "permitted_source_inventory": [{"path": "main.go"}], "requirement": "fix"},
        {"model": {"model": "deepseek-flash", "provider": "openai_compatible"},
         "max_output_tokens": 16},
    )
    assert request.model == "deepseek-flash"
    ledger = MoneyBudget(tmp_path / "money.json", limit_cny="1", model="deepseek-flash",
                         input_cny_per_million="2", output_cny_per_million="8")
    reservation = ledger.reserve(request.model, 1, 1)
    assert reservation
