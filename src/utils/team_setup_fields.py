"""Team-specific setup parameter definitions; existing field keys never change."""
from copy import deepcopy
import re
from uuid import uuid4
from inventory_fields import BUILTIN_CATEGORIES, BASELINE_SETUP_CATEGORY

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
    sections = document.get("sections", {})
    if (not isinstance(sections, dict) or len(sections) > 30
            or any(not isinstance(key, str) or not re.fullmatch(r"section_[a-f0-9]{32}", key)
                   or not isinstance(label, str) or not label.strip() or len(label) > 80
                   for key, label in sections.items())):
        raise ValueError("Invalid custom setup sections.")
    section_names = list(SECTIONS.values()) + list(sections.values())
    if len({label.strip().casefold() for label in section_names}) != len(section_names):
        raise ValueError("Setup sections need different names.")
    types = document.get("part_types", [])
    if not isinstance(types, list) or len(types) > 50:
        raise ValueError("A team can add up to 50 part types.")
    normalized_types, names = [], {name.casefold() for name in (*BUILTIN_CATEGORIES, BASELINE_SETUP_CATEGORY, "All Parts")}
    for row in types:
        if not isinstance(row, dict):
            raise ValueError("Invalid part type.")
        name = row.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or name.strip().casefold() in names:
            raise ValueError("Choose a unique part type name (up to 80 characters).")
        names.add(name.strip().casefold())
        attributes = row.get("fields", [])
        if not isinstance(attributes, list) or len(attributes) > 40:
            raise ValueError("A part type can have up to 40 attributes.")
        specs, spec_keys, spec_labels = [], set(), set()
        for item in attributes:
            if not isinstance(item, dict):
                raise ValueError("Invalid part attribute.")
            key, label, unit = item.get("key"), item.get("label"), item.get("unit", "")
            if (not isinstance(key, str) or not re.fullmatch(r"spec_[a-f0-9]{32}", key)
                    or key in spec_keys or not isinstance(label, str) or not label.strip() or len(label) > 80
                    or label.strip().casefold() in spec_labels or not isinstance(unit, str) or len(unit) > 24
                    or item.get("kind") not in ("text", "number", "integer", "date")):
                raise ValueError("Part attributes need unique names and valid value types.")
            spec_keys.add(key)
            spec_labels.add(label.strip().casefold())
            specs.append(dict(key=key, label=label.strip(), kind=item["kind"], unit=unit.strip()))
        normalized_types.append({"name": name.strip(), "fields": specs})
    categories = set(BUILTIN_CATEGORIES) | {row["name"] for row in normalized_types}
    fields, keys, labels = [], set(), set()
    for raw in document["fields"]:
        if not isinstance(raw, dict):
            raise ValueError("Invalid custom parameter.")
        key = raw.get("key")
        section = raw.get("section")
        label, unit = raw.get("label"), raw.get("unit", "")
        if not isinstance(key, str) or not re.fullmatch(r"custom_[a-f0-9]{32}", key) or key in keys:
            raise ValueError("Each custom parameter must have a unique stable field ID.")
        if raw.get("id") != key or section not in {**SECTIONS, **sections} or raw.get("kind") not in ("num", "text", "part"):
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
        field = {"id": key, "key": key, "section": section, "label": label.strip(),
                 "unit": unit.strip(), "kind": "text" if raw["kind"] == "part" else raw["kind"], "active": active}
        # Keep schema-1 text storage readable by older clients; category adds
        # picker behavior without introducing an incompatible stored value type.
        if raw["kind"] == "part" or (raw["kind"] == "text" and raw.get("category") is not None):
            if raw.get("category") not in categories:
                raise ValueError("Choose an existing Parts category for this field.")
            field["category"] = raw["category"]
        fields.append(field)
    result = {"schema": 1, "fields": fields}
    overrides = document.get("builtin_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("Invalid original field captions.")
    import setup_fields as sf
    original_keys = {field.key for field in sf.all_value_fields() if not field.key.startswith("custom_")}
    captions = {}
    for key, value in overrides.items():
        if (key not in original_keys or not isinstance(value, dict)
                or set(value) - {"label", "unit"}
                or not isinstance(value.get("label"), str) or not value["label"].strip()
                or len(value["label"]) > 80 or not isinstance(value.get("unit", ""), str)
                or len(value.get("unit", "")) > 24):
            raise ValueError("Original fields can have a name and units, but keep their original value type.")
        captions[key] = {"label": value["label"].strip(), "unit": value.get("unit", "").strip()}
    if captions:
        result["builtin_overrides"] = captions
    order = document.get("field_order", {})
    if not isinstance(order, dict):
        raise ValueError("Invalid setup field order.")
    groups = {"front": sf.FRONT_FIELDS, "rear": sf.REAR_FIELDS, "engine": sf.ENGINE_FIELDS,
              "tires": sf.TIRE_FIELDS, "session": sf.RUN_FIELDS}
    for section, keys in order.items():
        allowed = {item.key for item in groups.get(section, [])} | {
            item["key"] for item in fields if item["section"] == section}
        if (section not in {**SECTIONS, **sections} or not isinstance(keys, list)
                or any(not isinstance(key, str) or key not in allowed for key in keys)
                or len(keys) != len(set(keys))):
            raise ValueError("Field order must contain unique fields from the same section.")
    if order:
        result["field_order"] = deepcopy(order)
    if sections:
        result["sections"] = {key: label.strip() for key, label in sections.items()}
    if normalized_types:
        result["part_types"] = normalized_types
    return result


def add_section(document, label):
    document = normalize(document)
    key = "section_" + uuid4().hex
    document.setdefault("sections", {})[key] = label
    return normalize(document), key


def add_part_type(document, name, attributes):
    document = normalize(document)
    document.setdefault("part_types", []).append({"name": name, "fields": attributes})
    return normalize(document)


def add_parameter(document, section, label, unit="", kind="text", category=None):
    document = normalize(document)
    key = "custom_" + uuid4().hex
    document["fields"].append({"id": key, "key": key, "section": section,
                                "label": label, "unit": unit, "kind": kind, "active": True,
                                "category": category if kind == "part" else None})
    return normalize(document)


def edit_parameter(document, key, section, label, unit="", kind="text", category=None):
    document = normalize(document)
    field = next((row for row in document["fields"] if row["key"] == key), None)
    if field is None:
        raise ValueError("Select a custom field to edit.")
    field.update(section=section, label=label, unit=unit, kind=kind,
                 category=category if kind == "part" else None)
    return normalize(document)


def load():
    import database.client as db
    return normalize(db.fetch_app_doc(DOC_KEY))


def save(document):
    import database.client as db
    document = normalize(document)
    db.save_app_doc(DOC_KEY, document)


def apply(document):
    import setup_fields as sf
    from inventory_fields import apply_part_types
    document = normalize(document)
    apply_part_types(document.get("part_types", []))
    sf.CUSTOM_FIELD_DEFINITIONS = deepcopy(document)
    sf.INVENTORY_FIELD_CATEGORIES.clear()
    sf.INVENTORY_FIELD_CATEGORIES.update(sf.BUILTIN_INVENTORY_FIELD_CATEGORIES)
    sf.INVENTORY_FIELD_CATEGORIES.update({item["key"]: item["category"]
        for item in document["fields"] if item["active"] and item.get("category")})
    return document
