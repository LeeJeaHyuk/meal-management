"""Ephemeral numeric protocol vectors; not food samples or nutritional evidence."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from meal_management.__main__ import main
from meal_management.assessment import assess
from meal_management.catalog import Catalog
from meal_management.catalog_git import MANIFEST, check_directory
from meal_management.journal import Journal
from meal_management.nutrients import CORE_NUTRIENTS, DEFAULT_REGISTRY, NutrientRegistry, load_registry
from meal_management.reporting import build, markdown
from meal_management.statistics import build as statistics, render_html


class NutritionContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = NutrientRegistry([{"id": "protocol_mg", "label": "__extension_marker__", "unit": "mg", "maximum": "30"}])
        self.catalog = Catalog(self.root / "catalog", self.registry)
        self.journal = Journal(self.root / "journal.sqlite3")
        self.journal.init()

    def profile(self, values=None):
        return {"food_id": "__protocol__", "name": "__numeric_vector__",
                "basis": {"quantity": "2", "unit": "g"},
                "nutrients": values if values is not None else {
                    "energy_kcal": "10", "carbohydrate_g": "2", "protein_g": "1", "fat_g": "0",
                    "protocol_mg": "3"},
                "source": {"url": "https://example.invalid/mechanism", "observed_on": "2001-01-01", "publisher": "protocol-only"},
                "review_state": "verified"}

    def event(self, profile=None, message="probe"):
        event = {"channel": "mechanism", "actor": "protocol", "message_id": message,
                 "received_at": "2001-01-01T00:00:00+00:00", "occurred_on": "2001-01-01",
                 "text": "__technical_storage_probe__", "kind": "meal"}
        if profile is not None:
            ref = self.catalog.put(profile)["nutrition_ref"]
            event["components"] = [{"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "confirmed"}]
        return event

    def report(self, **kwargs):
        return build(self.journal, self.catalog, "2001-01-01", "2001-01-01", include_statistics=True, **kwargs)

    def test_catalog_validation_returns_storage_payload_without_creating_catalog(self):
        record = self.profile({"energy_kcal": "10", "carbohydrate_g": "2", "protein_g": "1", "fat_g": "0"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("sys.stdin", io.StringIO(json.dumps(record))):
            code = main(["--catalog", str(self.root / "not-created"), "catalog-validate"])
        self.assertEqual(code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["record"], record)
        self.assertFalse(result["stored"])
        self.assertEqual(len(result["nutrition_ref"]), 64)
        self.assertFalse((self.root / "not-created").exists())

    def test_extension_validates_scales_and_is_not_a_global_mutation(self):
        self.assertNotIn("protocol_mg", DEFAULT_REGISTRY.ids)
        self.journal.record(self.event(self.profile()))
        report = self.report(nutrients=["protocol_mg"])
        self.assertEqual(report["nutrient_fields"], [*CORE_NUTRIENTS, "protocol_mg"])
        values = {r["id"]: r for r in report["nutrient_summary"]}
        self.assertEqual(values["protocol_mg"]["total_partial"], "6")
        self.assertEqual(values["fat_g"]["total_partial"], "0")
        self.assertEqual(report["calculation_state"], "ready_for_recorded_meals")
        self.assertIn("__extension_marker__", markdown(report))
        bad = self.profile();bad["nutrients"]["protocol_mg"] = "31"
        with self.assertRaisesRegex(ValueError, "out_of_bounds"):
            self.catalog.put(bad)
        with self.assertRaises(ValueError):
            Catalog(self.root / "other").put(self.profile())

    def test_bad_or_conflicting_definition_and_unknown_selection_are_rejected(self):
        for value in [
            {"id":"protein_g", "label":"override", "unit":"g"},
            {"id":"protocol_mg", "label":"x", "unit":"g"},
            {"id":"protocol_mg", "label":"x", "unit":"mg", "maximum":"NaN"},
            {"id":"protocol_mg", "label":"x", "unit":"mg", "maximum":"1", "minimum":"2"},
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                NutrientRegistry([value])
        with self.assertRaisesRegex(ValueError, "unknown_nutrient"):
            self.registry.selected(["not_registered_mg"])

    def test_missing_core_stays_unknown_and_raw_storage_is_not_completion(self):
        raw = self.event()
        saved = self.journal.record(raw)
        status = assess(self.journal.show(saved["event_id"]), self.catalog)
        self.assertFalse(status["calculation_ready"])
        self.assertIn("consumed_quantity_with_basis", status["missing_intake"])
        partial = self.profile({"energy_kcal":"0"})
        self.journal.record(self.event(partial, "partial"))
        report = self.report()
        values = {r["id"]:r for r in report["nutrient_summary"]}
        self.assertEqual(values["energy_kcal"]["total_partial"], "0")
        self.assertIsNone(values["fat_g"]["total_partial"])
        self.assertEqual(values["fat_g"]["missing_components"], 1)
        self.assertEqual(values["fat_g"]["records_without_components"], 1)
        self.assertEqual(report["calculation_state"], "partial_or_pending")
        self.assertIn("미확인", markdown(report))

    def test_assessment_preserves_quantity_and_detects_unreviewed_or_wrong_basis(self):
        event = self.event(self.profile())
        self.assertTrue(assess(event, self.catalog)["calculation_ready"])
        event["components"][0]["unit"] = "ml"
        result = assess(event, self.catalog)
        self.assertTrue(result["intake_known"])
        self.assertFalse(result["calculation_ready"])
        self.assertEqual(result["components"][0]["reason"], "unit_mismatch")
        event["components"][0]["unit"] = "g"
        event["components"][0]["nutrition_ref"] = "0"*64
        self.assertFalse(assess(event, self.catalog)["intake_known"])

    def test_configuration_is_used_by_directory_validation_and_cli(self):
        config = self.root / "nutrients.json"
        config.write_text(json.dumps({"schema_version":1,"definitions":[{"id":"protocol_mg","label":"__extension_marker__","unit":"mg"}]}))
        self.assertIn("protocol_mg", load_registry(config).ids)
        base = self.root / "shared";base.mkdir()
        (base/"catalog.json").write_text(json.dumps(MANIFEST))
        target = Catalog(base/"records", self.registry)
        target.put(self.profile())
        self.assertEqual(check_directory(base, self.registry)["records"], 1)
        with self.assertRaises(ValueError):
            check_directory(base)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--nutrient-definitions", str(config), "nutrients"])
        self.assertEqual(code, 0)
        self.assertIn("protocol_mg", output.getvalue())

    def test_private_nutrient_table_does_not_enter_public_projection(self):
        self.journal.record(self.event(self.profile()))
        projected = statistics(self.report(nutrients=["protocol_mg"]))
        private = render_html(projected)
        self.assertIn("__extension_marker__", private)
        self.assertIn("6 mg", private)
        self.assertIn("0 g", private)
        public = statistics(self.report(nutrients=["protocol_mg"]), mode="public")
        self.assertNotIn("nutrient_summary", public)
        self.assertNotIn("__extension_marker__", render_html(public))
        self.assertNotIn("__numeric_vector__", json.dumps(public))

    def test_assess_cli_requires_no_date_and_does_not_write_a_revision(self):
        event = self.event(self.profile());event.pop("occurred_on")
        result = self.journal.record(event)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--journal", str(self.journal.path), "--catalog", str(self.catalog.root),
                         "--nutrient-definitions", str(self._configuration()), "assess", "--event-id", result["event_id"]])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["calculation_ready"])
        self.assertEqual(self.journal.show(result["event_id"])["revision"], 1)

    def _configuration(self):
        config = self.root/"defs.json"
        config.write_text(json.dumps({"schema_version":1,"definitions":[{"id":"protocol_mg","label":"__extension_marker__","unit":"mg","maximum":"30"}]}))
        return config
