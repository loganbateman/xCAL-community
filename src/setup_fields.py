"""
Single source of truth for the kart setup builder.

MAIN fields use the team's real setup-sheet vocabulary (from the Redspeed/Birel
Google Sheets): castor/camber hole positions, spacers, flex length, carb pop-off,
exhaust, wheels, needle settings, etc. The optional DETAIL section keeps the
per-corner tire data (temps, hot/cold pressures) for trackside logging.

Values persist in a JSONB `data` bag keyed by Field.key, so adding a field is a
one-line change with no database migration.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str            # stable storage key (also used in baseline dicts)
    label: str          # shown to the user
    unit: str = ""      # e.g. "psi", "in"
    kind: str = "num"   # "num" | "text"

    @property
    def display_label(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label


# ── Run / session meta (top bar) ─────────────────────────────────────────────
META_FIELDS = [
    Field("kart_number", "Kart #", "", "text"),
    Field("run_date", "Date", "", "date"),
    Field("run_number", "Run #", "", "text"),
    Field("air_temp", "Air Temp", "°F"),
    Field("track_temp", "Track Temp", "°F"),
    Field("fastest_lap", "Fastest Lap", "s"),
]

# The sheet identity keeps only the kart number at the top. The remaining
# legacy meta fields now live in each entry of the unlimited run log. Keeping
# META_FIELDS unchanged preserves comparison/import compatibility for setups
# saved before the structured run log was introduced.
IDENTITY_META_FIELDS = [META_FIELDS[0]]


# Kart numbers ↔ chassis (team convention).
CHASSIS_TO_NUMBER = {}
NUMBER_TO_CHASSIS = {}


def kart_number_for(chassis_name: str | None) -> str:
    return CHASSIS_TO_NUMBER.get(chassis_name or "", "")


def chassis_for_number(number: str | None) -> str:
    return NUMBER_TO_CHASSIS.get((number or "").strip(), "")

# ── Front end (top of the kart) ──────────────────────────────────────────────
FRONT_FIELDS = [
    Field("upper_castor_camber", "Top Pill Position", "", "text"),
    Field("lower_castor_camber", "Bottom Pill Position", "", "text"),
    Field("front_left_camber", "Front Left Camber", "ticks in/out", "text"),
    Field("front_right_camber", "Front Right Camber", "ticks in/out", "text"),
    Field("toe", "Toe", "boxes", "text"),
    Field("torsion_bar", "Front Torsion Bar", "", "text"),
    Field("front_ride_height", "Front Ride Height", "", "text"),
    Field("ackerman", "Ackerman", "", "text"),
    Field("front_tire_pressure", "Front Tire Pressure", "psi"),
    Field("front_weight_pct", "Front Weight", "%"),
]

# ── Rear end (bottom of the kart) ────────────────────────────────────────────
REAR_FIELDS = [
    Field("axle", "Axle", "", "text"),
    Field("cage", "Cage", "", "text"),
    Field("rear_ride_height", "Rear Ride Height", "", "text"),
    Field("rear_tire_pressure", "Rear Tire Pressure", "psi"),
]

# ── Engine / drivetrain / carb (centre) ──────────────────────────────────────
ENGINE_FIELDS = [
    Field("engine", "Engine", "", "text"),
    Field("clutch", "Clutch", "", "text"),
    Field("gear_ratio", "Total Ratio", "", "text"),
    Field("clutch_sprocket", "Clutch Sprocket", "", "text"),
    Field("axle_sprocket", "Axle Sprocket", "", "text"),
    Field("clutch_slip", "Clutch Slip", "RPM", "text"),
    Field("carburetor", "Carburetor (pop/fulcrum)", "", "text"),
    Field("needle_settings", "Needle Settings", "", "text"),
    Field("flex_length", "Flex Length", "in", "text"),
    Field("exhaust_pipe", "Exhaust Pipe", "", "text"),
    Field("cassettes", "Cassettes", ""),
    Field("egt_peak", "EGT Peak", "°F", "text"),
    Field("oil_mix", "Oil Mix", "", "text"),
    Field("fuel", "Fuel", "", "text"),
    Field("fuel_start", "Fuel Start", "qt/kg"),
    Field("fuel_end", "Fuel End", "qt/kg"),
]

# ── Tires & wheels ───────────────────────────────────────────────────────────
TIRE_FIELDS = [
    Field("tire_compound", "Tire Compound", "", "text"),
    Field("hoosier_tire_rec", "Hoosier Tire Rec", "", "text"),
]


# ── Session & feedback (structured) ──────────────────────────────────────────
SESSION_FIELDS = [
    Field("session_length", "Session Length", "", "text"),
    Field("laps", "Laps", ""),
]
RUN_TIME_FIELD = Field("run_time", "Time", "", "time")

RUN_FIELDS = [
    META_FIELDS[1],  # run_date
    RUN_TIME_FIELD,
    META_FIELDS[2],  # run_number
    SESSION_FIELDS[0],  # session_length
    SESSION_FIELDS[1],  # laps
    META_FIELDS[3],  # air_temp
    META_FIELDS[4],  # track_temp
    META_FIELDS[5],  # fastest_lap
]

RUNS_KEY = "runs"
RUN_NOTES_KEY = "notes"
RUN_SETUP_KEY = "setup"
LEGACY_RUN_KEYS = tuple(field.key for field in RUN_FIELDS) + ("session_number",)


# ── DETAIL: axle measurements + per-corner tire data (optional) ──────────────
FRONT_TRACK_FIELDS = [
    Field("front_hub_material", "Hub Type", "", "text"),
    Field("rim_type", "Front Rims", "", "text"),
    Field("front_track_spacers", "Front Spacers", "", "text"),
    Field("front_track_width", "Overall Track Width", "mm"),
]

REAR_TRACK_FIELDS = [
    Field("rear_hub_material", "Hub Type", "", "text"),
    Field("rear_rim_type", "Rear Rims", "", "text"),
    Field("rear_track", "Overall Track Width", "", "text"),
]

# Combined axle-level details used by comparison and persistence helpers.
DETAIL_AXLE_FIELDS = FRONT_TRACK_FIELDS + REAR_TRACK_FIELDS

FRONT_CORNER_FIELDS = [
    Field("cold_psi", "Cold Pressure", "psi"),
    Field("hot_psi", "Hot Pressure", "psi"),
    Field("heat_cycles", "Heat Cycles", ""),
    Field("temp_o", "Temp Outer", "°F"),
    Field("temp_m", "Temp Middle", "°F"),
    Field("temp_i", "Temp Inner", "°F"),
]

REAR_CORNER_FIELDS = [
    Field("cold_psi", "Cold Pressure", "psi"),
    Field("hot_psi", "Hot Pressure", "psi"),
    Field("heat_cycles", "Heat Cycles", ""),
    Field("tire_condition", "Tire Condition", "", "text"),
    Field("temp_o", "Temp Outer", "°F"),
    Field("temp_m", "Temp Middle", "°F"),
    Field("temp_i", "Temp Inner", "°F"),
]

# corner code -> (display name, field list, is_front)
CORNERS = {
    "fl": ("Left Front", FRONT_CORNER_FIELDS, True),
    "fr": ("Right Front", FRONT_CORNER_FIELDS, True),
    "rl": ("Left Rear", REAR_CORNER_FIELDS, False),
    "rr": ("Right Rear", REAR_CORNER_FIELDS, False),
}


def corner_fields(code: str) -> list[Field]:
    """Return the fields for a corner, with keys prefixed (e.g. 'fl_camber')."""
    _, base_fields, _ = CORNERS[code]
    return [Field(f"{code}_{f.key}", f.label, f.unit, f.kind) for f in base_fields]


# ── Grouped views used by the builder ────────────────────────────────────────
MAIN_GROUPS = [
    ("▲ Front End", FRONT_FIELDS),
    ("⚙ Engine · Drivetrain · Carb", ENGINE_FIELDS),
    ("▼ Rear End", REAR_FIELDS),
    ("◎ Tires & Wheels", TIRE_FIELDS),
]


# Setup fields that can be selected directly from active Parts inventory. The
# combo boxes remain editable, so legacy values and trackside one-off text are
# still accepted even when no matching inventory record exists.
INVENTORY_FIELD_CATEGORIES = {
    "engine": "Engines",
    "clutch": "Clutches",
    "carburetor": "Carbs",
    "axle": "Axles",
    "torsion_bar": "Front Torsion Bar",
    "cage": "Cage",
    "exhaust_pipe": "Exhausts",
    "fuel": "Fuel",
    "tire_compound": "Tires",
    "rim_type": "Rims",
    "rear_rim_type": "Rims",
    "front_hub_material": "Front Hubs",
    "front_track_spacers": "Front Spacers",
    "rear_hub_material": "Rear Hubs",
}


CUSTOM_FIELD_DEFINITIONS = {"schema": 1, "fields": []}


def custom_fields(section=None):
    return [Field(item["key"], item["label"], item["unit"], item["kind"])
            for item in CUSTOM_FIELD_DEFINITIONS["fields"]
            if item["active"] and (section is None or item["section"] == section)]


def section_fields(section):
    builtin = {"front": FRONT_FIELDS, "rear": REAR_FIELDS,
               "engine": ENGINE_FIELDS, "tires": TIRE_FIELDS}.get(section, [])
    return list(builtin) + custom_fields(section)


def all_value_fields() -> list[Field]:
    """Every field (main + detail + corners), corner keys already prefixed."""
    fields: list[Field] = []
    fields += META_FIELDS
    fields.append(RUN_TIME_FIELD)
    for _, group in MAIN_GROUPS:
        fields += group
    fields += SESSION_FIELDS
    fields += DETAIL_AXLE_FIELDS
    for code in CORNERS:
        fields += corner_fields(code)
    fields += custom_fields()
    return fields
