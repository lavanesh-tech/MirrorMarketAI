"""Run a k6 profile against the Compose stack and record the result.

    make load-up                                   # start the stack for load testing
    uv run python -m benchmarks.k6_run smoke       # or: make load-smoke
    uv run python -m benchmarks.k6_run load        # or: make load-test
    uv run python -m benchmarks.k6_run capacity    # or: make load-capacity

Writes `benchmarks/results/k6-<profile>-<label>.json` (commit, hardware, configuration
and the measured numbers) and regenerates `docs/PERFORMANCE.md`. The label defaults to
the number of API processes (`w1`, `w4`).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

from benchmarks import k6_report

ROOT = k6_report.ROOT
RAW_DIR = k6_report.RESULTS_DIR / "raw"
COMPOSE = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.loadtest.yml",
    "--profile",
    "loadtest",
]
# Passed through to the k6 script when set.
TUNABLES = ("RATE", "DURATION", "STEPS", "STEP_SECONDS", "USERS", "PRODUCTS")
# Recorded with the result. Never secrets.
RECORDED_SETTINGS = (
    "WEB_CONCURRENCY",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_TIMEOUT_SECONDS",
    "AGENT_ENGINE",
    "REQUIREMENTS_EXTRACTOR",
    "EMBEDDING_PROVIDER",
    "RATE_LIMIT_ENABLED",
    "RATE_LIMIT_AUTH_PER_MINUTE",
    "RATE_LIMIT_AGENTS_PER_MINUTE",
    "KAFKA_ENABLED",
    "OTEL_ENABLED",
)
OFFLINE = {
    "AGENT_ENGINE": "rules",
    "REQUIREMENTS_EXTRACTOR": "rules",
    "EMBEDDING_PROVIDER": "hashing",
}
# Files a run rewrites itself; they do not make the measured code "dirty".
GENERATED = ("benchmarks/results/", "docs/PERFORMANCE.md")


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=check)  # noqa: S603


def api_settings() -> dict[str, str]:
    """The API container's own view of the settings that shape a result."""
    printed = _run([*COMPOSE, "exec", "-T", "api", "printenv"]).stdout
    env = dict(line.split("=", 1) for line in printed.splitlines() if "=" in line)
    return {key: env[key] for key in RECORDED_SETTINGS if key in env}


def refuse_paid_engines(settings: dict[str, str]) -> None:
    wrong = {key: settings.get(key) for key, value in OFFLINE.items() if settings.get(key) != value}
    if wrong:
        raise SystemExit(
            f"The API is not running the offline engines ({wrong}). A load test must not call a "
            "paid model API. Start the stack with `make load-up` and try again."
        )


def git_state() -> tuple[str, bool]:
    commit = _run(["git", "rev-parse", "--short", "HEAD"], check=False).stdout.strip() or "unknown"
    changed = [
        line[3:]
        for line in _run(["git", "status", "--porcelain"], check=False).stdout.splitlines()
        if not line[3:].startswith(GENERATED)
    ]
    return commit, bool(changed)


def machine() -> dict[str, Any]:
    info = _run(
        ["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"], check=False
    ).stdout.split()
    cpus, memory = (int(info[0]), int(info[1])) if len(info) == 2 else (0, 0)  # noqa: PLR2004
    return {
        "machine": f"{platform.machine()}, {os.cpu_count()} logical CPUs",
        "os": f"{platform.system()} {platform.release()}",
        "docker_cpus": cpus,
        "docker_memory_gb": round(memory / 1024**3, 1),
    }


SAMPLE_SECONDS = 5.0
_SESSIONS_SQL = (
    "SELECT CASE WHEN wait_event_type = 'Lock' THEN 'waiting_on_lock' "
    "WHEN state = 'idle in transaction' THEN 'idle_in_transaction' "
    "WHEN state = 'active' THEN 'active' ELSE 'idle' END, count(*) "
    "FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() "
    "GROUP BY 1"
)


def sample_once() -> dict[str, Any]:
    """CPU per container (100 = one core) and what PostgreSQL sessions are doing."""
    stats = _run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}}"], check=False
    )
    cpu: dict[str, float] = {}
    for line in stats.stdout.splitlines():
        name, _, percent = line.partition(" ")
        service = name.removeprefix("mirrormarket-").rsplit("-", 1)[0]
        if service.startswith("k6-run"):
            service = "k6"
        try:
            cpu[service] = float(percent.rstrip("%"))
        except ValueError:
            continue
    psql = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "' + _SESSIONS_SQL + '"'
    sessions = _run([*COMPOSE, "exec", "-T", "postgres", "sh", "-c", psql], check=False)
    states = {"active": 0, "idle_in_transaction": 0, "waiting_on_lock": 0, "idle": 0}
    for line in sessions.stdout.splitlines():
        state, _, count = line.partition("|")
        if state in states and count.isdigit():
            states[state] = int(count)
    return {"cpu_percent": cpu, "postgres_sessions": states}


