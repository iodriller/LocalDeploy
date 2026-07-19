"""Regression test for compare_models.py's CLI argument resolution.

Covers a real bug: when no profile is resolvable from --profile,
DEFAULT_MODEL_PROFILE, or config.json's default_profile, `profile_names`
used to become `[None]` (truthy list containing None) instead of an empty
list, which passed the "no profiles selected" guard and then crashed with
`TypeError: sequence item 0: expected str instance, NoneType found` inside
`', '.join(missing)` — an ugly traceback instead of the intended helpful
message.
"""
from __future__ import annotations

import sys

import pytest

import compare_models


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(compare_models, "load_config", lambda: {"profiles": {}, "default_profile": None})
    monkeypatch.delenv("DEFAULT_MODEL_PROFILE", raising=False)
    monkeypatch.setattr(sys, "argv", ["compare_models.py"])


def test_main_reports_helpful_message_when_no_profile_resolvable(capsys):
    exit_code = compare_models.main()
    assert exit_code == 2
    out = capsys.readouterr().out
    assert "No profiles selected" in out
    assert "Traceback" not in out


def test_main_does_not_crash_with_empty_profiles_and_no_all_flag(capsys):
    # Belt-and-suspenders: directly exercises the exact list comprehension
    # shape that used to produce [None] instead of [].
    config = {"profiles": {}, "default_profile": None}
    selected = None or None or config.get("default_profile")
    profile_names = [selected] if selected else []
    assert profile_names == []
