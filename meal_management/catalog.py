"""Immutable public nutrition references. Personal intake never belongs here."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

NUTRIENTS = {"energy_kcal", "protein_g", "carbohydrate_g", "fat_g", "fiber_g", "sodium_mg", "sugars_g", "saturated_fat_g"}
UNITS = {"g", "ml", "serving"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def number(value, positive=False):
    if isinstance(value, bool):
        raise ValueError("invalid_number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_number") from None
    if (not result.is_finite() or result < 0 or (positive and result == 0)
            or len(result.as_tuple().digits) > 24 or abs(result.as_tuple().exponent) > 24):
        raise ValueError("invalid_number")
    return result


def iso_date(value):
    try:
        result = date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("invalid_iso_date") from None
    if result.isoformat() != value:
        raise ValueError("invalid_iso_date")
    return result


def data_path(path):
    path = Path(path).expanduser().absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("symlink_data_path")
    path = path.resolve()
    repo = Path(__file__).resolve().parent.parent
    if path == repo or repo in path.parents:
        raise ValueError("data_inside_code_repository")
    for ancestor in (path, *path.parents):
        if ((ancestor / ".git").exists()
                and any((ancestor / name).is_dir() for name in ("meal_management", "harnesses"))):
            raise ValueError("data_inside_code_repository")
    return path


def validate_record(record):
    allowed = {"food_id", "name", "brand", "preparation", "basis", "nutrients", "source", "review_state", "uncertainty"}
    if not isinstance(record, dict) or set(record) - allowed:
        raise ValueError("invalid_catalog_fields")
    for key in ("food_id", "name"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError("catalog_identity_required")
    for key in ("brand", "preparation"):
        if key in record and not isinstance(record[key], str):
            raise ValueError("invalid_catalog_text_metadata")
    basis = record.get("basis", {})
    if not isinstance(basis, dict) or set(basis) != {"quantity", "unit"} or basis["unit"] not in UNITS:
        raise ValueError("invalid_nutrition_basis")
    number(basis["quantity"], positive=True)
    nutrients = record.get("nutrients")
    if not isinstance(nutrients, dict) or not nutrients or set(nutrients) - NUTRIENTS:
        raise ValueError("invalid_nutrients")
    for value in nutrients.values():
        number(value)
    if "uncertainty" in record:
        uncertainty = record["uncertainty"]
        if (not isinstance(uncertainty, dict)
                or set(uncertainty) != {"kind", "reason", "ranges"}
                or uncertainty["kind"] != "estimated"
                or not isinstance(uncertainty["reason"], str)
                or not uncertainty["reason"].strip()):
            raise ValueError("invalid_nutrition_uncertainty")
        ranges = uncertainty["ranges"]
        if (not isinstance(ranges, dict) or not ranges
                or set(ranges) - set(nutrients)
                or ("energy_kcal" in nutrients and "energy_kcal" not in ranges)):
            raise ValueError("invalid_nutrition_ranges")
        for nutrient, bounds in ranges.items():
            if not isinstance(bounds, dict) or set(bounds) != {"low", "high"}:
                raise ValueError("invalid_nutrition_ranges")
            low, high = number(bounds["low"]), number(bounds["high"])
            if not low <= number(nutrients[nutrient]) <= high:
                raise ValueError("invalid_nutrition_ranges")
    source = record.get("source", {})
    if not isinstance(source, dict) or set(source) - {"url", "observed_on", "publisher", "evidence_sha256", "evidence_quote"}:
        raise ValueError("invalid_source_metadata")
    if not isinstance(source.get("url"), str) or not isinstance(source.get("publisher"), str):
        raise ValueError("invalid_source_metadata")
    for key in ("evidence_quote", "evidence_sha256"):
        if key in source and not isinstance(source[key], str):
            raise ValueError("invalid_source_metadata")
    if "evidence_sha256" in source and (len(source["evidence_sha256"]) != 64 or any(x not in "0123456789abcdef" for x in source["evidence_sha256"])):
        raise ValueError("invalid_source_evidence_hash")
    url = urlparse(source.get("url", ""))
    if url.scheme != "https" or not url.netloc or url.username or url.password:
        raise ValueError("public_source_url_required")
    if not source.get("observed_on") or not source.get("publisher"):
        raise ValueError("source_metadata_required")
    try:
        iso_date(source["observed_on"])
    except (ValueError, TypeError):
        raise ValueError("invalid_source_date") from None
    if record.get("review_state") not in {"unreviewed", "verified"}:
        raise ValueError("explicit_review_state_required")
    return record


class Catalog:
    def __init__(self, root):
        self.root = data_path(root)

    def put(self, record):
        validate_record(record)
        text = canonical(record)
        ref = hashlib.sha256(text.encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = self.root / (ref + ".json")
        try:
            with target.open("x", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except FileExistsError:
            if target.read_text(encoding="utf-8") != text + "\n":
                raise ValueError("catalog_integrity_mismatch") from None
        return {"nutrition_ref": ref, "review_state": record["review_state"]}

    def get(self, ref):
        if not isinstance(ref, str) or len(ref) != 64 or any(c not in "0123456789abcdef" for c in ref):
            raise ValueError("invalid_nutrition_ref")
        target = self.root / (ref + ".json")
        if target.is_symlink():
            raise ValueError("symlink_catalog_entry")
        try:
            text = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ValueError("nutrition_ref_missing") from None
        if hashlib.sha256(text.rstrip("\n").encode()).hexdigest() != ref:
            raise ValueError("catalog_integrity_mismatch")
        return json.loads(text)

    def find(self, query):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query_required")
        if not self.root.exists():
            return []
        rows = []
        for path in sorted(self.root.glob("*.json")):
            if path.is_symlink():
                raise ValueError("symlink_catalog_entry")
            record = self.get(path.stem)
            if query.casefold() in " ".join(str(record.get(k, "")) for k in ("name", "brand", "preparation")).casefold():
                rows.append({"nutrition_ref": path.stem, **record})
        return rows
