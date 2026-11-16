"""Turn k6 summaries into committed result records and `docs/PERFORMANCE.md`.

A record keeps only what the report shows (counts, error rates, latency percentiles)
next to the commit, hardware and configuration behind the numbers. The Markdown report
is generated from the record files and from nothing else, so every number in it can be
traced to a run.

    uv run python -m benchmarks.k6_report        # rebuild docs/PERFORMANCE.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = ROOT / "benchmarks" / "results"
REPORT = ROOT / "docs" / "PERFORMANCE.md"

# A capacity step counts as sustained when all of these hold.
MAX_FAILED = 0.01
MIN_ACHIEVED = 0.98
MAX_P95_MS = 1000.0

Record = dict[str, Any]


def _values(metrics: dict[str, Any], name: str) -> dict[str, float]:
    found = metrics.get(name)
    return dict(found["values"]) if found else {}


def _latency(metrics: dict[str, Any], tag: str) -> dict[str, float | int]:
    duration = _values(metrics, f"http_req_duration{{{tag}}}")
    requests = _values(metrics, f"http_reqs{{{tag}}}")
    failed = _values(metrics, f"http_req_failed{{{tag}}}")
    return {
        "requests": int(requests.get("count", 0)),
        "failed_rate": round(failed.get("rate", 0.0), 5),
        "p50_ms": round(duration.get("med", 0.0), 2),
        "p95_ms": round(duration.get("p(95)", 0.0), 2),
        "p99_ms": round(duration.get("p(99)", 0.0), 2),
        "max_ms": round(duration.get("max", 0.0), 2),
    }


def sustained(step: dict[str, Any]) -> bool:
    return bool(
        step["failed_rate"] < MAX_FAILED
        and step["dropped"] == 0
        and step["achieved_rate"] >= MIN_ACHIEVED * step["target_rate"]
        and step["p95_ms"] < MAX_P95_MS
    )


# Containers whose CPU use explains a result; the rest (Kafka, workers) is shown as "other".
_MAIN = ("api", "postgres", "redis", "k6")


def busiest(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The sample with the highest total CPU: the stack at its most loaded moment.

    CPU is per container, 100 = one core. PostgreSQL sessions say what the database was
    doing at that moment: running a statement (`active`), holding a transaction open while
    waiting for the API to send the next statement (`idle_in_transaction`), or waiting for
    a lock held by another session (`waiting_on_lock`).
    """
    if not samples:
        return None
    peak = max(samples, key=lambda sample: sum(sample["cpu_percent"].values()))
    cpu = peak["cpu_percent"]
    shown = {name: round(cpu.get(name, 0.0)) for name in _MAIN}
    shown["other"] = round(sum(value for name, value in cpu.items() if name not in _MAIN))
    return {
        "at_seconds": peak["at_seconds"],
        "samples": len(samples),
        "cpu_percent": shown,
        "postgres_sessions": peak["postgres_sessions"],
        "max_waiting_on_lock": max(s["postgres_sessions"]["waiting_on_lock"] for s in samples),
    }


def summarise(raw: dict[str, Any]) -> Record:
    """The part of a k6 summary (as written by workload.js) that the report uses."""
    metrics = raw["metrics"]
    config = raw["config"]
    seconds = raw["state"]["testRunDurationMs"] / 1000
    overall = _latency(metrics, "phase:test")
    record: Record = {
        "profile": raw["profile"],
        "config": config,
        "duration_seconds": round(seconds, 1),
        "overall": overall
        | {"dropped": int(_values(metrics, "dropped_iterations").get("count", 0))},
        "operations": {
            name: _latency(metrics, f"op:{name}") | {"weight": weight}
            for name, weight in config["weights"].items()
        },
        "thresholds_passed": all(
            threshold["ok"]
            for metric in metrics.values()
            for threshold in metric.get("thresholds", {}).values()
        ),
    }
    if raw["profile"] == "capacity":
        steps = []
        for rate in config["steps"]:
            tag = f"scenario:step_{rate}"
            step = _latency(metrics, tag)
            dropped = _values(metrics, f"dropped_iterations{{{tag}}}").get("count", 0)
            step |= {
                "target_rate": rate,
                "achieved_rate": round(step["requests"] / config["step_seconds"], 1),
                "dropped": int(dropped),
            }
            step["sustained"] = sustained(step)
            steps.append(step)
        record["steps"] = steps
    else:
        scenario = next(iter(raw["scenarios"].values()))
        record["target_rate"] = scenario["rate"]
    return record


# --- Markdown -------------------------------------------------------------------------


def _ms(value: float) -> str:
    whole_numbers_from = 100  # 3,021 ms reads better than 3,021.4 ms
    return f"{value:,.0f}" if value >= whole_numbers_from else f"{value:.1f}"


