"""Run a k6 profile against the Compose stack and record the result.

    make load-up                                   # start the stack for load testing
    uv run python -m benchmarks.k6_run smoke       # or: make load-smoke
    uv run python -m benchmarks.k6_run load        # or: make load-test
    uv run python -m benchmarks.k6_run capacity    # or: make load-capacity

Writes `benchmarks/results/k6-<profile>-<label>.json` (commit, hardware, configuration
and the measured numbers) and regenerates `docs/PERFORMANCE.md`. The label defaults to
the number of API processes per container (`w1`, `w4`), prefixed with the number of API
containers when there are several (`r3w1`: three containers behind the load balancer).

The Compose files come from COMPOSE_FILE when it is set (the scaled setup sets it to
the three files, see `make load-up-scaled`); otherwise the base file plus the load-test
overlay.
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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmarks import k6_report

ROOT = k6_report.ROOT
RAW_DIR = k6_report.RESULTS_DIR / "raw"
DEFAULT_FILES = ("docker-compose.yml", "docker-compose.loadtest.yml")
PROBE = Path(__file__).with_name("process_probe.py")


def compose_command(environ: Mapping[str, str]) -> list[str]:
    """`docker compose` with the load-test files; COMPOSE_FILE, when set, picks them."""
    files = [] if environ.get("COMPOSE_FILE") else [arg for f in DEFAULT_FILES for arg in ("-f", f)]
    return ["docker", "compose", *files, "--profile", "loadtest"]


COMPOSE = compose_command(os.environ)
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


def service_of(container: str) -> str:
    """Compose service of a container name: `mirrormarket-api-2` -> `api`."""
    service = container.removeprefix("mirrormarket-").rsplit("-", 1)[0]
    return "k6" if service.startswith("k6-run") else service


def sample_once() -> dict[str, Any]:
    """CPU per container (100 = one core) and what PostgreSQL sessions are doing."""
    stats = _run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}}"], check=False
    )
    cpu: dict[str, float] = {}
    for line in stats.stdout.splitlines():
        name, _, percent = line.partition(" ")
        try:
            used = float(percent.rstrip("%"))
        except ValueError:
            continue
        service = service_of(name)
        cpu[service] = round(cpu.get(service, 0.0) + used, 2)  # replicas add up
    psql = 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "' + _SESSIONS_SQL + '"'
    sessions = _run([*COMPOSE, "exec", "-T", "postgres", "sh", "-c", psql], check=False)
    states = {"active": 0, "idle_in_transaction": 0, "waiting_on_lock": 0, "idle": 0}
    for line in sessions.stdout.splitlines():
        state, _, count = line.partition("|")
        if state in states and count.isdigit():
            states[state] = int(count)
    return {"cpu_percent": cpu, "postgres_sessions": states}


def api_containers() -> list[str]:
    """IDs of the running API containers (one, or API_REPLICAS in the scaled setup)."""
    return sorted(_run([*COMPOSE, "ps", "-q", "api"], check=False).stdout.split())


def parse_probe(printed: str, container: str) -> dict[str, tuple[float, int]]:
    """`process_probe` output -> {"<container>/<pid>": (CPU seconds, connections)}."""
    found: dict[str, tuple[float, int]] = {}
    for line in printed.splitlines():
        parts = line.split()
        if len(parts) != 3:  # noqa: PLR2004
            continue
        try:
            found[f"{container[:12]}/{parts[0]}"] = (float(parts[1]), int(parts[2]))
        except ValueError:
            continue
    return found


def api_processes(containers: list[str]) -> dict[str, tuple[float, int]]:
    """CPU seconds used so far and open client connections, per process in every API container."""
    found: dict[str, tuple[float, int]] = {}
    for container in containers:
        printed = subprocess.run(  # noqa: S603
            ["docker", "exec", "-i", container, "python", "-"],  # noqa: S607
            input=PROBE.read_text(),
            capture_output=True,
            text=True,
            check=False,
        )
        found |= parse_probe(printed.stdout, container)
    return found


def cpu_per_process(before: dict[str, float], after: dict[str, float]) -> list[float]:
    """CPU seconds each API process used during the run, busiest first.

    Shows whether the load was shared between the processes or landed on one of them.
    Processes that did next to nothing (the supervisor, this probe) are left out.
    """
    return [row["cpu_seconds"] for row in process_table(before, after, {})]


def process_table(
    before: dict[str, float], after: dict[str, float], connections: dict[str, int]
) -> list[dict[str, float | int]]:
    """Per API process: CPU seconds used during the run and the most client connections
    it held in any sample, busiest first. Idle processes (supervisor, probe) are left out.
    """
    used = {key: after[key] - before.get(key, 0.0) for key in after}
    top = max(used.values(), default=0.0)
    floor = max(1.0, 0.02 * top)
    rows = [
        {"cpu_seconds": round(seconds, 1), "peak_connections": connections.get(key, 0)}
        for key, seconds in used.items()
        if seconds >= floor
    ]
    return sorted(rows, key=lambda row: row["cpu_seconds"], reverse=True)


def default_label(settings: dict[str, str], containers: int) -> str:
    """`w4` for one container with four processes; `r3w1` for three containers of one."""
    processes = f"w{settings.get('WEB_CONCURRENCY', '1')}"
    return f"r{containers}{processes}" if containers > 1 else processes


class Sampler(threading.Thread):
    """Samples the stack while k6 runs, so a result also says where the time went."""

    def __init__(self, containers: list[str]) -> None:
        super().__init__(daemon=True)
        self.samples: list[dict[str, Any]] = []
        # Most client connections each API process held in any sample.
        self.peak_connections: dict[str, int] = {}
        self._containers = containers
        self._stop_requested = threading.Event()
        self._began = time.monotonic()

    def run(self) -> None:
        while not self._stop_requested.wait(SAMPLE_SECONDS):
            sample = sample_once()
            if sample["cpu_percent"]:
                self.samples.append({"at_seconds": round(time.monotonic() - self._began)} | sample)
            for key, (_, held) in api_processes(self._containers).items():
                self.peak_connections[key] = max(held, self.peak_connections.get(key, 0))

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
    containers = api_containers()
    label = args.label or default_label(settings, len(containers))

    cpu_before = {key: cpu for key, (cpu, _) in api_processes(containers).items()}
    sampler = Sampler(containers)
    sampler.start()
    exit_code = run_k6(args.profile)
    samples = sampler.stop()
    cpu_after = {key: cpu for key, (cpu, _) in api_processes(containers).items()}
    processes = process_table(cpu_before, cpu_after, sampler.peak_connections)
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
        "environment": machine() | {"api": settings, "api_containers": len(containers)},
        "busiest": k6_report.busiest(samples),
        "api_cpu_seconds_per_process": [row["cpu_seconds"] for row in processes],
        "api_processes": processes,
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
