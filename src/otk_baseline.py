"""Built-in, editable OTK dry baseline derived from the OTK setup guide."""
from __future__ import annotations

from copy import deepcopy

from database.models import Setup


TEMPLATE_ID = "otk-tony-kart-dry-v1"
BUILTIN_ID = f"builtin:{TEMPLATE_ID}"

_DATA = {
    "_template_id": TEMPLATE_ID,
    "_template_source": "OTK CHASSIS - SET UP GUIDE, Standard Dry Set-Up",
    "_universal_baseline": True,
    "upper_castor_camber": "FWD (Arrow forward)",
    "lower_castor_camber": "FWD (Arrow forward)",
    "front_left_camber": "0 ticks",
    "front_right_camber": "0 ticks",
    "toe": "half box toe-out (1mm)",
    "torsion_bar": "Flat OTK",
    "front_ride_height": "MID (middle height)",
    "ackerman": "BOTTOM HOLE",
    "axle": "N 1030 mm",
    "cage_spring_preload": "1-2",
    "rear_ride_height": "MID (middle height)",
    "front_track_spacers": "1 large spacer (15mm)",
    "front_hub_material": "Standard OTK hubs",
    "rear_track": "54 3/4 - 55 1/8",
    "rear_hub_material": "Standard OTK hubs",
    "rim_type": "OTK MXJ",
    "rear_rim_type": "OTK MXJ",
    "clutch_slip": "9500-10400",
    "carburetor": "Pop (8-12 psi) Fulc (.045-.055 in)",
    "needle_settings": "L 2 1/4 - 2 3/4 H 1/8 - 1/2",
    "flex_length": "7 3/4- 8 1/2",
    "exhaust_pipe": "L2",
    "oil_mix": "8 oz per gallon",
    "fuel": "110 Race Fuel",
}

_NOTES = """OTK dry-condition starting point from the OTK Chassis Set-Up Guide.

Cage spring preload: 1-2 turns, added as the team's requested starting value.

Also specified by the guide but not represented by a standard xCAL field: no rear torsion bar and one supplementary seat stay on each side. Seat position depends on driver height. Tire pressures, temperatures, gearing, carburetor settings, and other unspecified values are intentionally blank.

Review every value for the kart model, class, tires, driver, track, weather, and applicable rules before use. This is a universal baseline and is not tied to a driver, kart inventory record, or track."""


def template() -> Setup:
    """Return the read-only catalog representation shipped with every team."""
    return Setup(
        id=BUILTIN_ID,
        name="OTK Tony Kart Dry Baseline (Guide Draft)",
        is_baseline=True,
        data=deepcopy(_DATA),
        notes=_NOTES,
    )


def editable_copy(source: Setup | None = None) -> Setup:
    """Detach the built-in draft so saving creates this team's own baseline."""
    copied = deepcopy(source or template())
    copied.id = None
    copied.created_at = None
    copied.updated_at = None
    return copied


def is_builtin(setup) -> bool:
    return isinstance(setup, Setup) and setup.id == BUILTIN_ID


def is_universal(setup) -> bool:
    return (
        isinstance(setup, Setup)
        and bool((setup.data or {}).get("_universal_baseline")))


def add_when_team_has_no_copy(items) -> list:
    """Expose one draft until this team saves its editable copy."""
    rows = list(items or [])
    # Supply the newly introduced default for saved OTK templates only when
    # the field is absent. Explicit values (including blanks) remain intact.
    for index, setup in enumerate(rows):
        if (isinstance(setup, Setup) and setup.is_baseline
                and (setup.data or {}).get("_template_id") == TEMPLATE_ID
                and "cage_spring_preload" not in (setup.data or {})):
            setup = deepcopy(setup)
            setup.data["cage_spring_preload"] = "1-2"
            rows[index] = setup
    if not any(
            isinstance(item, Setup)
            and (item.data or {}).get("_template_id") == TEMPLATE_ID
            and not is_builtin(item)
            for item in rows):
        rows.append(template())
    return rows
