"""Tests for the estimated-vs-observed VRAM calibration store (Release R2)."""
from __future__ import annotations

from localdeploy.control import calibration


def _isolate_store(monkeypatch, tmp_path):
    path = tmp_path / "calibration.json"
    monkeypatch.setattr(calibration, "_store_path", lambda: path)
    return path


def test_no_samples_means_no_correction(monkeypatch, tmp_path):
    _isolate_store(monkeypatch, tmp_path)
    corr = calibration.get_correction(gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q4_K_M", context=4096)
    assert corr["applied"] is False
    assert corr["factor"] == 1.0
    assert corr["sample_count"] == 0
    assert corr["confidence"] == "none"


def test_record_and_get_correction_median(monkeypatch, tmp_path):
    _isolate_store(monkeypatch, tmp_path)
    key = dict(gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q4_K_M", context=4096)
    calibration.record_sample(**key, estimated_gb=10.0, observed_gb=11.0)  # ratio 1.10
    calibration.record_sample(**key, estimated_gb=10.0, observed_gb=10.8)  # ratio 1.08
    calibration.record_sample(**key, estimated_gb=10.0, observed_gb=11.2)  # ratio 1.12
    corr = calibration.get_correction(**key)
    assert corr["applied"] is True
    assert corr["sample_count"] == 3
    assert corr["factor"] == 1.10  # median of [1.08, 1.10, 1.12]
    assert corr["confidence"] == "medium"  # >= 3 samples


def test_confidence_rises_with_sample_count(monkeypatch, tmp_path):
    _isolate_store(monkeypatch, tmp_path)
    key = dict(gpu="amd:rocm:rx-7900", runtime="ollama", family="gemma3", quant="Q4_K_M", context=8192)
    for _ in range(2):
        calibration.record_sample(**key, estimated_gb=8.0, observed_gb=8.4)
    assert calibration.get_correction(**key)["confidence"] == "low"
    for _ in range(3):
        calibration.record_sample(**key, estimated_gb=8.0, observed_gb=8.4)
    assert calibration.get_correction(**key)["confidence"] == "medium"
    for _ in range(6):
        calibration.record_sample(**key, estimated_gb=8.0, observed_gb=8.4)
    assert calibration.get_correction(**key)["confidence"] == "high"


def test_different_context_buckets_do_not_mix(monkeypatch, tmp_path):
    _isolate_store(monkeypatch, tmp_path)
    calibration.record_sample(
        gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q4_K_M", context=4096,
        estimated_gb=10.0, observed_gb=11.0,
    )
    corr_32k = calibration.get_correction(
        gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q4_K_M", context=32768
    )
    assert corr_32k["applied"] is False  # a 4K sample must not answer a 32K question


def test_different_family_or_quant_does_not_mix(monkeypatch, tmp_path):
    _isolate_store(monkeypatch, tmp_path)
    calibration.record_sample(
        gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q4_K_M", context=4096,
        estimated_gb=10.0, observed_gb=11.0,
    )
    assert calibration.get_correction(
        gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="llama3.1", quant="Q4_K_M", context=4096
    )["applied"] is False
    assert calibration.get_correction(
        gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q8_0", context=4096
    )["applied"] is False


def test_stats_reports_mean_absolute_error(monkeypatch, tmp_path):
    _isolate_store(monkeypatch, tmp_path)
    assert calibration.stats() == {"keys": 0, "samples": 0, "mean_abs_error_pct": None}
    calibration.record_sample(
        gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q4_K_M", context=4096,
        estimated_gb=10.0, observed_gb=11.0,  # 10% error
    )
    calibration.record_sample(
        gpu="amd:rocm:rx-7900", runtime="ollama", family="gemma3", quant="Q4_K_M", context=4096,
        estimated_gb=8.0, observed_gb=8.0,  # 0% error
    )
    out = calibration.stats()
    assert out["keys"] == 2
    assert out["samples"] == 2
    assert out["mean_abs_error_pct"] == 5.0


def test_sample_cap_keeps_most_recent(monkeypatch, tmp_path):
    _isolate_store(monkeypatch, tmp_path)
    key = dict(gpu="nvidia:cuda:rtx-4090", runtime="ollama", family="qwen3", quant="Q4_K_M", context=4096)
    for i in range(60):
        calibration.record_sample(**key, estimated_gb=10.0, observed_gb=10.0 + i * 0.01)
    corr = calibration.get_correction(**key)
    assert corr["sample_count"] == calibration._MAX_SAMPLES_PER_KEY
    # The most recent sample (i=59) should have survived the trim.
    assert corr["last_sample_observed_gb"] == round(10.0 + 59 * 0.01, 3)


def test_gpu_key_falls_back_to_cpu_when_no_gpu():
    assert calibration.gpu_key({"gpus": []}) == "cpu"


def test_gpu_key_uses_vendor_backend_name():
    hw = {"gpus": [{"vendor": "NVIDIA", "backend": "CUDA", "name": "NVIDIA GeForce RTX 4090"}]}
    key = calibration.gpu_key(hw)
    assert key == "NVIDIA:CUDA:nvidia-geforce-rtx-4090"
