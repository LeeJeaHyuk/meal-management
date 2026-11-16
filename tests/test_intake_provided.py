"""Numeric protocol checks, not food or personal-intake fixtures."""
import copy
import tempfile
import unittest
from pathlib import Path
from meal_management.catalog import Catalog
from meal_management.journal import Journal, validated
from meal_management.reporting import build


class IntakeProvidedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.catalog = Catalog(root / "catalog")
        self.profile = {"food_id": "__protocol__", "name": "__protocol__",
                        "basis": {"quantity": 1, "unit": "serving"},
                        "nutrients": {"energy_kcal": 20, "carbohydrate_g": 3, "protein_g": 1, "fat_g": 0},
                        "source": {"url": "https://example.invalid/protocol", "observed_on": "2000-01-01", "publisher": "protocol"},
                        "review_state": "verified"}
        self.ref = self.catalog.put(self.profile)["nutrition_ref"]
        self.event = {"actor": "protocol", "channel": "protocol", "message_id": "protocol",
                      "received_at": "2000-01-01T00:00:00+00:00", "occurred_on": "2000-01-01",
                      "kind": "meal", "text": "__protocol__",
                      "components": [{"nutrition_ref": self.ref, "amount": 2, "unit": "serving",
                                      "certainty": "confirmed", "provided_nutrients": {"energy_kcal": 30, "protein_g": 4},
                                      "provided_basis": {"quantity": 1, "unit": "serving"}}]}
        self.journal = Journal(root / "journal.sqlite3")
        self.journal.init()

    def test_values_scale_prioritize_partial_input_and_do_not_change_catalog(self):
        self.journal.record(self.event)
        report = build(self.journal, self.catalog, "2000-01-01", "2000-01-01", include_statistics=True)
        self.assertEqual(report["nutrition_confirmed_partial"],
                         {"energy_kcal": "60", "carbohydrate_g": "6", "protein_g": "8", "fat_g": "0"})
        self.assertEqual(self.catalog.get(self.ref), self.profile)
        self.assertEqual(report["calculation_state"], "ready_for_recorded_meals")
        self.assertEqual(report["component_details"][0]["provided_nutrient_ids"], ["energy_kcal", "protein_g"])

    def test_unprovided_missing_values_remain_missing(self):
        profile = copy.deepcopy(self.profile)
        profile["nutrients"].pop("fat_g")
        self.event["components"][0]["nutrition_ref"] = self.catalog.put(profile)["nutrition_ref"]
        self.journal.record(self.event)
        report = build(self.journal, self.catalog, "2000-01-01", "2000-01-01")
        self.assertEqual(report["calculation_state"], "partial_or_pending")
        self.assertNotIn("fat_g", report["nutrition_confirmed_partial"])

    def test_incomplete_pair_incompatible_unit_and_negative_input_rejected(self):
        for change in ["missing_basis", "unit", "negative"]:
            event = copy.deepcopy(self.event)
            c = event["components"][0]
            if change == "missing_basis": c.pop("provided_basis")
            if change == "unit": c["provided_basis"]["unit"] = "g"
            if change == "negative": c["provided_nutrients"]["energy_kcal"] = -1
            with self.subTest(change=change), self.assertRaises(ValueError):
                validated(event)
