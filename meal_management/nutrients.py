"""Explicit nutrient metadata and numeric bounds; configuration never executes code."""
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import MappingProxyType

CORE_NUTRIENTS = ("energy_kcal", "carbohydrate_g", "protein_g", "fat_g")
BUILTINS = (
    ("energy_kcal", "열량", "kcal"), ("carbohydrate_g", "탄수화물", "g"),
    ("protein_g", "단백질", "g"), ("fat_g", "지방", "g"),
    ("fiber_g", "식이섬유", "g"), ("sodium_mg", "나트륨", "mg"),
    ("sugars_g", "당류", "g"), ("saturated_fat_g", "포화지방", "g"),
)


def bound(value):
    if isinstance(value, bool):
        raise ValueError("invalid_nutrient_bound")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid_nutrient_bound") from None
    if not result.is_finite() or result < 0 or len(result.as_tuple().digits) > 24 or abs(result.as_tuple().exponent) > 24:
        raise ValueError("invalid_nutrient_bound")
    return result


class NutrientRegistry:
    def __init__(self, definitions=()):
        rows = {key: {"id": key, "label": label, "unit": unit}
                for key, label, unit in BUILTINS}
        if not isinstance(definitions, (list, tuple)) or len(definitions) > 128:
            raise ValueError("bounded_nutrient_definitions_required")
        for item in definitions:
            if (not isinstance(item, dict) or set(item) - {"id", "label", "unit", "minimum", "maximum"}
                    or not {"id", "label", "unit"} <= set(item)):
                raise ValueError("invalid_nutrient_definition")
            key, label, unit = item["id"], item["label"], item["unit"]
            if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                    or not isinstance(label, str) or not 1 <= len(label.strip()) <= 80
                    or not isinstance(unit, str) or unit not in {"kcal", "g", "mg", "ug"} or not key.endswith("_" + unit)):
                raise ValueError("invalid_nutrient_definition")
            if key in rows:
                raise ValueError("nutrient_definition_conflict")
            if bound(item.get("minimum", 0)) > bound(item.get("maximum", "1e24")):
                raise ValueError("invalid_nutrient_bounds")
            rows[key] = dict(item)
        self._rows = MappingProxyType({key: MappingProxyType(row) for key, row in rows.items()})

    @property
    def ids(self):
        return frozenset(self._rows)

    def definition(self, key):
        if key not in self._rows:
            raise ValueError("unknown_nutrient")
        return dict(self._rows[key])

    def selected(self, extra=None):
        if extra is None:
            extra = ()
        if not isinstance(extra, (list, tuple)) or not all(isinstance(k, str) for k in extra):
            raise ValueError("nutrient_selection_required")
        if set(extra) - self.ids:
            raise ValueError("unknown_nutrient")
        return tuple(dict.fromkeys((*CORE_NUTRIENTS, *extra)))

    def validate_value(self, key, value):
        row = self.definition(key)
        numeric = bound(value)
        if numeric < bound(row.get("minimum", 0)) or ("maximum" in row and numeric > bound(row["maximum"])):
            raise ValueError("nutrient_value_out_of_bounds")

    def document(self):
        return {"schema_version": 1, "core": list(CORE_NUTRIENTS),
                "definitions": [dict(row) for row in self._rows.values()]}


DEFAULT_REGISTRY = NutrientRegistry()


def load_registry(path=None):
    if path is None:
        return DEFAULT_REGISTRY
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("bounded_nutrient_config_required")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schema_version", "definitions"} or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("unsupported_nutrient_config")
    return NutrientRegistry(value["definitions"])
