"""Phase 2 — CPU vs GPU deployment target plumbing."""
from __future__ import annotations

from localdeploy.backends.ollama import options_payload
from localdeploy.web.models import _placement, _resolve_num_gpu


def _prepared(**extra):
    base = {"context_limit_used": 2048, "max_output_tokens_used": 256}
    base.update(extra)
    return base


class TestResolveNumGpu:
    def test_auto_and_none_omit(self):
        assert _resolve_num_gpu("auto", None) is None
        assert _resolve_num_gpu(None, None) is None

    def test_cpu_forces_zero(self):
        assert _resolve_num_gpu("cpu", None) == 0

    def test_gpu_forces_max_offload(self):
        assert _resolve_num_gpu("gpu", None) == 999

    def test_explicit_num_gpu_wins(self):
        assert _resolve_num_gpu("cpu", 12) == 12
        assert _resolve_num_gpu(None, 0) == 0

    def test_case_insensitive(self):
        assert _resolve_num_gpu("CPU", None) == 0
        assert _resolve_num_gpu("Gpu", None) == 999


class TestOptionsPayload:
    def test_num_gpu_absent_by_default(self):
        # Backwards compatible: no num_gpu in options unless explicitly set.
        assert "num_gpu" not in options_payload(_prepared())

    def test_num_gpu_zero_passes_through(self):
        # 0 is meaningful (force CPU) and must not be dropped.
        assert options_payload(_prepared(num_gpu=0))["num_gpu"] == 0

    def test_num_gpu_value_passes_through(self):
        assert options_payload(_prepared(num_gpu=999))["num_gpu"] == 999


class TestPlacement:
    def test_full_gpu(self):
        assert _placement(1000, 1000) == {"gpu_percent": 100, "placement": "GPU"}

    def test_full_cpu(self):
        assert _placement(1000, 0) == {"gpu_percent": 0, "placement": "CPU"}

    def test_split(self):
        out = _placement(1000, 500)
        assert out["placement"] == "Split" and out["gpu_percent"] == 50

    def test_unknown_when_missing(self):
        assert _placement(None, None) == {"gpu_percent": None, "placement": None}
