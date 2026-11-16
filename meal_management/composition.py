"""Deterministic, no-write nutrition composition from immutable references."""
import hashlib
import copy
from decimal import Decimal

from .catalog import canonical, number, validate_components, validate_record
from .nutrients import CORE_NUTRIENTS, DEFAULT_REGISTRY


def decimal_text(value):
    return format(value.normalize(), "f")


def compose(payload, registry=None):
    registry = registry or DEFAULT_REGISTRY
    if (not isinstance(payload, dict) or set(payload) - {"identity", "components", "references", "estimate_reason"}
            or not {"identity", "components", "references"} <= set(payload)):
        raise ValueError("composition_payload_required")
    identity, components, references = payload["identity"], copy.deepcopy(payload["components"]), payload["references"]
    reason = payload.get("estimate_reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("portion_estimate_reason_required")
    if (not isinstance(identity, dict) or set(identity) - {"food_id", "name", "brand"}
            or not isinstance(references, dict)):
        raise ValueError("composition_identity_required")
    validate_components(components)
    resolved = []
    for component in components:
        ref = component["nutrition_ref"]
        if ref not in references:
            raise ValueError("composition_reference_missing")
        record = validate_record(references[ref], registry)
        if hashlib.sha256(canonical(record).encode()).hexdigest() != ref:
            raise ValueError("composition_reference_hash_mismatch")
        if component["unit"] != record["basis"]["unit"]:
            raise ValueError("composition_unit_mismatch")
        if set(CORE_NUTRIENTS) - set(record["nutrients"]):
            raise ValueError("component_missing_core_nutrients")
        resolved.append((component, record))
    if reason is not None:
        if len(resolved) != 1:
            raise ValueError("apply_estimate_to_one_portion_component")
        component, original = resolved[0]
        if "amount_range" not in component:
            whole = number(original["basis"]["quantity"], positive=True)
            amount = number(component["amount"], positive=True)
            if not amount < whole:
                raise ValueError("explicit_bounds_required_for_non_fraction_estimate")
            component["amount_range"] = {
                "low": "0", "high": decimal_text(whole),
                "reason": "Assume the actual portion lies between zero and the original whole serving; "
                          "these are conservative assumed limits, not measured error or a confidence interval. " + reason,
            }
    shared = set.intersection(*(set(record["nutrients"]) for _, record in resolved))
    totals, ranges = {}, {}
    estimated = any("amount_range" in c or "uncertainty" in r for c, r in resolved)
    for nutrient in registry.ids & shared:
        total = low_total = high_total = Decimal(0)
        for component, record in resolved:
            basis = number(record["basis"]["quantity"], positive=True)
            amount = number(component["amount"], positive=True)
            bounds = component.get("amount_range", {"low": amount, "high": amount})
            low_amount, high_amount = number(bounds["low"]), number(bounds["high"])
            value = number(record["nutrients"][nutrient])
            source_ranges = record.get("uncertainty", {}).get("ranges", {})
            if "uncertainty" in record and nutrient not in source_ranges:
                raise ValueError("component_uncertainty_range_missing")
            source_bounds = source_ranges.get(nutrient, {"low": value, "high": value})
            total += value * amount / basis
            low_total += number(source_bounds["low"]) * low_amount / basis
            high_total += number(source_bounds["high"]) * high_amount / basis
        totals[nutrient] = decimal_text(total)
        if estimated:
            ranges[nutrient] = {"low": decimal_text(low_total), "high": decimal_text(high_total)}
    first_source = resolved[0][1]["source"]
    record = {
        **identity, "basis": {"quantity": "1", "unit": "serving"}, "nutrients": totals,
        "source": {k: v for k, v in first_source.items() if k != "evidence_quote"},
        "review_state": "unreviewed", "components": components,
        "preparation": "Computed from component references and amounts; this record is one composed serving. "
                       "Source indexes the first component; follow all component references for original evidence.",
    }
    if estimated:
        reasons = []
        for component, source in resolved:
            if "amount_range" in component:
                reasons.append(component["amount_range"]["reason"])
            if "uncertainty" in source:
                reasons.append(source["uncertainty"]["reason"])
        record["uncertainty"] = {
            "kind": "estimated",
            "reason": "Assumed portion bounds, not measured confidence intervals. " + "; ".join(dict.fromkeys(reasons)),
            "ranges": ranges,
        }
    return validate_record(record, registry)