def _percent(rate: float) -> str:
    return f"{100 * rate:.2f}%"


def _environment(record: Record) -> list[str]:
    env = record["environment"]
    api = env["api"]
    return [
        f"- Recorded {record['recorded_at']} at commit `{record['commit']}`"
        + (" (with uncommitted changes)" if record.get("dirty") else "")
        + ".",
        f"- Machine: {env['machine']}, {env['os']}; Docker has {env['docker_cpus']} CPUs and "
        f"{env['docker_memory_gb']} GB. The load generator, the API, PostgreSQL, Redis and "
        "Kafka all share that machine.",
        f"- API: {api.get('WEB_CONCURRENCY', '1')} process(es), database pool "
        f"{api.get('DB_POOL_SIZE', '5')} + {api.get('DB_MAX_OVERFLOW', '5')} overflow per "
        f"process, pool timeout {api.get('DB_POOL_TIMEOUT_SECONDS', '3')} s; engines "
        f"`{api.get('AGENT_ENGINE')}` / `{api.get('EMBEDDING_PROVIDER')}` "
        "(offline, no model calls).",
        f"- Data: synthetic. {record['config']['users']} users, each with one workspace and "
        f"{record['config']['products_per_user']} products "
        "(two documents and 30 prices per product).",
    ]


def _per_process(record: Record) -> list[str]:
    used = record.get("api_cpu_seconds_per_process")
    if not used:
        return []
    listed = ", ".join(f"{seconds:g}" for seconds in used)
    return ["", f"CPU seconds used by each API process over the whole run: {listed}."]


def _busiest(record: Record) -> list[str]:
    peak = record.get("busiest")
    if not peak:
        return []
    cpu = peak["cpu_percent"]
    sessions = peak["postgres_sessions"]
    processes = int(record["environment"]["api"].get("WEB_CONCURRENCY", "1"))
    return [
        "",
        f"At the busiest moment sampled ({peak['at_seconds']} s into the run), CPU per "
        f"container (100% = one core): API **{cpu['api']}%** across {processes} process(es), "
        f"PostgreSQL **{cpu['postgres']}%**, Redis {cpu['redis']}%, k6 {cpu['k6']}%, "
        f"everything else {cpu['other']}%. PostgreSQL sessions at that moment: "
        f"{sessions['active']} running a statement, {sessions['idle_in_transaction']} holding "
        f"a transaction open while waiting for the API, {sessions['waiting_on_lock']} waiting "
        f"on a lock (most seen in any sample: {peak['max_waiting_on_lock']}).",
        *_per_process(record),
    ]


