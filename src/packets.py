"""
Packets — a per-kart bundle of RaceStudio telemetry + a setup within a session.

A session can contain several kart numbers (either as numeric sub-folders like
72 / 24 / 11, or as the number embedded in each .xrk filename). Each kart gets
its own packet: a setup record carrying the kart number/chassis and a reference
list of its .xrk files (stored in the setup's JSONB data under "telemetry").

`generate_packets()` creates these from existing Drive data, skipping karts that
already have a setup in that folder.
"""
from __future__ import annotations

import database.client as db
from database.models import Setup
from utils import google_drive_client as g
from utils.xrk_meta import parse_xrk_filename
import setup_fields as sf


def detect_track(*texts: str) -> str | None:
    blob = " ".join(t for t in texts if t).lower()
    if "kaminsky" in blob:
        return "Kaminsky"
    if "purdue" in blob or "pgp" in blob or " gp" in blob or "(gp" in blob:
        return "Purdue GP"
    return None


def karts_for_session(session) -> list[tuple[str | None, str | None, list]]:
    """Return [(kart_number, chassis_name, xrk_files)] for a session folder.

    If the folder name is itself a kart number, the whole folder is one kart.
    Otherwise group the .xrk files by the number parsed from their filename.
    """
    name = (session.name or "").strip()
    if name.isdigit():
        return [(name, sf.chassis_for_number(name), list(session.xrks))]

    groups: dict[str | None, list] = {}
    for x in session.xrks:
        m = parse_xrk_filename(x.name)
        num = m.number if m else None
        groups.setdefault(num, []).append(x)
    out = []
    for num, files in groups.items():
        out.append((num, sf.chassis_for_number(num) if num else None, files))
    return out


def _telemetry_refs(files) -> list[dict]:
    return [{"id": f.id, "name": f.name, "size": f.size} for f in files]


def generate_packets(sessions=None) -> dict:
    """Create per-kart packet setups from existing Drive sessions.

    Returns {"created": n, "skipped": n, "sessions": n}.
    """
    if sessions is None:
        sessions = g.walk_session_folders()
    chassis = {c.name: c.id for c in db.fetch_chassis()}

    created = skipped = 0
    for s in sessions:
        if not s.xrks:
            continue  # a packet needs RaceStudio data
        existing = db.fetch_setups_by_folder(s.id)
        existing_karts: set[str] = set()
        for e in existing:
            kn = str(e.get("kart_number") or "").strip()
            for part in kn.replace(" ", "").split("/"):
                if part:
                    existing_karts.add(part)
        track = detect_track(s.name, s.path)

        for num, chassis_name, files in karts_for_session(s):
            key = str(num) if num else ""
            if num and key in existing_karts:
                skipped += 1
                continue
            data = {"telemetry": _telemetry_refs(files)}
            if num:
                data["kart_number"] = num
            cid = chassis.get(chassis_name) if chassis_name else None
            name = f"Kart {num}" if num else "Setup"
            db.create_setup(Setup(
                is_baseline=False, name=name, track=track,
                chassis_id=cid, drive_folder_id=s.id,
                drive_folder_path=s.path, data=data,
                notes="Packet auto-generated from Drive telemetry.",
            ))
            created += 1
    return {"created": created, "skipped": skipped, "sessions": len(sessions)}
