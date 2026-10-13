"""
Kart tuning cheat sheet data.

TUNING_TABLE is transcribed from the team's "Kart Setup Cheat Sheet". Each row is
(component, adjustment, {effect: "+"/"-"}) across the effect columns. A "+" means
that adjustment increases the effect, "-" decreases it.

MECHANICS_NOTES adds common chassis-tuning context that pairs with the table.
"""
from __future__ import annotations

EFFECT_COLUMNS = [
    "Turn-In",
    "Weight Jacking",
    "Weight Jacking Rate",
    "Inside Rear Lift",
    "Contact Patch",
    "Tire Temp",
    "Steering Weight",
    "Side Bite",
]

# (component, adjustment, effects dict)
TUNING_TABLE = [
    ("Front Track Width", "Wider (+)",
     {"Turn-In": "+", "Weight Jacking": "+", "Inside Rear Lift": "+"}),
    ("Front Track Width", "Narrower (-)",
     {"Turn-In": "-", "Weight Jacking": "-", "Inside Rear Lift": "-"}),

    ("Front Torsion Bar", "Stiffer (+)",
     {"Weight Jacking Rate": "+"}),
    ("Front Torsion Bar", "Softer (-)",
     {"Weight Jacking Rate": "-"}),

    ("Caster", "More (+)",
     {"Weight Jacking": "+", "Inside Rear Lift": "+", "Steering Weight": "+"}),
    ("Caster", "Less (-)",
     {"Weight Jacking": "-", "Inside Rear Lift": "-", "Steering Weight": "-"}),

    ("Camber", "Positive (+)",
     {"Turn-In": "-", "Contact Patch": "+", "Tire Temp": "+"}),
    ("Camber", "Negative (-)",
     {"Turn-In": "+", "Contact Patch": "-", "Tire Temp": "-"}),

    ("Front Ride Height", "Raise (+)",
     {"Turn-In": "+", "Steering Weight": "+"}),
    ("Front Ride Height", "Lower (-)",
     {"Turn-In": "-", "Steering Weight": "-"}),

    ("Rear Track Width", "Wider (+)",
     {"Inside Rear Lift": "-"}),
    ("Rear Track Width", "Narrower (-)",
     {"Inside Rear Lift": "+"}),

    ("Rear Ride Height", "Higher (+)",
     {"Weight Jacking": "+", "Weight Jacking Rate": "+", "Side Bite": "+"}),
    ("Rear Ride Height", "Lower (-)",
     {"Weight Jacking": "-", "Weight Jacking Rate": "-", "Side Bite": "-"}),

    ("Rear Bumper", "Tight (+)",
     {"Inside Rear Lift": "-"}),
    ("Rear Bumper", "Loose (-)",
     {"Inside Rear Lift": "+"}),
]

# Extra context beyond the raw table — common karting mechanics.
MECHANICS_NOTES = [
    ("Understeer (push / won't turn in)",
     "Widen front track, add front width or caster, lower front ride height, or "
     "soften the front torsion bar. Too much rear grip also causes push — try "
     "narrowing rear track or lowering rear ride height."),
    ("Oversteer (loose / snappy rear)",
     "Narrow the front track, reduce caster, or raise the front. Add rear grip by "
     "widening rear track (up to limit), raising rear ride height, or loosening the "
     "rear bumper. Check rear tire pressures aren't too high."),
    ("Mid-corner grip",
     "Camber controls contact patch and tire temps — aim for even Outer/Middle/Inner "
     "temps across the tire. If outers are hot, add negative camber; if inners are "
     "hot, reduce it."),
    ("Tire pressures",
     "Higher cold pressure = quicker heat-up and more initial grip but can overheat "
     "and go greasy. Lower pressure builds grip more slowly but lasts longer. Read "
     "hot pressures and tire temps to dial the window."),
    ("Weight jacking",
     "Jacking transfers load diagonally to lift the inside rear and free the kart "
     "through the corner. Front width, caster, and rear ride height are the main "
     "levers; 'rate' is how quickly it happens on turn-in."),
    ("Gearing",
     "Lower total ratio (taller gear) = more top speed, less punch out of slow "
     "corners; higher ratio = more acceleration. Tune to hit target max RPM at the "
     "end of the longest straight without bogging on exits."),
    ("Clutch engagement (slip)",
     "Higher slip RPM keeps the engine in the powerband off slow corners; lower slip "
     "is smoother and easier on the clutch. Team baseline: ~9800 race / ~9500 "
     "practice."),
    ("Carburetor jetting",
     "Low jet controls off-idle/low-RPM, high jet controls top-end. Tune off a clean "
     "4-cycle (four-stroking) on the straight. Richer (more turns out) for cold/dense "
     "air, leaner for hot air — always err rich to protect the motor."),
]


def default_document() -> dict:
    """The built-in cheat sheet as a shareable JSON document.

    Used to seed the shared copy and as a fallback when the team doc hasn't
    been created yet. Structure matches what CheatSheetView reads/writes.
    """
    return {
        "effect_columns": list(EFFECT_COLUMNS),
        "tuning_table": [
            {"component": component, "adjustment": adjustment, "effects": dict(effects)}
            for (component, adjustment, effects) in TUNING_TABLE
        ],
        "mechanics_notes": [
            {"title": title, "body": body} for (title, body) in MECHANICS_NOTES
        ],
    }


def normalize_document(doc: dict | None) -> dict:
    """Coerce a stored doc into the expected shape, filling from defaults."""
    base = default_document()
    if not isinstance(doc, dict):
        return base
    cols = doc.get("effect_columns")
    if isinstance(cols, list) and cols:
        base["effect_columns"] = [str(c) for c in cols]
    rows = doc.get("tuning_table")
    if isinstance(rows, list):
        base["tuning_table"] = [
            {
                "component": str(r.get("component", "")),
                "adjustment": str(r.get("adjustment", "")),
                "effects": {str(k): str(v) for k, v in (r.get("effects") or {}).items()},
            }
            for r in rows if isinstance(r, dict)
        ]
    notes = doc.get("mechanics_notes")
    if isinstance(notes, list):
        base["mechanics_notes"] = [
            {"title": str(n.get("title", "")), "body": str(n.get("body", ""))}
            for n in notes if isinstance(n, dict)
        ]
    return base