def _operations_table(record: Record) -> list[str]:
    lines = [
        "| Request | Share | Requests | Failed | p50 ms | p95 ms | p99 ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, op in record["operations"].items():
        lines.append(
            f"| `{name}` | {op['weight']}% | {op['requests']:,} | {_percent(op['failed_rate'])} | "
            f"{_ms(op['p50_ms'])} | {_ms(op['p95_ms'])} | {_ms(op['p99_ms'])} |"
        )
    overall = record["overall"]
    lines.append(
        f"| **all** | 100% | {overall['requests']:,} | {_percent(overall['failed_rate'])} | "
        f"{_ms(overall['p50_ms'])} | {_ms(overall['p95_ms'])} | {_ms(overall['p99_ms'])} |"
    )
    return lines


def _steps_table(record: Record) -> list[str]:
    lines = [
        "| Target req/s | Achieved req/s | Failed | Dropped "
        "| p50 ms | p95 ms | p99 ms | Sustained |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for step in record["steps"]:
        lines.append(
            f"| {step['target_rate']} | {step['achieved_rate']} "
            f"| {_percent(step['failed_rate'])} | {step['dropped']:,} "
            f"| {_ms(step['p50_ms'])} | {_ms(step['p95_ms'])} | "
            f"{_ms(step['p99_ms'])} | {'yes' if step['sustained'] else 'no'} |"
        )
    return lines


def highest_sustained(record: Record) -> int | None:
    """The last step before the first one that was not sustained."""
    best = None
    for step in record["steps"]:
        if not step["sustained"]:
            break
        best = step["target_rate"]
    return best


def _verdict(record: Record) -> str:
    best = highest_sustained(record)
    if best is None:
        return "No step was sustained."
    if all(step["sustained"] for step in record["steps"]):
        return f"Every step was sustained: the limit is above **{best} requests/s**."
    return f"Highest rate sustained: **{best} requests/s**."


def _section(record: Record) -> list[str]:
    label = record["label"]
    lines: list[str] = []
    if record["profile"] == "capacity":
        lines += [f"### Capacity, {label}", "", *_environment(record), ""]
        lines += _steps_table(record)
        lines += [
            "",
            _verdict(record),
            *_busiest(record),
        ]
    else:
        title = "Steady load" if record["profile"] == "load" else "Smoke"
        overall = record["overall"]
        lines += [
            f"### {title}, {label}",
            "",
            *_environment(record),
            f"- Load: {record['target_rate']} requests/s for {record['duration_seconds']:.0f} s "
            f"(including setup); {overall['dropped']} requests could not be started in time.",
            "",
            *_operations_table(record),
            *_busiest(record),
        ]
    return [*lines, ""]


INTRO = """# Performance

Generated by `python -m benchmarks.k6_report` from the files in `benchmarks/results/`.
Do not edit by hand: run a load test (`make load-test`, `make load-capacity`) instead.

## How to read this

- The load is a weighted mix of the requests a workspace makes (`benchmarks/k6/workload.js`):
  mostly reads, some searches and questions, a few writes, and now and then a full
  analysis of every product. The shares are an assumption about usage, not a measurement
  of real users.
- Requests arrive at a fixed rate whether or not the server keeps up (an open model), so a
  slow server shows up as high latency, failed or dropped requests, not as a lower rate.
- "Failed" is any response other than 200, 201 or 204, including the 503 the API returns
  when it sheds load.
- A capacity step is "sustained" when under 1% of requests fail, none are dropped, at
  least 98% of the target rate is achieved and p95 stays under 1 second.
- Everything runs on one machine, load generator included. The numbers describe this
  setup; they are not a prediction for a server deployment.
- Engines are the offline ones. Latency with a language model in the loop is dominated by
  the model and is not measured here.
"""

FINDINGS = """## What the load test found

- **One API process is the limit, not the database.** In the capacity runs recorded on
  the developer Mac (2026-11-15), at the busiest moment the API container used one full
  core (103%) while PostgreSQL used about a fifth of one, no session was waiting on a
  lock, and 9 to 10 sessions sat idle in a transaction waiting for the API. The database
  was waiting for Python, not the other way round.
- **Four API processes did not spread the load.** With `API_WORKERS=4`, one process used
  about 142 CPU-seconds and the other three too little to list. The sustained rate barely
  moved (100 requests per second with one process, 150 with four, a difference within
  run-to-run noise here), and about 26% of requests failed at 200 per second in both runs.
  Whether the extra uvicorn workers were not started in the
  container or never received connections has not been established. Phase 30 scales the
  API by running several containers behind a load balancer instead, and measures again.
- **Overload used to look like a crash.** When every pooled database connection was busy,
  a request waited 10 seconds and was then answered 500, with a stack trace in the log
  for each one. Now it waits at most `DB_POOL_TIMEOUT_SECONDS` (default 3), is answered
  `503` with `Retry-After` and the error code `overloaded`, writes one log line, and is
  counted in `mm_db_pool_timeouts_total`.
- **Requests hold a database connection for their whole duration.** A session is opened
  per request and its transaction stays open until the response is ready, so when the
  API process is busy, connections sit idle in a transaction until the pool runs out and
  new requests are shed.
- **A full analysis is by far the most expensive request.** It runs every agent for every
  product and issues several hundred SQL statements (p50 about 0.26 s at light load).
  Batching them is the obvious next optimisation and has not been done.

## Limits of these numbers

- One machine, one run per configuration, no repetition: small differences between runs
  mean nothing.
- With more than one API process per container, `/metrics` reports one process per
  scrape (`docs/OBSERVABILITY.md`); the default stays one process.
- The request mix is an assumption. A workspace that runs analyses more often than 1% of
  its requests will sustain a lower rate.
- WebSocket fan-out, Kafka throughput and query-level timings have their own benchmarks
  in `backend/benchmarks/`.
"""

_ORDER = {"capacity": 0, "load": 1, "smoke": 2}


def load_records(directory: Path = RESULTS_DIR) -> list[Record]:
    records = [json.loads(path.read_text()) for path in sorted(directory.glob("k6-*.json"))]
    return sorted(records, key=lambda r: (_ORDER.get(r["profile"], 9), r["label"]))


def render(records: list[Record]) -> str:
    lines = [INTRO, "## Results", ""]
    if not records:
        lines += ["No runs recorded yet.", ""]
    for record in records:
        lines += _section(record)
    lines.append(FINDINGS)
    return "\n".join(lines)


def write() -> Path:
    REPORT.write_text(render(load_records()))
    return REPORT


if __name__ == "__main__":
    print(f"wrote {write().relative_to(ROOT)}")  # noqa: T201
