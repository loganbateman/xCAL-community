"""Parse RaceStudio .xrk filenames for driver / kart-number context.

Typical filenames look like: "Driver_7_Track_Practice_a_0001.xrk"
i.e.  Driver_Number_Track_SessionType_..._####.xrk
"""
from __future__ import annotations
import re
from dataclasses import dataclass

# 11 = Birel, 24/42 = Redspeed (team convention)
DEFAULT_NUMBER_TO_CHASSIS = {}
NUMBER_TO_CHASSIS = dict(DEFAULT_NUMBER_TO_CHASSIS)
DRIVER_CANONICAL: dict[str, str] = {}


def configure_naming(assignments: list[dict]) -> None:
    """Apply shared Driver/Kart/Number naming conventions at runtime."""
    DRIVER_CANONICAL.clear()
    NUMBER_TO_CHASSIS.clear()
    NUMBER_TO_CHASSIS.update(DEFAULT_NUMBER_TO_CHASSIS)
    for row in assignments:
        driver = str(row.get("driver") or "").strip()
        number = str(row.get("number") or "").strip()
        kart = str(row.get("kart") or "").strip()
        if driver:
            DRIVER_CANONICAL[driver.lower()] = driver
        if number and kart:
            NUMBER_TO_CHASSIS[number] = kart


@dataclass(frozen=True)
class XrkMeta:
    driver: str | None
    number: str | None
    chassis: str | None

    @property
    def label(self) -> str:
        bits = []
        if self.driver:
            bits.append(self.driver)
        if self.number:
            kart = f"Kart {self.number}"
            if self.chassis:
                kart += f" ({self.chassis})"
            bits.append(kart)
        return "  ·  ".join(bits)


def parse_xrk_filename(name: str) -> XrkMeta | None:
    """Best-effort parse of driver + kart number from an .xrk filename."""
    stem = re.sub(r"\.xrk$", "", name, flags=re.IGNORECASE)
    parts = stem.split("_")
    if len(parts) < 2:
        return None
    raw_driver = parts[0].strip()
    driver = DRIVER_CANONICAL.get(raw_driver.lower(), raw_driver) or None
    number = None
    # The kart number is the first short numeric token after the driver.
    for tok in parts[1:3]:
        t = tok.strip()
        if t.isdigit() and len(t) <= 2:
            number = t
            break
    if not driver and not number:
        return None
    chassis = NUMBER_TO_CHASSIS.get(number or "")
    return XrkMeta(driver=driver, number=number, chassis=chassis)
