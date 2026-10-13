"""Shared Driver / Kart / Number naming configuration."""
from __future__ import annotations
import re

import setup_fields as sf
from utils import xrk_meta

DOC_KEY = "team_naming"
_DEFAULT_NUMBER_TO_CHASSIS = dict(sf.NUMBER_TO_CHASSIS)
_DEFAULT_CHASSIS_TO_NUMBER = dict(sf.CHASSIS_TO_NUMBER)
_KART_MARKER = re.compile(r"\s*\[xCAL kart:\s*([^\]]+)\]", re.IGNORECASE)


def normalize(doc) -> dict:
    rows = (doc or {}).get("assignments") if isinstance(doc, dict) else None
    assignments = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        assignments.append({
            "driver": str(row.get("driver") or "").strip(),
            "kart": str(row.get("kart") or "").strip(),
            "number": str(row.get("number") or "").strip(),
        })
    return {"assignments": assignments}


def from_drivers(drivers) -> dict:
    """Build shared naming rows from the existing Drivers table."""
    rows = []
    for driver in drivers:
        marker = _KART_MARKER.search(driver.notes or "")
        kart = (
            marker.group(1).strip() if marker
            else sf.chassis_for_number(driver.number))
        rows.append({
            "driver": driver.name,
            "number": driver.number or "",
            "kart": kart or "",
        })
    return {"assignments": rows}


def from_inventory(items) -> dict:
    """Build telemetry naming from official Driver and Kart Parts."""
    active = [item for item in items
              if not item.archived_at and item.status != "retired"]
    karts_by_number = {}
    for item in active:
        if item.category != "Karts":
            continue
        number = str((item.specs or {}).get("kart_number") or "").strip()
        if number:
            karts_by_number[number] = item.name
    rows = []
    for item in active:
        if item.category != "Drivers":
            continue
        number = str((item.specs or {}).get("race_number") or "").strip()
        rows.append({
            "driver": item.name,
            "number": number,
            "kart": karts_by_number.get(number, ""),
        })
    return {"assignments": rows}


def notes_with_kart(notes: str | None, kart: str) -> str | None:
    """Replace the private xCAL kart marker while preserving user notes."""
    clean = _KART_MARKER.sub("", notes or "").strip()
    marker = f"[xCAL kart: {kart}]" if kart else ""
    result = " ".join(part for part in (clean, marker) if part)
    return result or None


def apply(doc) -> dict:
    """Apply naming config to both telemetry parsing and setup conventions."""
    clean = normalize(doc)
    rows = clean["assignments"]
    xrk_meta.configure_naming(rows)

    sf.NUMBER_TO_CHASSIS.clear()
    sf.NUMBER_TO_CHASSIS.update(_DEFAULT_NUMBER_TO_CHASSIS)
    sf.CHASSIS_TO_NUMBER.clear()
    sf.CHASSIS_TO_NUMBER.update(_DEFAULT_CHASSIS_TO_NUMBER)
    numbers: dict[str, str] = {}
    chassis_numbers: dict[str, list[str]] = {}
    for row in rows:
        number, kart = row["number"], row["kart"]
        if not number or not kart:
            continue
        numbers[number] = kart
        chassis_numbers.setdefault(kart, [])
        if number not in chassis_numbers[kart]:
            chassis_numbers[kart].append(number)
    if numbers:
        sf.NUMBER_TO_CHASSIS.update(numbers)
    for kart, nums in chassis_numbers.items():
        sf.CHASSIS_TO_NUMBER[kart] = "/".join(nums)
    return clean
