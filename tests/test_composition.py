"""Numeric protocol vectors only; no invented food/reference dataset."""
import copy
import contextlib
import io
import json
from unittest.mock import patch
import hashlib
import unittest

from meal_management.catalog import Catalog, canonical, validate_record
from meal_management.__main__ import main
from meal_management.composition import compose


class CompositionTests(unittest.TestCase):
    def record(self, marker, values):
        return {"food_id": marker, "name": marker, "basis": {"quantity": "2", "unit": "g"},
                "nutrients": dict(zip(("energy_kcal", "carbohydrate_g", "protein_g", "fat_g"), values)),
                "source": {"url": "https://example.invalid/protocol", "observed_on": "2001-01-01",
                           "publisher": "protocol-only"}, "review_state": "unreviewed"}

    def ref(self, record):
        return hashlib.sha256(canonical(record).encode()).hexdigest()

    def request(self):
        a = self.record("__numeric_a__", ["20", "4", "2", "1"])
        b = self.record("__numeric_b__", ["10", "2", "1", "0"])
        ra, rb = self.ref(a), self.ref(b)
        return {"identity": {"food_id": "__sum__", "name": "__numeric_sum__"},
                "components": [{"nutrition_ref": ra, "amount": "1", "unit": "g"},
                               {"nutrition_ref": rb, "amount": "2", "unit": "g"}],
                "references": {ra: a, rb: b}}

    def test_ref_amount_sum_keeps_basis_and_source_inputs_immutable(self):
        request = self.request()
        original = copy.deepcopy(request)
        result = compose(request)
        self.assertEqual(result["nutrients"], {"energy_kcal": "20", "carbohydrate_g": "4",
                                               "protein_g": "2", "fat_g": "0.5"})
        self.assertEqual(result["basis"], {"quantity": "1", "unit": "serving"})
        self.assertEqual(result["components"], request["components"])
        self.assertEqual(result["review_state"], "unreviewed")
        self.assertEqual(request, original)
        validate_record(result)

    def test_nested_fraction_propagates_assumed_bounds_without_double_halving(self):
        request = self.request()
        request["components"] = request["components"][:1]
        request["components"][0]["amount_range"] = {"low": "0.8", "high": "1.2", "reason": "__assumed_portion__"}
        half = compose(request)
        self.assertEqual(half["nutrients"]["energy_kcal"], "10")
        self.assertEqual(half["uncertainty"]["ranges"]["energy_kcal"], {"low": "8", "high": "12"})
        nested = {"identity": {"food_id": "__nested__", "name": "__nested_vector__"},
                  "components": [{"nutrition_ref": self.ref(half), "amount": "1", "unit": "serving"}],
                  "references": {self.ref(half): half}}
        result = compose(nested)
        self.assertEqual(result["nutrients"], half["nutrients"])
        self.assertEqual(result["uncertainty"]["ranges"], half["uncertainty"]["ranges"])

    def test_hash_unit_missing_core_and_invalid_range_fail(self):
        request = self.request()
        bad = copy.deepcopy(request)
        first = bad["components"][0]["nutrition_ref"]
        bad["references"][first]["nutrients"]["energy_kcal"] = "21"
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            compose(bad)
        bad = copy.deepcopy(request);bad["components"][0]["unit"] = "serving"
        with self.assertRaisesRegex(ValueError, "unit_mismatch"):
            compose(bad)
        bad = copy.deepcopy(request);bad["components"][0]["amount_range"] = {"low":"2","high":"3","reason":"__bounds__"}
        with self.assertRaisesRegex(ValueError, "component_amount_range"):
            compose(bad)
        bad = copy.deepcopy(request)
        entry = bad["references"].pop(first);entry["nutrients"].pop("fat_g")
        new_ref = self.ref(entry);bad["references"][new_ref] = entry;bad["components"][0]["nutrition_ref"] = new_ref
        with self.assertRaisesRegex(ValueError, "missing_core"):
            compose(bad)

    def test_cli_composition_is_no_write_and_returns_a_valid_ref(self):
        request = self.request()
        out = io.StringIO()
        with patch("sys.stdin",io.StringIO(json.dumps(request))), contextlib.redirect_stdout(out), patch.object(Catalog,"put") as write:
            code = main(["catalog-compose"])
        self.assertEqual(code,0)
        result = json.loads(out.getvalue())
        self.assertFalse(result["stored"])
        self.assertEqual(result["nutrition_ref"],self.ref(result["record"]))
        write.assert_not_called()

    def test_unmeasured_fraction_reason_uses_explicit_wide_bounds_not_fake_precision(self):
        request = self.request()
        request["components"] = request["components"][:1]
        request["estimate_reason"] = "__unmeasured_fraction__"
        original = copy.deepcopy(request)
        result = compose(request)
        self.assertEqual(result["nutrients"]["energy_kcal"],"10")
        self.assertEqual(result["uncertainty"]["ranges"]["energy_kcal"],{"low":"0","high":"20"})
        self.assertIn("not measured",result["uncertainty"]["reason"])
        self.assertEqual(request,original)
        request["components"][0]["amount"]="3"
        with self.assertRaisesRegex(ValueError,"explicit_bounds_required"):
            compose(request)
