"""Readiness of stored intake; saving raw text is not nutrition completion."""
from .catalog import number
from .nutrients import CORE_NUTRIENTS


def assess(event, catalog):
    if event.get("kind") != "meal":
        raise ValueError("meal_event_required")
    components = []
    for index, component in enumerate(event.get("components", [])):
        row = {"index": index, "nutrition_ref": component["nutrition_ref"],
               "amount": str(number(component["amount"], positive=True)),
               "unit": component["unit"], "certainty": component["certainty"]}
        try:
            profile = catalog.get(component["nutrition_ref"])
        except ValueError:
            row.update(intake_known=False, ready=False, reason="missing_or_invalid_reference",
                       missing_nutrients=list(CORE_NUTRIENTS))
        else:
            row.update(food_id=profile["food_id"], name=profile["name"], intake_known=True,
                       missing_nutrients=[key for key in CORE_NUTRIENTS if key not in profile["nutrients"] and key not in component.get("provided_nutrients", {})])
            if profile["review_state"] != "verified":
                row.update(ready=False, reason="unreviewed_component")
            elif profile["basis"]["unit"] != component["unit"]:
                row.update(ready=False, reason="unit_mismatch")
            else:
                row.update(ready=not row["missing_nutrients"],
                           reason="missing_core_nutrients" if row["missing_nutrients"] else None)
        components.append(row)
    intake_known = bool(components) and all(row["intake_known"] for row in components)
    ready = bool(components) and all(row["ready"] for row in components)
    return {"event_id": event.get("event_id"), "intake_known": intake_known,
            "calculation_ready": ready, "state": "ready_for_recorded_components" if ready else "pending",
            "missing_intake": [] if intake_known else (["food_identity_or_resolved_reference"] if components else ["food_identity", "consumed_quantity_with_basis"]),
            "components": components,
            "note": "Raw text is preserved. Readiness covers structured components only, not an entire day or omitted foods."}