_PROCESS_CPU = (
    "import os\n"
    "tick = os.sysconf('SC_CLK_TCK')\n"
    "for pid in filter(str.isdigit, os.listdir('/proc')):\n"
    "    try:\n"
    "        fields = open(f'/proc/{pid}/stat').read().rsplit(')', 1)[1].split()\n"
    "    except OSError:\n"
    "        continue\n"
    "    print(pid, (int(fields[11]) + int(fields[12])) / tick)\n"
)


def api_process_cpu() -> dict[str, float]:
    """CPU seconds used so far by each process in the API container."""
    printed = _run([*COMPOSE, "exec", "-T", "api", "python", "-c", _PROCESS_CPU], check=False)
    used: dict[str, float] = {}
    for line in printed.stdout.splitlines():
        pid, _, seconds = line.partition(" ")
        try:
            used[pid] = float(seconds)
        except ValueError:
            continue
    return used


def cpu_per_process(before: dict[str, float], after: dict[str, float]) -> list[float]:
    """CPU seconds each API process used during the run, busiest first.

    Shows whether the load was shared between the processes or landed on one of them.
    Processes that did next to nothing (the supervisor, this probe) are left out.
    """
    used = sorted((after[pid] - before.get(pid, 0.0) for pid in after), reverse=True)
    floor = max(1.0, 0.02 * used[0]) if used else 0.0
    return [round(seconds, 1) for seconds in used if seconds >= floor]


class Sampler(threading.Thread):
    """Samples the stack while k6 runs, so a result also says where the time went."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[dict[str, Any]] = []
        self._stop_requested = threading.Event()
        self._began = time.monotonic()

    def run(self) -> None:
        while not self._stop_requested.wait(SAMPLE_SECONDS):
            sample = sample_once()
            if sample["cpu_percent"]:
                self.samples.append({"at_seconds": round(time.monotonic() - self._began)} | sample)

    def stop(self) -> list[dict[str, Any]]:
        self._stop_requested.set()
        self.join(timeout=30)
        return self.samples


def run_k6(profile: str) -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw = RAW_DIR / f"{profile}.json"
    raw.unlink(missing_ok=True)
    command = [*COMPOSE, "run", "--rm", "-e", f"PROFILE={profile}"]
    if hasattr(os, "getuid"):  # so the result file belongs to the caller on Linux
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    for name in TUNABLES:
        if os.environ.get(name):
            command += ["-e", f"{name}={os.environ[name]}"]
    # Output goes straight to the terminal: a load test is watched while it runs.
    return subprocess.run([*command, "k6"], cwd=ROOT, check=False).returncode  # noqa: S603


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=["smoke", "load", "capacity"])
    parser.add_argument("--label", help="name for this configuration (default: w<API processes>)")
    args = parser.parse_args()

    settings = api_settings()
    refuse_paid_engines(settings)
    label = args.label or f"w{settings.get('WEB_CONCURRENCY', '1')}"

    cpu_before = api_process_cpu()
    sampler = Sampler()
    sampler.start()
    exit_code = run_k6(args.profile)
    samples = sampler.stop()
    per_process = cpu_per_process(cpu_before, api_process_cpu())
    raw = RAW_DIR / f"{args.profile}.json"
    if not raw.exists():
        print(f"k6 wrote no summary (exit code {exit_code}); nothing recorded.", file=sys.stderr)  # noqa: T201
        return exit_code or 1

    commit, dirty = git_state()
    record = {
        "label": label,
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit,
        "dirty": dirty,
        "tool": "k6 (grafana/k6 image in docker-compose.loadtest.yml)",
        "environment": machine() | {"api": settings},
        "busiest": k6_report.busiest(samples),
        "api_cpu_seconds_per_process": per_process,
        **k6_report.summarise(json.loads(raw.read_text())),
    }
    target = k6_report.RESULTS_DIR / f"k6-{args.profile}-{label}.json"
    if args.profile != "smoke":  # a smoke run proves the scripts work; it is not a result
        target.write_text(json.dumps(record, indent=2) + "\n")
        k6_report.write()
        print(f"recorded {target.relative_to(ROOT)} and rebuilt docs/PERFORMANCE.md")  # noqa: T201
    if not record["thresholds_passed"]:
        print("k6 thresholds FAILED", file=sys.stderr)  # noqa: T201
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
