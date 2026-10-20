"""Team-specific setup parameter definitions; existing field keys never change."""
from copy import deepcopy
import re
from uuid import uuid4

DOC_KEY = "team_setup_fields"
SECTIONS = {"front": "Front End", "rear": "Rear End", "engine": "Engine / Drivetrain / Carb",
            "tires": "Tires & Wheels", "session": "Session"}


def normalize(document):
    if document is None:
        return {"schema": 1, "fields": []}
    if not isinstance(document, dict) or document.get("schema") != 1 or not isinstance(document.get("fields"), list):
        raise ValueError("Unsupported team setup parameter definitions.")
    if len(document["fields"]) > 100:
        raise ValueError("A team can add up to 100 custom parameters.")
    fields, keys, labels = [], set(), set()
    for raw in document["fields"]:
        if not isinstance(raw, dict):
            raise ValueError("Invalid custom parameter.")
        key = raw.get("key")
        section = raw.get("section")
        label, unit = raw.get("label"), raw.get("unit", "")
        if not isinstance(key, str) or not re.fullmatch(r"custom_[a-f0-9]{32}", key) or key in keys:
            raise ValueError("Each custom parameter must have a unique stable field ID.")
        if raw.get("id") != key or section not in SECTIONS or raw.get("kind") not in ("num", "text"):
            raise ValueError("Invalid parameter section or value type.")
        if not isinstance(label, str) or not label.strip() or len(label) > 80 or not isinstance(unit, str) or len(unit) > 24:
            raise ValueError("Enter a parameter label of 1–80 characters and units of at most 24 characters.")
        active = raw.get("active", True)
        if not isinstance(active, bool):
            raise ValueError("Invalid parameter visibility.")
        identity = (section, label.strip().casefold())
        if active and identity in labels:
            raise ValueError("Active custom parameters in a section need different labels.")
        if active:
            labels.add(identity)
        keys.add(key)
        fields.append({"id": key, "key": key, "section": section, "label": label.strip(),
                       "unit": unit.strip(), "kind": raw["kind"], "active": active})
    return {"schema": 1, "fields": fields}


def add_parameter(document, section, label, unit="", kind="text"):
    document = normalize(document)
    key = "custom_" + uuid4().hex
    document["fields"].append({"id": key, "key": key, "section": section,
                                "label": label, "unit": unit, "kind": kind, "active": True})
    return normalize(document)


def load():
    import database.client as db
    return normalize(db.fetch_app_doc(DOC_KEY))


def apply(document):
    import setup_fields as sf
    document = normalize(document)
    sf.CUSTOM_FIELD_DEFINITIONS = deepcopy(document)
    return document
