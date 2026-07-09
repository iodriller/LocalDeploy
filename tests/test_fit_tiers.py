"""Phase 3 — tiered soft/hard deployability warnings."""
from __future__ import annotations

from localdeploy.control.fit import _classify


def test_comfortable_is_ok_and_fits():
    out = _classify(required_gb=4.0, free_vram_gb=8.0, ram_available_gb=16.0)
    assert out["tier"] == "comfortable"
    assert out["severity"] == "ok"
    assert out["verdict"] == "FITS"  # backward-compatible coarse verdict


def test_tight_is_soft_but_still_fits():
    out = _classify(required_gb=7.6, free_vram_gb=8.0, ram_available_gb=16.0)
    assert out["tier"] == "tight"
    assert out["severity"] == "soft"
    assert out["verdict"] == "FITS"  # tight still fits VRAM -> not blocked


def test_cpu_only_when_gpu_too_small_but_ram_ok():
    out = _classify(required_gb=20.0, free_vram_gb=8.0, ram_available_gb=32.0)
    assert out["tier"] == "cpu_only"
    assert out["severity"] == "soft"
    assert out["cpu_deployable"] is True
    assert out["verdict"] == "WONT_FIT"  # coarse verdict unchanged (won't fit VRAM)


def test_hard_when_too_big_for_gpu_and_ram():
    out = _classify(required_gb=40.0, free_vram_gb=8.0, ram_available_gb=32.0)
    assert out["tier"] == "wont_fit"
    assert out["severity"] == "hard"
    assert out["cpu_deployable"] is False
    assert out["verdict"] == "WONT_FIT"


def test_no_gpu_but_fits_ram_is_cpu_soft():
    out = _classify(required_gb=10.0, free_vram_gb=None, ram_available_gb=32.0)
    assert out["tier"] == "cpu_only"
    assert out["severity"] == "soft"
    assert out["verdict"] == "UNKNOWN"  # no VRAM figure -> coarse verdict stays UNKNOWN


def test_no_gpu_and_too_big_for_ram_is_hard():
    out = _classify(required_gb=40.0, free_vram_gb=None, ram_available_gb=8.0)
    assert out["tier"] == "cpu_too_big"
    assert out["severity"] == "hard"
    assert out["verdict"] == "UNKNOWN"


def test_nothing_known_is_unknown():
    out = _classify(required_gb=10.0, free_vram_gb=None, ram_available_gb=None)
    assert out["tier"] == "unknown"
    assert out["severity"] == "unknown"
    assert out["verdict"] == "UNKNOWN"


def test_fit_check_response_carries_new_fields(monkeypatch):
    # Full endpoint returns the enriched fields alongside the legacy verdict.
    from localdeploy.control import fit as fit_mod

    monkeypatch.setattr(
        fit_mod,
        "detect_hardware",
        lambda: {"gpu_available": True, "gpus": [{"vram_free_mb": 8192}], "system": {"ram_available_mb": 32768}},
    )
    res = fit_mod.fit_check(fit_mod.FitRequest(params_b=7, quant="q4", context=4096))
    for key in ("verdict", "tier", "severity", "headline", "cpu_deployable", "ram_available_gb"):
        assert key in res
