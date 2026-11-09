"""continuous_runner: budget override allowlist."""

import continuous_runner as cr

CFG = {"budget_override_keyword": "override budget",
       "budget_override_allowed_senders": ["@owner:example.org"]}


def test_override_from_allowlisted_sender():
    assert cr.is_budget_override(CFG, "@owner:example.org", "please OVERRIDE BUDGET")


def test_override_from_other_sender_ignored():
    assert not cr.is_budget_override(CFG, "@stranger:example.org", "override budget")


def test_override_disabled_with_empty_allowlist():
    cfg = dict(CFG, budget_override_allowed_senders=[])
    assert not cr.is_budget_override(cfg, "@owner:example.org", "override budget")


def test_no_keyword_no_override():
    assert not cr.is_budget_override(CFG, "@owner:example.org", "hello")


def test_env_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_BUDGET_OVERRIDE_USERS", "@a:x.org, @b:x.org")
    cfg = cr.load_runner_config(str(tmp_path / "missing.json"))
    assert cfg["budget_override_allowed_senders"] == ["@a:x.org", "@b:x.org"]
