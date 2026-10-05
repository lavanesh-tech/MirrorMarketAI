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

    exit_code = run_k6(args.profile)
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
