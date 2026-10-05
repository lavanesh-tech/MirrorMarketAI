"""The load-test report: what is kept from a k6 summary and how a run is judged."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmarks import k6_report, k6_run

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
WORKLOAD = ROOT / "benchmarks" / "k6" / "workload.js"
WEIGHTS = {"workspace_get": 60, "search": 30, "analyze": 10}


def _trend(p50: float, p95: float) -> dict[str, Any]:
    values = {"med": p50, "p(95)": p95, "p(99)": p95 * 1.5, "max": p95 * 2, "avg": p50}
    return {"type": "trend", "values": values, "thresholds": {"max>=0": {"ok": True}}}


def _tagged(
    tag: str, count: int, failed: float, p50: float, p95: float, *, ok: bool = True
) -> dict[str, Any]:
    return {
        f"http_req_duration{{{tag}}}": _trend(p50, p95),
        f"http_reqs{{{tag}}}": {"type": "counter", "values": {"count": count, "rate": 1.0}},
        f"http_req_failed{{{tag}}}": {
            "type": "rate",
            "values": {"rate": failed},
            "thresholds": {"rate<0.01": {"ok": ok}},
        },
    }


def _raw(profile: str, **extra_metrics: Any) -> dict[str, Any]:
    metrics: dict[str, Any] = _tagged("phase:test", 1000, 0.0, 8.0, 40.0)
    for name in WEIGHTS:
        metrics |= _tagged(f"op:{name}", 100, 0.0, 5.0, 20.0)
    metrics |= extra_metrics
    return {
        "profile": profile,
        "config": {
            "users": 10,
            "products_per_user": 3,
            "rate": 30,
            "steps": [50, 100, 200],
            "step_seconds": 30,
            "weights": WEIGHTS,
        },
        "scenarios": {profile: {"executor": "constant-arrival-rate", "rate": 30}},
        "state": {"testRunDurationMs": 125_000},
        "metrics": metrics,
    }


def _record(raw: dict[str, Any], label: str = "w1") -> dict[str, Any]:
    return k6_report.summarise(raw) | {
        "label": label,
        "recorded_at": "2026-10-05T00:00:00+00:00",
        "commit": "abc1234",
        "dirty": False,
        "environment": {
            "machine": "arm64, 15 logical CPUs",
            "os": "Darwin 25",
            "docker_cpus": 15,
            "docker_memory_gb": 7.7,
            "api": {
                "WEB_CONCURRENCY": label[1:],
                "AGENT_ENGINE": "rules",
                "EMBEDDING_PROVIDER": "hashing",
            },
        },
    }


def _capacity() -> dict[str, Any]:
    steps: dict[str, Any] = {}
    steps |= _tagged("scenario:step_50", 1500, 0.0, 8.0, 30.0)
    steps |= _tagged("scenario:step_100", 3000, 0.001, 20.0, 400.0)
    steps |= _tagged("scenario:step_200", 5100, 0.2, 900.0, 3050.0)
    steps["dropped_iterations{scenario:step_200}"] = {"type": "counter", "values": {"count": 42}}
    steps["dropped_iterations"] = {"type": "counter", "values": {"count": 42}}
    return _raw("capacity", **steps)


def test_a_steady_run_keeps_counts_errors_and_percentiles() -> None:
    record = k6_report.summarise(_raw("load"))
    assert record["profile"] == "load"
    assert record["target_rate"] == 30
    assert record["duration_seconds"] == 125.0
    assert record["overall"] == {
        "requests": 1000,
        "failed_rate": 0.0,
        "p50_ms": 8.0,
        "p95_ms": 40.0,
        "p99_ms": 60.0,
        "max_ms": 80.0,
        "dropped": 0,
    }
    assert record["operations"]["search"]["weight"] == 30
    assert record["operations"]["search"]["p95_ms"] == 20.0
    assert record["thresholds_passed"] is True
    assert "steps" not in record


def test_a_failed_threshold_is_recorded() -> None:
    raw = _raw("load", **_tagged("phase:test", 1000, 0.05, 8.0, 40.0, ok=False))
    assert k6_report.summarise(raw)["thresholds_passed"] is False


def test_capacity_steps_are_judged_one_by_one() -> None:
    record = k6_report.summarise(_capacity())
    by_rate = {step["target_rate"]: step for step in record["steps"]}
    assert by_rate[50]["achieved_rate"] == 50.0
    assert [by_rate[rate]["sustained"] for rate in (50, 100, 200)] == [True, True, False]
    assert by_rate[200]["dropped"] == 42
    assert by_rate[200]["achieved_rate"] == 170.0
    assert k6_report.highest_sustained(record) == 100


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, True),
        ({"failed_rate": 0.01}, False),
        ({"dropped": 1}, False),
        ({"achieved_rate": 97.9}, False),
        ({"p95_ms": 1000.0}, False),
    ],
)
def test_what_counts_as_sustained(change: dict[str, float], expected: bool) -> None:
    step = {
        "failed_rate": 0.0,
        "dropped": 0,
        "achieved_rate": 100.0,
        "target_rate": 100,
        "p95_ms": 50.0,
    }
    assert k6_report.sustained(step | change) is expected


def test_a_recovery_after_a_failed_step_does_not_count() -> None:
    record = k6_report.summarise(_capacity())
    record["steps"][0]["sustained"] = False
    assert k6_report.highest_sustained(record) is None


def test_report_shows_every_run_with_its_environment() -> None:
    report = k6_report.render([_record(_capacity(), "w4"), _record(_raw("load"))])
    assert "### Capacity, w4" in report
    assert "### Steady load, w1" in report
    assert "commit `abc1234`" in report
    assert "arm64, 15 logical CPUs" in report
    assert "API: 4 process(es)" in report
    assert "Highest rate sustained: **100 requests/s**." in report
    assert "| 200 | 170.0 | 20.00% | 42 | 900 | 3,050 | 4,575 | no |" in report
    assert "| `search` | 30% | 100 | 0.00% | 5.0 | 20.0 | 30.0 |" in report
    assert "synthetic" in report


def test_report_without_runs_says_so() -> None:
    assert "No runs recorded yet." in k6_report.render([])


def test_uncommitted_changes_are_disclosed() -> None:
    record = _record(_raw("load")) | {"dirty": True}
    assert "(with uncommitted changes)" in k6_report.render([record])


def test_load_test_refuses_paid_engines() -> None:
    offline = {
        "AGENT_ENGINE": "rules",
        "REQUIREMENTS_EXTRACTOR": "rules",
        "EMBEDDING_PROVIDER": "hashing",
    }
    k6_run.refuse_paid_engines(offline)
    with pytest.raises(SystemExit, match="offline engines"):
        k6_run.refuse_paid_engines(offline | {"AGENT_ENGINE": "openai"})
    with pytest.raises(SystemExit, match="offline engines"):
        k6_run.refuse_paid_engines({})


def test_no_secret_is_recorded_with_a_result() -> None:
    for name in k6_run.RECORDED_SETTINGS:
        assert not re.search(r"KEY|SECRET|TOKEN|PASSWORD|URL", name), name


def test_request_shares_sum_to_100() -> None:
    weights = [
        int(w) for w in re.findall(r"^  \w+: \[\s*(\d+),", WORKLOAD.read_text(), re.MULTILINE)
    ]
    assert len(weights) >= 10
    assert sum(weights) == 100


def test_overlay_forces_offline_engines_and_keeps_k6_optional() -> None:
    overlay = yaml.safe_load((ROOT / "docker-compose.loadtest.yml").read_text())
    for service in ("api", "embedding-worker", "event-worker"):
        env = overlay["services"][service]["environment"]
        assert (env["AGENT_ENGINE"], env["REQUIREMENTS_EXTRACTOR"], env["EMBEDDING_PROVIDER"]) == (
            "rules",
            "rules",
            "hashing",
        )
    k6 = overlay["services"]["k6"]
    assert k6["profiles"] == ["loadtest"]
    assert "--no-usage-report" in k6["entrypoint"]


def test_a_run_that_never_reached_the_limit_does_not_claim_one() -> None:
    raw = _capacity()
    raw["metrics"] |= _tagged("scenario:step_200", 6000, 0.0, 10.0, 50.0)
    raw["metrics"]["dropped_iterations{scenario:step_200}"]["values"]["count"] = 0
    report = k6_report.render([_record(raw)])
    assert "Every step was sustained: the limit is above **200 requests/s**." in report
    assert "Highest rate sustained" not in report
