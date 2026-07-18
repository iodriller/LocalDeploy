from __future__ import annotations

import benchmark


def test_repetitions_emit_variance_and_provenance(monkeypatch) -> None:
    elapsed = iter([1.0, 3.0])

    def fake_execute(*args, **kwargs):
        value = next(elapsed)
        return benchmark.TestResult(
            name="repeat",
            category="quality",
            success=True,
            elapsed_seconds=value,
            response_length=2,
            response_preview="ok",
            accuracy=1.0,
            approx_tokens_per_second=20.0 / value,
            metrics={"tokens_per_second": 20.0 / value},
            tokens_per_second_source="backend",
        )

    monkeypatch.setattr(benchmark, "execute_test", fake_execute)
    case = benchmark.TestCase(
        name="repeat",
        category="quality",
        prompt="x",
        grader=lambda text: 1.0,
        grader_explainer="test",
    )
    provenance = {"profiles": {"p": {"warm_state": "cold", "model_digest": "sha256:full"}}}
    events = list(
        benchmark.iter_run(
            "http://127.0.0.1:8000",
            {"p": {"model_id": "m"}},
            ["p"],
            [case],
            30,
            repetitions=2,
            provenance=provenance,
        )
    )
    assert events[0]["test_count"] == 2
    assert events[0]["provenance"] == provenance
    results = [event for event in events if event["event"] == "test_result"]
    assert [event["repetition"] for event in results] == [1, 2]
    assert [event["warm_state"] for event in results] == ["cold", "warm"]
    aggregate = next(event for event in events if event["event"] == "test_aggregate")
    assert aggregate["latency_stdev_seconds"] == 1.414
    assert aggregate["tokens_per_second_stdev"] == 9.43
