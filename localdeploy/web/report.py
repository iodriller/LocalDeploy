"""Step 13 (D1) - shareable, reproducible Report Cards + A/B compare.

POST /benchmark/export  -> a self-contained .html/.md card (model + hardware +
                           scores). The HTML embeds the card JSON so it can be
                           re-imported for comparison.
POST /benchmark/compare -> diff two cards (e.g. old model vs new, quant A vs B).
"""
from __future__ import annotations

import html as _html
import json
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Dict, List

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


def _summary(tests: List[Dict[str, Any]]) -> Dict[str, Any]:
    accs = [float(t.get("accuracy") or 0) for t in tests]
    lats = [float(t.get("elapsed_seconds") or 0) for t in tests]
    # Only successful tests with a measured rate contribute to avg tok/s.
    tps = [
        float(t["approx_tokens_per_second"])
        for t in tests
        if t.get("approx_tokens_per_second") is not None
    ]
    return {
        "tests": len(tests),
        "passed": sum(1 for t in tests if t.get("success")),
        "avg_accuracy": round(mean(accs), 3) if accs else 0.0,
        "avg_latency_s": round(mean(lats), 3) if lats else 0.0,
        "avg_tokens_per_second": round(mean(tps), 2) if tps else None,
    }


def _category_summary(tests: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-category rollup so a card shows strengths/weaknesses at a glance."""
    out: List[Dict[str, Any]] = []
    for cat in sorted({str(t.get("category") or "?") for t in tests}):
        subset = [t for t in tests if str(t.get("category") or "?") == cat]
        accs = [float(t.get("accuracy") or 0) for t in subset]
        lats = [float(t.get("elapsed_seconds") or 0) for t in subset if t.get("success")]
        out.append(
            {
                "category": cat,
                "tests": len(subset),
                "passed": sum(1 for t in subset if t.get("success")),
                "avg_accuracy": round(mean(accs), 3) if accs else 0.0,
                "avg_latency_s": round(mean(lats), 3) if lats else 0.0,
            }
        )
    return out


def build_card(payload: Dict[str, Any]) -> Dict[str, Any]:
    tests = payload.get("tests") or []
    return {
        "kind": "localdeploy.report_card",
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "profile": payload.get("profile"),
        "model_id": payload.get("model_id"),
        "device": payload.get("device") or None,
        "hardware": payload.get("hardware") or {},
        "tests": tests,
        "summary": payload.get("summary") or _summary(tests),
        "category_summary": payload.get("category_summary") or _category_summary(tests),
    }


def _device_suffix(card: Dict[str, Any]) -> str:
    d = card.get("device")
    return f" [{d.upper()}]" if d else ""


def render_md(card: Dict[str, Any]) -> str:
    s = card["summary"]
    hw = card.get("hardware") or {}
    hw_label = hw.get("gpu") or "CPU only"
    if hw.get("vram_total_mb"):
        hw_label += f" ({hw['vram_total_mb']} MB)"
    dev = _device_suffix(card)
    tps = s.get("avg_tokens_per_second")
    tps_label = f" · avg {tps} tok/s" if tps is not None else ""
    lines = [
        "# LocalDeploy Report Card",
        "",
        f"- Model: `{card.get('model_id') or card.get('profile') or '?'}`{dev}",
        f"- Profile: `{card.get('profile') or '?'}`",
        f"- Hardware: {hw_label}",
        f"- Generated: {card['generated_at']}",
        "",
        f"**{s['passed']}/{s['tests']} passed · avg accuracy {s['avg_accuracy']} · "
        f"avg latency {s['avg_latency_s']}s{tps_label}**",
        "",
    ]
    cat_rows = card.get("category_summary") or _category_summary(card["tests"])
    if cat_rows:
        lines += [
            "## By category",
            "",
            "| Category | Passed | Avg accuracy | Avg latency |",
            "|---|---:|---:|---:|",
        ]
        for c in cat_rows:
            lines.append(
                f"| {c['category']} | {c['passed']}/{c['tests']} | "
                f"{c['avg_accuracy']} | {c['avg_latency_s']}s |"
            )
        lines.append("")
    lines += [
        "## Per test",
        "",
        "| Test | Category | Result | Latency | tok/s | Accuracy |",
        "|---|---|---|---:|---:|---:|",
    ]
    for t in card["tests"]:
        res = "PASS" if t.get("success") else "FAIL"
        tps_cell = t.get("approx_tokens_per_second")
        lines.append(
            f"| {t.get('name')} | {t.get('category')} | {res} | "
            f"{t.get('elapsed_seconds')}s | {tps_cell if tps_cell is not None else '—'} | {t.get('accuracy')} |"
        )
    return "\n".join(lines) + "\n"


def render_html(card: Dict[str, Any]) -> str:
    s = card["summary"]
    hw = card.get("hardware") or {}

    def _tps_cell(value: Any) -> str:
        return _html.escape(str(value)) if value is not None else "—"

    rows = "".join(
        f"<tr><td>{_html.escape(str(t.get('name')))}</td>"
        f"<td>{_html.escape(str(t.get('category')))}</td>"
        f"<td class='{'pass' if t.get('success') else 'fail'}'>"
        f"{'PASS' if t.get('success') else 'FAIL'}</td>"
        f"<td>{_html.escape(str(t.get('elapsed_seconds')))}s</td>"
        f"<td>{_tps_cell(t.get('approx_tokens_per_second'))}</td>"
        f"<td>{_html.escape(str(t.get('accuracy')))}</td></tr>"
        for t in card["tests"]
    )
    cat_rows_data = card.get("category_summary") or _category_summary(card["tests"])
    cat_rows = "".join(
        f"<tr><td>{_html.escape(str(c['category']))}</td>"
        f"<td>{c['passed']}/{c['tests']}</td>"
        f"<td>{c['avg_accuracy']}</td>"
        f"<td>{c['avg_latency_s']}s</td></tr>"
        for c in cat_rows_data
    )
    cat_table = (
        "<h2>By category</h2><table><thead><tr><th>Category</th><th>Passed</th>"
        f"<th>Avg accuracy</th><th>Avg latency</th></tr></thead><tbody>{cat_rows}</tbody></table>"
        if cat_rows_data
        else ""
    )
    # The embedded JSON makes the card portable and re-importable for compare.
    data_json = _html.escape(json.dumps(card, ensure_ascii=False), quote=False)
    model = _html.escape(str(card.get("model_id") or card.get("profile") or "?"))
    dev = _html.escape(_device_suffix(card))
    tps = s.get("avg_tokens_per_second")
    tps_label = f" · avg {tps} tok/s" if tps is not None else ""
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>LocalDeploy Report Card</title><style>"
        "body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
        "background:#0f1115;color:#e6e8ec;max-width:780px;margin:2rem auto;padding:0 1rem}"
        "h1{font-size:1.3rem}h2{font-size:1rem;margin-top:1.5rem}"
        "table{width:100%;border-collapse:collapse;margin-top:1rem;font-size:.9rem}"
        "td,th{padding:.4rem .5rem;border-bottom:1px solid #2a2f3a;text-align:left}"
        "th{color:#9aa0a6}.pass{color:#34d399;font-weight:600}.fail{color:#f87171;font-weight:600}"
        ".meta{color:#9aa0a6}code{color:#8ab4f8}</style></head><body>"
        "<h1>LocalDeploy Report Card</h1>"
        f"<p class='meta'>Model <code>{model}</code>{dev} · Hardware "
        f"{_html.escape(str(hw.get('gpu') or 'CPU only'))} · {_html.escape(card['generated_at'])}</p>"
        f"<p><b>{s['passed']}/{s['tests']} passed</b> · avg accuracy {s['avg_accuracy']} · "
        f"avg latency {s['avg_latency_s']}s{tps_label}</p>"
        f"{cat_table}"
        "<h2>Per test</h2>"
        "<table><thead><tr><th>Test</th><th>Category</th><th>Result</th><th>Latency</th>"
        f"<th>tok/s</th><th>Accuracy</th></tr></thead><tbody>{rows}</tbody></table>"
        f"<script type='application/json' id='localdeploy-card'>{data_json}</script>"
        "</body></html>"
    )


@router.post("/benchmark/export")
async def benchmark_export(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        return {"success": False, "error": f"invalid JSON body: {exc}"}
    if not isinstance(payload, dict):
        return {"success": False, "error": "expected a JSON object"}
    card = build_card(payload)
    return {"success": True, "card": card, "html": render_html(card), "md": render_md(card)}


class CompareRequest(BaseModel):
    card_a: Dict[str, Any]
    card_b: Dict[str, Any]


def _delta(a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    return round(float(b) - float(a), 3)


@router.post("/benchmark/compare")
def benchmark_compare(req: CompareRequest) -> Dict[str, Any]:
    a = {t.get("name"): t for t in (req.card_a.get("tests") or [])}
    b = {t.get("name"): t for t in (req.card_b.get("tests") or [])}
    names = list(dict.fromkeys(list(a) + list(b)))
    rows = []
    for name in names:
        ta, tb = a.get(name), b.get(name)
        rows.append(
            {
                "name": name,
                "accuracy_a": ta.get("accuracy") if ta else None,
                "accuracy_b": tb.get("accuracy") if tb else None,
                "accuracy_delta": _delta(ta and ta.get("accuracy"), tb and tb.get("accuracy")),
                "latency_a": ta.get("elapsed_seconds") if ta else None,
                "latency_b": tb.get("elapsed_seconds") if tb else None,
                "latency_delta": _delta(ta and ta.get("elapsed_seconds"), tb and tb.get("elapsed_seconds")),
                "tps_a": ta.get("approx_tokens_per_second") if ta else None,
                "tps_b": tb.get("approx_tokens_per_second") if tb else None,
                "tps_delta": _delta(
                    ta and ta.get("approx_tokens_per_second"),
                    tb and tb.get("approx_tokens_per_second"),
                ),
            }
        )
    sa = req.card_a.get("summary") or _summary(req.card_a.get("tests") or [])
    sb = req.card_b.get("summary") or _summary(req.card_b.get("tests") or [])

    def _card_label(card: Dict[str, Any], fallback: str) -> str:
        name = card.get("model_id") or card.get("profile") or fallback
        dev = card.get("device")
        return f"{name}/{dev.upper()}" if dev else name

    return {
        "success": True,
        "label_a": _card_label(req.card_a, "A"),
        "label_b": _card_label(req.card_b, "B"),
        "summary_delta": {
            "avg_accuracy": _delta(sa.get("avg_accuracy"), sb.get("avg_accuracy")),
            "avg_latency_s": _delta(sa.get("avg_latency_s"), sb.get("avg_latency_s")),
            "avg_tokens_per_second": _delta(
                sa.get("avg_tokens_per_second"), sb.get("avg_tokens_per_second")
            ),
            "tps_a": sa.get("avg_tokens_per_second"),
            "tps_b": sb.get("avg_tokens_per_second"),
            "passed_a": sa.get("passed"),
            "passed_b": sb.get("passed"),
        },
        "tests": rows,
    }
