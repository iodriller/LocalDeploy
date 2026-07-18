"""Phase B tests: tok/s in summary, per-category rollup, tok/s in compare."""
from __future__ import annotations

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI TestClient requires httpx", allow_module_level=True)

from api_server import app
from localdeploy.control.report import _category_summary, _summary, build_card, render_html, render_md

client = TestClient(app)


def _tests() -> list:
    return [
        {"name": "p1", "category": "planning", "success": True, "accuracy": 0.9,
         "elapsed_seconds": 1.0, "approx_tokens_per_second": 40.0},
        {"name": "c1", "category": "code", "success": True, "accuracy": 0.5,
         "elapsed_seconds": 2.0, "approx_tokens_per_second": 20.0},
        {"name": "c2", "category": "code", "success": False, "accuracy": 0.0,
         "elapsed_seconds": 0.5, "approx_tokens_per_second": None},
    ]


# ---- summary: avg tok/s -----------------------------------------------------

def test_summary_includes_avg_tokens_per_second() -> None:
    s = _summary(_tests())
    # Only the two tests with a measured rate count: (40 + 20) / 2 = 30.0
    assert s["avg_tokens_per_second"] == 30.0


def test_summary_tps_none_when_no_rates() -> None:
    s = _summary([{"name": "x", "category": "a", "success": True, "accuracy": 1.0,
                   "elapsed_seconds": 1.0}])
    assert s["avg_tokens_per_second"] is None


# ---- category rollup --------------------------------------------------------

def test_category_summary_groups_by_category() -> None:
    cats = {c["category"]: c for c in _category_summary(_tests())}
    assert set(cats) == {"planning", "code"}
    assert cats["planning"]["passed"] == 1
    assert cats["planning"]["tests"] == 1
    # code: 1 of 2 passed; avg accuracy over both = (0.5 + 0.0)/2 = 0.25
    assert cats["code"]["passed"] == 1
    assert cats["code"]["tests"] == 2
    assert cats["code"]["avg_accuracy"] == 0.25
    # avg latency only over successful code test = 2.0
    assert cats["code"]["avg_latency_s"] == 2.0


def test_build_card_embeds_category_summary() -> None:
    card = build_card({"profile": "p", "model_id": "m", "tests": _tests()})
    assert "category_summary" in card
    assert {c["category"] for c in card["category_summary"]} == {"planning", "code"}


def test_build_card_preserves_provenance_and_variance() -> None:
    payload = {
        "profile": "p",
        "tests": _tests(),
        "repetitions": 3,
        "provenance": {"profiles": {"p": {"model_digest": "sha256:full", "quant": "Q4_K_M"}}},
        "variance": {"latency_stdev_seconds": 0.2, "tokens_per_second_stdev": 1.5},
    }
    card = build_card(payload)
    assert card["version"] == 2
    assert card["repetitions"] == 3
    assert card["provenance"]["profiles"]["p"]["model_digest"] == "sha256:full"
    assert "latency σ 0.2s" in render_md(card)


def test_build_card_id_is_stable_for_same_content() -> None:
    # Re-exporting (or re-importing) the same completed run must yield the same
    # id both times, or the client's history dedup (which matches on id) can
    # never catch a duplicate import.
    payload = {"profile": "p", "model_id": "m", "device": "gpu", "tests": _tests()}
    card1 = build_card(payload)
    card2 = build_card(dict(payload))
    assert card1["id"]
    assert card1["id"] == card2["id"]


def test_build_card_id_differs_for_different_content() -> None:
    card_a = build_card({"profile": "p", "model_id": "m", "tests": _tests()})
    card_b = build_card({"profile": "p", "model_id": "m", "tests": _tests()[:1]})
    assert card_a["id"] != card_b["id"]


def test_build_card_id_passthrough_when_provided() -> None:
    card = build_card({"profile": "p", "model_id": "m", "tests": _tests(), "id": "run-explicit-123"})
    assert card["id"] == "run-explicit-123"


# ---- rendering --------------------------------------------------------------

def test_render_html_shows_tps_and_categories() -> None:
    card = build_card({"profile": "p", "model_id": "m", "tests": _tests()})
    html = render_html(card)
    assert "tok/s" in html
    assert "By category" in html
    assert "30.0 tok/s" in html  # aggregate in the summary line


def test_render_md_shows_tps_and_categories() -> None:
    card = build_card({"profile": "p", "model_id": "m", "tests": _tests()})
    md = render_md(card)
    assert "tok/s" in md
    assert "## By category" in md


def test_render_html_no_tps_label_when_absent() -> None:
    card = build_card({"profile": "p", "model_id": "m", "tests": [
        {"name": "x", "category": "a", "success": True, "accuracy": 1.0, "elapsed_seconds": 1.0},
    ]})
    html = render_html(card)
    assert "tok/s</th>" in html  # column header always present
    assert "tok/s</p>" not in html  # but no aggregate label in the summary line


# ---- compare: tok/s deltas --------------------------------------------------

def test_compare_includes_tps_delta() -> None:
    card_a = build_card({"profile": "m", "model_id": "m", "device": "gpu", "tests": [
        {"name": "t1", "category": "code", "success": True, "accuracy": 1.0,
         "elapsed_seconds": 1.0, "approx_tokens_per_second": 60.0},
    ]})
    card_b = build_card({"profile": "m", "model_id": "m", "device": "cpu", "tests": [
        {"name": "t1", "category": "code", "success": True, "accuracy": 1.0,
         "elapsed_seconds": 6.0, "approx_tokens_per_second": 10.0},
    ]})
    resp = client.post("/benchmark/compare", json={"card_a": card_a, "card_b": card_b}).json()
    assert resp["success"] is True
    row = resp["tests"][0]
    assert row["tps_a"] == 60.0
    assert row["tps_b"] == 10.0
    assert row["tps_delta"] == -50.0
    sd = resp["summary_delta"]
    assert sd["tps_a"] == 60.0
    assert sd["tps_b"] == 10.0
    assert sd["avg_tokens_per_second"] == -50.0


def test_compare_tps_none_when_cards_lack_rates() -> None:
    card = build_card({"profile": "m", "model_id": "m", "tests": [
        {"name": "t1", "category": "code", "success": True, "accuracy": 1.0, "elapsed_seconds": 1.0},
    ]})
    resp = client.post("/benchmark/compare", json={"card_a": card, "card_b": card}).json()
    assert resp["tests"][0]["tps_delta"] is None
    assert resp["summary_delta"]["avg_tokens_per_second"] is None
