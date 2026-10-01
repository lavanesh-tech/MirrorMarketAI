"""Capability-detection benchmark for the rules engine of the Compatibility Agent.

Run: uv run python -m benchmarks.compatibility
Synthetic, author-labelled spec/review sentences: (text, capability, expected support).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.compatibility import Support, detect
from benchmarks.citations import _commit

S, X, U = Support.SUPPORTED, Support.NOT_SUPPORTED, Support.UNKNOWN

CASES: list[tuple[str, str, Support]] = [
    ("Ports include two Thunderbolt 4 USB-C connectors and HDMI 2.1.", "hdmi", S),
    ("Ports include two Thunderbolt 4 USB-C connectors and HDMI 2.1.", "usb_c", S),
    ("There is no HDMI port on this model.", "hdmi", X),
    ("HDMI is not supported.", "hdmi", X),
    ("It does not have an SD card reader.", "sd_card", X),
    ("A full-size SD card slot sits on the right edge.", "sd_card", S),
    ("No fan noise, plus HDMI 2.1 output for TVs.", "hdmi", S),
    ("Without a headphone jack, you need a dongle.", "headphone_jack", X),
    ("The 3.5mm audio jack is still here.", "headphone_jack", S),
    ("Connects over Bluetooth 5.3 with multipoint.", "bluetooth", S),
    ("Supports AAC and LDAC codecs.", "aac", S),
    ("The companion app is available for iPhone and Android.", "ios", S),
    ("The companion app is available for iPhone and Android.", "android", S),
    ("Linux is not officially supported.", "linux", X),
    ("Ships with Windows 11 Home.", "windows", S),
    ("Gigabit Ethernet via the RJ-45 port.", "ethernet", S),
    ("Wi-Fi 7 and Bluetooth 5.4 are built in.", "wifi", S),
    ("USB-A ports were dropped in favour of USB-C.", "usb_a", X),
    ("The battery lasts all day.", "hdmi", U),
    ("Great keyboard and a bright screen.", "usb_c", U),
    ("The dock is sold separately and is not included; HDMI works through it.", "hdmi", S),
    ("Lacks DisplayPort output.", "displayport", X),
]


# Written after the rules were tuned on CASES; reported separately as a held-out check.
HELD_OUT: list[tuple[str, str, Support]] = [
    ("Sadly, there's no Ethernet jack, so bring an adapter.", "ethernet", X),
    ("You get HDMI, two USB-A ports and a microSD slot.", "usb_a", S),
    ("You get HDMI, two USB-A ports and a microSD slot.", "sd_card", S),
    ("macOS users will need a third-party driver; Windows works out of the box.", "windows", S),
    ("It never supported Linux, and the BIOS blocks other operating systems.", "linux", X),
    ("Bluetooth pairing was flaky in our testing but it does connect.", "bluetooth", S),
    ("Thunderbolt is missing on the base model.", "thunderbolt", X),
    ("The charger plugs into the USB-C port on the left.", "usb_c", S),
]


def _score(cases: list[tuple[str, str, Support]]) -> tuple[int, list[str]]:
    correct = 0
    misses: list[str] = []
    for text, capability, expected in cases:
        found = detect(capability, text)
        got = found[0] if found else U
        if got is expected:
            correct += 1
        else:
            misses.append(f"{capability}: {text!r} -> {got.value}, want {expected.value}")
    return correct, misses


def run() -> dict[str, object]:
    correct, misses = _score(CASES)
    held_correct, held_misses = _score(HELD_OUT)
    return {
        "benchmark": "compatibility_detection_rules",
        "dataset": f"synthetic, {len(CASES)} labelled sentences (negation, clause boundaries)",
        "cases": len(CASES),
        "accuracy": round(correct / len(CASES), 4),
        "correct": correct,
        "misses": misses,
        "held_out_cases": len(HELD_OUT),
        "held_out_accuracy": round(held_correct / len(HELD_OUT), 4),
        "held_out_misses": held_misses,
        "note": "rules were tuned on `cases` (0.8182 before tuning); held_out was written after",
    }


def main() -> None:
    result = run() | {"commit": _commit()}
    out = Path(__file__).parent / "results" / "compatibility.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
