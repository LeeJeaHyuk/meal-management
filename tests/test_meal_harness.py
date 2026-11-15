"""Technical storage/arithmetic protocol probes only; not meals, users or domain fixtures."""
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from meal_management.catalog import Catalog, data_path, number
from meal_management.journal import Journal, validated
from meal_management.reporting import build, markdown


class MealMechanismTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="meal-mechanism-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = Journal(self.root / "private-protocol.sqlite3")
        self.catalog = Catalog(self.root / "public-protocol")
        self.journal.init()

    def event(self, message="probe-1", **changes):
        value = {"channel": "mechanism", "actor": "protocol-actor", "message_id": message,
                 "received_at": "2026-01-02T00:00:00+00:00", "kind": "meal",
                 "text": "__technical_storage_probe__", "occurred_on": "2026-01-02"}
        value.update(changes)
        return value

    def profile(self, **changes):
        value = {"food_id": "protocol-arithmetic", "name": "__technical_unit_profile__",
                 "basis": {"quantity": "2", "unit": "g"},
                 "nutrients": {"energy_kcal": "10", "protein_g": "2"},
                 "source": {"url": "https://example.invalid/protocol", "observed_on": "2026-01-01", "publisher": "mechanism-only"},
                 "review_state": "verified"}
        value.update(changes)
        return value

    def linked(self, **changes):
        ref = self.catalog.put(self.profile())["nutrition_ref"]
        component = {"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "confirmed"}
        component.update(changes)
        return [component]

    def summary(self):
        return build(self.journal, self.catalog, "2026-01-02", "2026-01-02")

    def test_statistics_macros_exclude_components_with_missing_nutrients(self):
        ref = self.catalog.put(self.profile(nutrients={
            "energy_kcal": "21", "carbohydrate_g": "2", "protein_g": "1", "fat_g": "1",
        }))["nutrition_ref"]
        component = {"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "confirmed"}
        self.journal.record(self.event(components=[component]))
        self.journal.record(self.event("incomplete", components=self.linked()))
        self.assertNotIn("macronutrient_composition", self.summary())
        report = build(self.journal, self.catalog, "2026-01-02", "2026-01-02", include_statistics=True)
        composition = report["macronutrient_composition"]
        self.assertEqual(composition["energy_kcal"], {"carbohydrate_g": "16", "protein_g": "8", "fat_g": "18"})
        self.assertEqual(composition["included_components"], 1)
        self.assertEqual(composition["incomplete_components"], 1)


    def test_management_counts_records_once_and_excludes_untimed_or_invalid_links(self):
        from meal_management.statistics import build as project_statistics
        components = self.linked()
        self.journal.record(self.event("multi-link", components=components * 2))
        self.journal.record(self.event("bad-unit", components=self.linked(unit="ml")))
        self.journal.record(self.event("no-link"))
        untimed = self.event("untimed", components=components)
        untimed.pop("occurred_on")
        self.journal.record(untimed)
        default = self.summary()
        self.assertNotIn("management_statistics", default)
        report = build(self.journal, self.catalog, "2026-01-02", "2026-01-03", include_statistics=True)
        self.assertEqual(report["management_statistics"], {
            "recorded_days": 1, "meal_record_count": 3,
            "nutrition_linked_meal_record_count": 1,
        })
        projected = project_statistics(report, mode="public")
        self.assertEqual(projected["management_percent"],
                         {"recorded_days": "50.00", "nutrition_linked_records": "33.33"})
        self.assertEqual(default, self.summary())


    def test_daily_macronutrients_keep_each_dates_evidence_and_default_report_shape(self):
        ref = self.catalog.put(self.profile(nutrients={
            "energy_kcal": "21", "carbohydrate_g": "2", "protein_g": "1", "fat_g": "1",
        }))["nutrition_ref"]
        component = {"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "estimated"}
        self.journal.record(self.event("first-day", components=[component]))
        self.journal.record(self.event("second-day", occurred_on="2026-01-03", components=self.linked()))
        self.journal.record(self.event("invalid-unit", components=self.linked(unit="ml")))
        default = self.summary()
        self.assertTrue(all("macronutrient_composition" not in d for d in default["daily_observations"]))
        report = build(self.journal, self.catalog, "2026-01-02", "2026-01-03", include_statistics=True)
        first, second = report["daily_observations"]
        self.assertEqual(first["macronutrient_composition"]["energy_kcal"],
                         {"carbohydrate_g": "16", "protein_g": "8", "fat_g": "18"})
        self.assertEqual(first["macronutrient_composition"]["estimated_components"], 1)
        self.assertEqual(first["macronutrient_composition"]["excluded_inputs"], 1)
        self.assertEqual(second["macronutrient_composition"]["energy_kcal"], {})
        self.assertEqual(second["macronutrient_composition"]["incomplete_components"], 1)
        self.assertEqual(default, self.summary())

    def test_replay_does_not_duplicate(self):
        first = self.journal.record(self.event())
        second = self.journal.record(self.event())
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertFalse(second["created"])
        self.assertEqual(len(self.journal.current()), 1)

    def test_same_text_different_messages_are_distinct(self):
        self.journal.record(self.event("one"))
        self.journal.record(self.event("two"))
        self.assertEqual(len(self.journal.current()), 2)

    def test_changed_source_requires_explicit_revision(self):
        self.journal.record(self.event())
        with self.assertRaisesRegex(ValueError, "use_revision"):
            self.journal.record(self.event(text="__changed_protocol_token__"))

    def test_revision_preserves_original_and_replay_is_idempotent(self):
        event_id = self.journal.record(self.event())["event_id"]
        changed = self.event(text="__revision_protocol_token__")
        revision = self.journal.revise(event_id, 1, "edit-probe", changed)
        replay = self.journal.revise(event_id, 1, "edit-probe", changed)
        self.assertEqual(revision["revision"], 2)
        self.assertFalse(replay["created"])
        with self.journal.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM revisions").fetchone()[0], 2)
        self.assertEqual(self.journal.current()[0]["text"], changed["text"])

    def test_show_and_list_preserve_date_uncertainty(self):
        event = self.event()
        event.pop("occurred_on")
        event_id = self.journal.record(event)["event_id"]
        rows = self.journal.list_period("2026-01-02", "2026-01-02")
        self.assertEqual(rows[0]["selection_date_basis"], "recorded_only_not_meal_date")
        self.assertEqual(self.journal.show(event_id, history=True)[0]["text"], event["text"])
        with self.assertRaisesRegex(ValueError, "event_not_found"):
            self.journal.show("unknown")

    def test_stale_edit_cannot_overwrite(self):
        event_id = self.journal.record(self.event())["event_id"]
        self.journal.revise(event_id, 1, "first", self.event(text="__first__"))
        with self.assertRaisesRegex(ValueError, "stale_revision"):
            self.journal.revise(event_id, 1, "second", self.event(text="__second__"))

    def test_unknown_or_ambiguous_inputs_rejected(self):
        for changes in [{"received_at": "2026-01-02T00:00:00"}, {"unexpected": 1},
                        {"components": [{"amount": 1}]}, {"kg": 1}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validated(self.event(**changes))

    def test_only_canonical_dates_reach_range_filters(self):
        with self.assertRaises(ValueError):
            self.journal.record(self.event(occurred_on="20260102"))
        with self.assertRaises(ValueError):
            build(self.journal, self.catalog, "20260102", "20260103")
        with self.assertRaises(ValueError):
            self.catalog.put(self.profile(source={"url": "https://example.invalid/protocol", "publisher": "mechanism", "observed_on": "20260102"}))

    def test_kst_midnight_date_matches(self):
        self.journal.record(self.event(occurred_at="2026-01-01T16:00:00+00:00"))
        self.assertEqual(self.summary()["explicit_meal_hour_counts"], {"1": 1})

    def test_untimed_record_not_assumed_eaten_on_input_day(self):
        event = self.event()
        event.pop("occurred_on")
        self.journal.record(event)
        report = self.summary()
        self.assertEqual(report["meal_record_count"], 0)
        self.assertEqual(report["untimed_record_count"], 1)

    def test_init_is_non_destructive(self):
        self.journal.record(self.event())
        self.assertFalse(self.journal.init()["created"])
        self.assertEqual(len(self.journal.current()), 1)

    def test_init_rejects_another_database_without_mutation(self):
        path = self.root / "other.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "unsupported_journal"):
            Journal(path).init()
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_missing_database_is_not_silently_created(self):
        path = self.root / "absent.sqlite3"
        with self.assertRaisesRegex(ValueError, "not_initialized"):
            Journal(path).current()
        self.assertFalse(path.exists())

    def test_catalog_is_content_addressed_and_reusable(self):
        first = self.catalog.put(self.profile())
        self.assertEqual(first, self.catalog.put(self.profile()))
        changed = self.profile(nutrients={"energy_kcal": "11"})
        self.assertNotEqual(first["nutrition_ref"], self.catalog.put(changed)["nutrition_ref"])
        self.assertEqual(self.catalog.get(first["nutrition_ref"])["nutrients"]["energy_kcal"], "10")

    def test_catalog_corruption_is_detected(self):
        ref = self.catalog.put(self.profile())["nutrition_ref"]
        (self.catalog.root / (ref + ".json")).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.catalog.get(ref)

    def test_catalog_excludes_personal_fields_and_untyped_sources(self):
        for change in [{"actor": "protocol"}, {"source": {"private_note": "probe"}}, {"basis": {"quantity": 1, "unit": "unknown"}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.catalog.put(self.profile(**change))

    def test_non_finite_and_negative_numbers_rejected(self):
        for value in [True, "-1", "NaN", "Infinity", "1e999", "0.00000000000000000000000001"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                number(value)

    def test_unit_conversion_requires_exact_basis_and_partial_is_not_zero(self):
        self.journal.record(self.event(components=self.linked(unit="ml")))
        report = self.summary()
        self.assertEqual(report["nutrition_coverage"]["unit_mismatch"], 1)
        self.assertEqual(report["nutrition_confirmed_partial"], {})

    def test_unreviewed_catalog_not_promoted(self):
        ref = self.catalog.put(self.profile(review_state="unreviewed"))["nutrition_ref"]
        component = {"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "confirmed"}
        self.journal.record(self.event(components=[component]))
        self.assertEqual(self.summary()["nutrition_coverage"]["unreviewed_component"], 1)
        self.assertEqual(self.summary()["nutrition_confirmed_partial"], {})

    def test_confirmed_and_estimated_arithmetic_separate(self):
        confirmed = self.linked()
        estimated = copy.deepcopy(confirmed)
        estimated[0]["certainty"] = "estimated"
        self.journal.record(self.event(components=confirmed + estimated))
        report = self.summary()
        self.assertEqual(report["nutrition_confirmed_partial"]["energy_kcal"], "20")
        self.assertEqual(report["nutrition_estimated_partial"]["energy_kcal"], "20")
        self.assertNotIn("fat_g", report["nutrition_confirmed_partial"])
        self.assertEqual(report["linked_food_occurrences"][0]["component_count"], 2)
        self.assertIn("열량 20.00 kcal", markdown(report))


    def uncertain(self, **changes):
        value = {"kind": "estimated", "reason": "__range_protocol__",
                 "ranges": {"energy_kcal": {"low": "8", "high": "12"}}}
        value.update(changes)
        return value

    def test_estimate_profile_never_becomes_confirmed_by_amount(self):
        ref = self.catalog.put(self.profile(uncertainty=self.uncertain()))["nutrition_ref"]
        component = {"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "confirmed"}
        self.journal.record(self.event(components=[component]))
        report = self.summary()
        self.assertEqual(report["nutrition_confirmed_partial"], {})
        self.assertEqual(report["nutrition_estimated_partial"]["energy_kcal"], "20")
        self.assertEqual(report["daily_observations"][0]["nutrition_estimated_partial"]["energy_kcal"], "20")
        self.assertEqual(report["nutrition_coverage"]["estimated_components"], 1)
        self.assertEqual(report["nutrition_coverage"]["estimated_profiles"], 1)
        detail = report["component_details"][0]
        self.assertEqual(detail["certainty"], "estimated")
        self.assertEqual(detail["input_certainty"], "confirmed")
        self.assertEqual(detail["nutrient_ranges"]["energy_kcal"], {"low": "16", "high": "24"})
        self.assertIn("통계적 신뢰구간", markdown(report))

    def test_uncertainty_rejects_unsupported_fields_and_missing_reason(self):
        for change in [{"kind": "exact"}, {"reason": ""}, {"reason": 1}, {"actor": "protocol"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.catalog.put(self.profile(uncertainty=self.uncertain(**change)))
        for value in [None, [], "estimated"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.catalog.put(self.profile(uncertainty=value))

    def test_ranges_require_known_nutrients_and_energy_bounds(self):
        for ranges in [{}, {"protein_g": {"low": "1", "high": "3"}},
                       {"energy_kcal": {"low": "8", "high": "12"}, "unknown": {"low": 0, "high": 1}}]:
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                self.catalog.put(self.profile(uncertainty=self.uncertain(ranges=ranges)))

    def test_ranges_enclose_point_value_and_reject_invalid_numbers(self):
        for bounds in [{"low": "11", "high": "12"}, {"low": "8", "high": "9"},
                       {"low": "12", "high": "8"}, {"low": "-1", "high": "12"},
                       {"low": "NaN", "high": "12"}, {"low": True, "high": "12"},
                       {"low": "8"}, {"low": "8", "high": "12", "private": "protocol"}, []]:
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                self.catalog.put(self.profile(uncertainty=self.uncertain(ranges={"energy_kcal": bounds})))

    def test_optional_ranges_are_not_invented_for_other_nutrients(self):
        ref = self.catalog.put(self.profile(uncertainty=self.uncertain()))["nutrition_ref"]
        self.journal.record(self.event(components=[{"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "estimated"}]))
        detail = self.summary()["component_details"][0]
        self.assertNotIn("protein_g", detail["nutrient_ranges"])
        self.assertEqual(self.summary()["nutrition_estimated_partial"]["protein_g"], "4")

    def test_sugar_and_saturated_fat_render_and_unknown_fields_still_fail(self):
        ref = self.catalog.put(self.profile(nutrients={"sugars_g": "1", "saturated_fat_g": "2"}))["nutrition_ref"]
        self.journal.record(self.event(components=[{"nutrition_ref": ref, "amount": "4", "unit": "g", "certainty": "confirmed"}]))
        rendered = markdown(self.summary())
        self.assertIn("당류 2.00 g", rendered)
        self.assertIn("포화지방 4.00 g", rendered)
        with self.assertRaises(ValueError):
            self.catalog.put(self.profile(nutrients={"unknown_nutrient": "1"}))

    def test_report_hash_stable_and_revisions_change_it(self):
        event_id = self.journal.record(self.event())["event_id"]
        first = self.summary()["report_hash"]
        self.assertEqual(first, self.summary()["report_hash"])
        self.journal.revise(event_id, 1, "revision", self.event(text="__new_revision__"))
        self.assertNotEqual(first, self.summary()["report_hash"])

    def test_weight_summary_does_not_invent_missing_dates(self):
        for key, day, kg in [("one", "2026-01-01", "2"), ("two", "2026-01-03", "1")]:
            self.journal.record(self.event(key, kind="weight", occurred_on=day, kg=kg))
        report = build(self.journal, self.catalog, "2026-01-01", "2026-01-07")
        self.assertEqual(report["weight"]["measured_days"], 2)
        self.assertEqual(report["weight"]["change_between_observed_days_kg"], "-1")
        self.assertNotIn("2026-01-02", report["weight"]["daily_mean_kg"])

    def test_daily_observations_keep_missing_nutrients_and_weight_unknown(self):
        self.journal.record(self.event(components=self.linked()))
        self.journal.record(self.event("next", occurred_on="2026-01-03"))
        report = build(self.journal, self.catalog, "2026-01-01", "2026-01-07")
        days = report["daily_observations"]
        self.assertEqual([row["date"] for row in days], ["2026-01-02", "2026-01-03"])
        self.assertEqual(days[0]["nutrition_confirmed_partial"]["energy_kcal"], "20")
        self.assertEqual(days[1]["nutrition_confirmed_partial"], {})
        self.assertIsNone(days[1]["weight_daily_mean_kg"])
        self.assertIn("날짜별 관측", markdown(report))

    def test_delivery_unknown_and_sent_do_not_replay(self):
        for state in ("unknown", "sent"):
            report_hash = hashlib.sha256(state.encode()).hexdigest()
            first = self.journal.claim_delivery(report_hash, "protocol-recipient")
            self.assertTrue(first["claimed"])
            self.journal.finish_delivery(first["delivery_id"], state, "receipt-probe" if state == "sent" else None)
            self.assertFalse(self.journal.claim_delivery(report_hash, "protocol-recipient")["claimed"])

    def test_delivery_requires_real_receipt_and_failure_is_bounded(self):
        report_hash = "a" * 64
        for i in range(3):
            claim = self.journal.claim_delivery(report_hash, "protocol-recipient")
            self.assertTrue(claim["claimed"])
            self.assertEqual(claim["attempt"], i + 1)
            with self.assertRaises(ValueError):
                self.journal.finish_delivery(claim["delivery_id"], "sent")
            with self.assertRaises(ValueError):
                self.journal.finish_delivery(claim["delivery_id"], "failed", "partial-receipt")
            self.journal.finish_delivery(claim["delivery_id"], "failed")
        self.assertFalse(self.journal.claim_delivery(report_hash, "protocol-recipient")["claimed"])

    def test_source_tree_cannot_be_a_data_root(self):
        with self.assertRaisesRegex(ValueError, "data_inside_code"):
            data_path(Path(__file__).resolve().parents[1] / "runtime")
        repo = Path(__file__).resolve().parents[1]
        with self.assertRaisesRegex(ValueError, "data_inside_code"):
            data_path(repo.parent / "absent-protocol-segment" / ".." / repo.name / "runtime")

    def test_cli_roundtrip_and_error_redaction(self):
        command = [sys.executable, "-m", "meal_management", "--journal", str(self.journal.path), "--catalog", str(self.catalog.root)]
        result = subprocess.run(command + ["record"], input=json.dumps(self.event()), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["created"])
        report = subprocess.run(command + ["report", "--start", "2026-01-02", "--end", "2026-01-02"], text=True, capture_output=True)
        self.assertEqual(json.loads(report.stdout)["meal_record_count"], 1)
        private_sentinel = "__DO_NOT_ECHO_PAYLOAD__"
        bad = self.event()
        bad["unknown_field"] = private_sentinel
        failed = subprocess.run(command + ["record"], input=json.dumps(bad), text=True, capture_output=True)
        self.assertEqual(failed.returncode, 2)
        self.assertNotIn(private_sentinel, failed.stdout + failed.stderr)
