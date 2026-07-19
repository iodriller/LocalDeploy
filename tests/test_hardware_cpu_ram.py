"""Phase 1 - hardware probe now reports CPU model, cores, and RAM."""
from __future__ import annotations

from localdeploy.control.hardware import _cpu_and_memory, detect_hardware


def test_system_block_has_cpu_and_ram_keys():
    sys = detect_hardware()["system"]
    # The contract: these keys always exist (values may be None if psutil absent).
    for key in (
        "cpu_model",
        "logical_cores",
        "physical_cores",
        "ram_total_mb",
        "ram_available_mb",
        "psutil_available",
        "ram_probe_message",
    ):
        assert key in sys


def test_detect_hardware_never_raises_and_is_well_formed():
    hw = detect_hardware()
    assert hw["success"] is True
    assert isinstance(hw["gpus"], list)
    assert "system" in hw


def test_cpu_and_memory_logical_cores_populated():
    info = _cpu_and_memory()
    # logical_cores comes from os.cpu_count(); should be a positive int on any CI host.
    assert info["logical_cores"] is None or info["logical_cores"] >= 1


def test_psutil_path_populates_ram_when_available():
    info = _cpu_and_memory()
    try:
        import psutil  # noqa: F401
    except Exception:
        return  # psutil not installed - RAM legitimately None; nothing to assert
    assert info["ram_total_mb"] is not None and info["ram_total_mb"] > 0
    assert info["physical_cores"] is None or info["physical_cores"] >= 1
