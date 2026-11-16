"""Printed per process inside an API container: pid, CPU seconds, open client connections.

Run inside the container by `k6_run` (piped to `python -`), so it uses the standard
library only and reads the container's own /proc. One line per process:

    <pid> <CPU seconds used so far> <established connections on the API port>

The connection count shows how the load generator's (or the balancer's) connections
are spread over the API processes: a process that holds no connection gets no work.
"""

import os
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
ESTABLISHED = "01"


def established_sockets(port: int) -> set[str]:
    """Socket inodes of established TCP connections whose local port is `port`."""
    inodes: set[str] = set()
    suffix = f":{port:04X}"
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as handle:
                rows = handle.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            fields = row.split()
            if fields[1].endswith(suffix) and fields[3] == ESTABLISHED:
                inodes.add(fields[9])
    return inodes


def held(pid: str, sockets: set[str]) -> int:
    count = 0
    try:
        descriptors = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return 0
    for descriptor in descriptors:
        try:
            target = os.readlink(f"/proc/{pid}/fd/{descriptor}")
        except OSError:
            continue
        if target.startswith("socket:[") and target[8:-1] in sockets:
            count += 1
    return count


def main() -> None:
    tick = os.sysconf("SC_CLK_TCK")
    sockets = established_sockets(PORT)
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/stat") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        seconds = (int(fields[11]) + int(fields[12])) / tick
        print(pid, seconds, held(pid, sockets))  # noqa: T201


if __name__ == "__main__":
    main()
