from __future__ import annotations

import json

from localdeploy.control import hardware


def _gpu(name: str, vendor: str, backend: str, total: int, free: int) -> dict:
    return hardware._gpu(name, vendor, backend, total, free, source="test")


def test_parse_rocm_smi_inventory() -> None:
    payload = {
        "card0": {
            "Card Series": "AMD Radeon RX 7900 XTX",
            "VRAM Total Memory (B)": 24 * 1024**3,
            "VRAM Total Used Memory (B)": 2 * 1024**3,
            "Driver version": "6.2",
        }
    }
    rows = hardware._parse_amd_json(payload, "rocm-smi")
    assert len(rows) == 1
    assert rows[0]["vendor"] == "AMD"
    assert rows[0]["backend"] == "ROCm"
    assert rows[0]["vram_total_mb"] == 24 * 1024
    assert rows[0]["vram_free_mb"] == 22 * 1024


def test_windows_intel_arc_detection(monkeypatch) -> None:
    payload = {
        "Name": "Intel(R) Arc(TM) A770 Graphics",
        "AdapterRAM": 16 * 1024**3,
        "DriverVersion": "32.0.101",
    }
    monkeypatch.setattr(hardware.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "powershell.exe")
    monkeypatch.setattr(hardware, "_run", lambda *args, **kwargs: json.dumps(payload))
    rows = hardware._query_windows_adapters({})
    assert rows[0]["vendor"] == "Intel"
    assert rows[0]["backend"] == "SYCL"
    assert rows[0]["unified_memory"] is False
    assert rows[0]["vram_estimated"] is True


def test_same_name_cards_are_preserved_and_compatible_memory_is_summed() -> None:
    rows = [
        _gpu("RTX 4090", "NVIDIA", "CUDA", 24_000, 22_000),
        _gpu("RTX 4090", "NVIDIA", "CUDA", 24_000, 21_000),
    ]
    deduped = hardware._dedupe_gpus(rows)
    summary = hardware.summarize_gpus(deduped)
    assert len(deduped) == 2
    assert summary["best_pool_free_mb"] == 43_000


def test_mixed_vendor_memory_is_not_summed() -> None:
    summary = hardware.summarize_gpus(
        [
            _gpu("NVIDIA", "NVIDIA", "CUDA", 12_000, 10_000),
            _gpu("AMD", "AMD", "ROCm", 16_000, 14_000),
        ]
    )
    assert summary["best_pool_free_mb"] == 14_000
    assert len(summary["compatible_groups"]) == 2


def test_multi_gpu_placement_uses_only_compatible_pool() -> None:
    rows = [
        _gpu("A", "NVIDIA", "CUDA", 8_192, 8_192),
        _gpu("B", "NVIDIA", "CUDA", 8_192, 8_192),
        _gpu("C", "AMD", "ROCm", 8_192, 8_192),
    ]
    placement = hardware.estimate_gpu_placement(12, rows)
    assert placement["mode"] == "multi_gpu_split"
    assert placement["vendor"] == "NVIDIA"
    assert placement["gpu_indexes"] == [0, 1]
    assert sum(item["memory_mb"] for item in placement["allocations"]) == 12 * 1024
