"""Intake-only user-declared nutrients, never written into the shared food catalog."""
import re
from .catalog import number


def validate_provided(component):
    present = {"provided_nutrients", "provided_basis"} & set(component)
    if not present:
        return
    values, basis = component.get("provided_nutrients"), component.get("provided_basis")
    if (present != {"provided_nutrients", "provided_basis"} or not isinstance(values, dict)
            or not values or not all(isinstance(k, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", k) for k in values)
            or not isinstance(basis, dict) or set(basis) != {"quantity", "unit"}
            or basis["unit"] != component["unit"]):
        raise ValueError("invalid_intake_provided_nutrients")
    number(basis["quantity"], positive=True)
    for value in values.values():
        number(value)


def nutrient_values(profile, component):
    validate_provided(component)
    factor = number(component["amount"], True) / number(profile["basis"]["quantity"], True)
    values = {key: number(value) * factor for key, value in profile["nutrients"].items()}
    if component.get("provided_nutrients"):
        factor = number(component["amount"], True) / number(component["provided_basis"]["quantity"], True)
        values.update({key: number(value) * factor for key, value in component["provided_nutrients"].items()})
    return values
