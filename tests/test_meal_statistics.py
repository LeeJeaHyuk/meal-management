"""Numeric/permission protocol checks only. No personal records or domain datasets."""
import base64
import contextlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from meal_management.__main__ import main
from meal_management.journal import Journal
from meal_management import statistics


class MealStatisticsTests(unittest.TestCase):
    def report(self):
        return {
            "start": "2001-01-01", "end": "2001-01-03",
            "report_hash": "RAW_REPORT_CANARY",
            "nutrition_coverage": {"records_without_nutrition": 7},
            "daily_observations": [
                {"date": "2001-01-01", "nutrition_confirmed_partial": {"energy_kcal": "12345"},
                 "nutrition_estimated_partial": {}, "weight_daily_mean_kg": "317.125"},
                {"date": "2001-01-03", "nutrition_confirmed_partial": {"energy_kcal": "24690"},
                 "nutrition_estimated_partial": {"energy_kcal": "12345"}, "weight_daily_mean_kg": "634.25"},
            ],
            "linked_food_occurrences": [{"name": "PRIVATE_NAME_CANARY"}],
            "macronutrient_composition": {
                "energy_kcal": {"carbohydrate_g": "20", "protein_g": "30", "fat_g": "50"},
                "included_components": 1, "estimated_components": 0,
            },
        }

    def profile(self, **changes):
        # Equation arithmetic vector, never a stored user or an approved domain sample.
        value = {"equation": "male", "age_years": 20, "height_cm": 160,
                 "weight_kg": 50, "activity_factor": "1.5"}
        return dict(value, **changes)

    def test_calendar_gaps_remain_unknown_and_explicit_zero_is_kept(self):
        report = self.report()
        report["daily_observations"][0]["nutrition_confirmed_partial"]["energy_kcal"] = "0"
        value = statistics.build(report)
        self.assertEqual(value["days"][0]["intake"], "0")
        self.assertIsNone(value["days"][1]["intake"])
        self.assertIsNone(value["days"][1]["weight"])
        self.assertEqual(value["days"][2]["intake"], "37035")
        self.assertEqual(value["days"][2]["estimated_intake"], "12345")

    def test_public_projection_uses_period_composition_and_strips_metadata(self):
        value = statistics.build(self.report(), self.profile(), "public")
        first, gap, last = value["days"]
        self.assertEqual(first["intake"], "25.00")
        self.assertEqual(last["intake"], "75.00")
        self.assertEqual(last["estimated_intake"], "25.00")
        self.assertNotIn("weight", last)
        self.assertNotIn("expenditure", last)
        self.assertEqual(value["composition_percent"], {"carbohydrate": "20.00", "protein": "30.00", "fat": "50.00"})
        self.assertIsNone(gap["intake"])
        self.assertEqual(first["label"], "1일차")
        self.assertEqual(set(value), {"schema_version", "mode", "days", "energy_unit",
                                     "composition_percent", "composition_partial", "composition_estimated", "management_percent", "notes"})
        serialized = json.dumps(value)
        for token in ("2001-01", "12345", "317.125", "RAW_REPORT_CANARY",
                      "PRIVATE_NAME_CANARY", "height_cm", "age_years", "coverage"):
            self.assertNotIn(token, serialized)

    def test_public_view_is_invariant_to_absolute_measurement_scale(self):
        original = statistics.build(self.report())
        scaled = statistics.build(self.report())
        for day in scaled["days"]:
            for key in ("intake", "estimated_intake", "expenditure", "weight"):
                if day[key] is not None:
                    day[key] = str(statistics.number(day[key]) * 7)
        self.assertEqual(statistics.public_view(original), statistics.public_view(scaled))

    def test_zero_intake_produces_no_invented_percent(self):
        report = self.report()
        report["daily_observations"] = [
            {"date": "2001-01-01", "nutrition_confirmed_partial": {"energy_kcal": "0"},
             "nutrition_estimated_partial": {}, "weight_daily_mean_kg": None}
        ]
        value = statistics.build(report, self.profile(), "public")
        self.assertTrue(all(point["intake"] is None for point in value["days"]))

    def test_equation_and_explicit_activity_factor(self):
        male = statistics.estimate_expenditure(self.profile())
        female = statistics.estimate_expenditure(self.profile(equation="female"))
        self.assertEqual(male["resting_kcal_per_day"], "1405.00")
        self.assertEqual(male["daily_kcal"], "2107.500")
        self.assertEqual(float(male["resting_kcal_per_day"]) -
                         float(female["resting_kcal_per_day"]), 166)
        profile = self.profile()
        del profile["activity_factor"]
        self.assertIsNone(statistics.estimate_expenditure(profile)["daily_kcal"])
        self.assertEqual(statistics.estimate_expenditure(None)["state"], "missing_profile")

    def test_invalid_profiles_never_fall_back_to_default_values(self):
        for change in ({"age_years": 17}, {"weight_kg": True}, {"height_cm": "NaN"},
                       {"equation": "inferred"}, {"activity_factor": -1},
                       {"unapproved": "PRIVATE_PROFILE_CANARY"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                statistics.estimate_expenditure(self.profile(**change))

    def test_invalid_period_and_mode_fail(self):
        for change in ({"end": "2000-01-01"}, {"end": "2040-01-01"}):
            with self.assertRaises(ValueError):
                statistics.build(dict(self.report(), **change))
        with self.assertRaises(ValueError):
            statistics.build(self.report(), mode="invalid")

    def test_public_download_contains_no_private_payload(self):
        private = statistics.build(self.report(), self.profile())
        html = statistics.render_html(private)
        encoded = re.search(r'href="data:text/html;charset=utf-8;base64,([^"]+)"', html)[1]
        public = base64.b64decode(encoded).decode("utf-8")
        direct = statistics.render_html(statistics.public_view(private))
        self.assertEqual(public, direct)
        self.assertIn("<svg", public)
        self.assertIn("25.00", public)
        for token in ("2001-01", "12345", "317.125", "2107", "1405",
                      "RAW_REPORT_CANARY", "PRIVATE_NAME_CANARY", "height_cm"):
            self.assertNotIn(token, public)
        self.assertNotIn("data:text/html", public)
        self.assertNotRegex(public, r"<(?:script|iframe)\\b|(?:src|href)=\"https?://")

    def test_empty_statistics_produce_readable_html(self):
        report = dict(self.report(), daily_observations=[])
        html = statistics.render_html(statistics.build(report))
        self.assertIn("표시할 기록이 없습니다", html)
        self.assertIn("공개용 구성비 파일 저장", html)

    def test_cli_reads_empty_journal_and_leaves_it_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = Journal(root / "journal.sqlite3")
            journal.init()
            output = io.StringIO()
            args = ["--journal", str(root / "journal.sqlite3"), "--catalog", str(root / "catalog"),
                    "stats", "--start", "2001-01-01", "--end", "2001-01-03",
                    "--mode", "public", "--format", "json"]
            with contextlib.redirect_stdout(output):
                rc = main(args)
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(output.getvalue())["mode"], "public")
            self.assertEqual(journal.current(), [])

    def test_profile_stdin_and_size_limit(self):
        with patch("sys.stdin", io.StringIO(json.dumps(self.profile()))):
            self.assertEqual(statistics.load_profile("-"), self.profile())
        with patch("sys.stdin", io.StringIO(" " * 65537)), self.assertRaises(ValueError):
            statistics.load_profile("-")

    def test_management_percentages_use_explicit_denominators(self):
        report = self.report()
        report["management_statistics"] = {
            "recorded_days": 2, "meal_record_count": 7,
            "nutrition_linked_meal_record_count": 3,
        }
        value = statistics.build(report, mode="public")
        self.assertEqual(value["management_percent"],
                         {"recorded_days": "66.67", "nutrition_linked_records": "42.86"})
        report["management_statistics"] = {
            "recorded_days": 0, "meal_record_count": 0,
            "nutrition_linked_meal_record_count": 0,
        }
        value = statistics.build(report, mode="public")
        self.assertEqual(value["management_percent"]["recorded_days"], "0.00")
        self.assertIsNone(value["management_percent"]["nutrition_linked_records"])

    def test_blog_svg_has_ratios_only_and_no_daily_series(self):
        private = statistics.build(self.report(), self.profile())
        private["management_percent"] = {"recorded_days": "66.67", "nutrition_linked_records": "42.86"}
        public = statistics.public_view(private)
        public["days"][0]["label"] = "PRIVATE_LABEL_CANARY"
        public["notes"].append("PRIVATE_NOTE_CANARY")
        public["management_percent"]["source_path"] = "PRIVATE_PATH_CANARY"
        svg = statistics.render_public_svg(public)
        from xml.etree import ElementTree
        ElementTree.fromstring(svg)
        self.assertEqual(svg, statistics.render_public_svg(private))
        for expected in ("66.67%", "42.86%", "20.00%", "30.00%", "50.00%"):
            self.assertIn(expected, svg)
        for token in ("2001-01", "12345", "317.125", "2107", "RAW_REPORT_CANARY",
                      "PRIVATE_NAME_CANARY", "PRIVATE_LABEL_CANARY", "PRIVATE_NOTE_CANARY",
                      "PRIVATE_PATH_CANARY", "height_cm", "age_years", "1일차", "<script", "href="):
            self.assertNotIn(token, svg)
        self.assertNotIn("<metadata", svg)

    def test_blog_svg_missing_data_is_not_zero_and_invalid_ratios_fail(self):
        value = statistics.build(self.report(), mode="public")
        self.assertIn("미확인", statistics.render_public_svg(value))
        value["management_percent"]["recorded_days"] = "0"
        self.assertIn("0.00%", statistics.render_public_svg(value))
        for invalid in ("-1", "101", "NaN", True):
            value["management_percent"]["recorded_days"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                statistics.render_public_svg(value)

    def test_blog_download_decodes_to_the_safe_standalone_svg(self):
        private = statistics.build(self.report(), self.profile())
        html = statistics.render_html(private)
        encoded = re.search(r'href="data:image/svg\+xml;base64,([^"]+)"', html)[1]
        svg = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(svg, statistics.render_public_svg(private))
        self.assertNotIn("317.125", svg)
        self.assertNotIn("2001-01", svg)

    def test_cli_blog_svg_and_private_mode_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = Journal(root / "journal.sqlite3")
            journal.init()
            args = ["--journal", str(root / "journal.sqlite3"), "--catalog", str(root / "catalog"),
                    "stats", "--start", "2001-01-01", "--end", "2001-01-03", "--format", "svg"]
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(main(args + ["--mode", "public"]), 0)
            self.assertTrue(output.getvalue().startswith("<svg"))
            output, error = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                self.assertEqual(main(args), 2)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(json.loads(error.getvalue())["error"], "public_mode_required_for_svg")
            self.assertEqual(journal.current(), [])

    def test_adjacent_day_calorie_changes_preserve_declines_zero_and_gaps(self):
        report = self.report()
        report["end"] = "2001-01-07"
        report["daily_observations"] = [
            {"date": f"2001-01-{index:02d}",
             "nutrition_confirmed_partial": {"energy_kcal": value},
             "nutrition_estimated_partial": {}, "weight_daily_mean_kg": None}
            for index, value in ((1, "8"), (2, "12"), (3, "6"), (4, "0"), (5, "4"), (7, "8"))
        ]
        private = statistics.build(report)
        self.assertEqual([day["intake_change_kcal"] for day in private["days"]],
                         [None, "4", "-6", "-6", "4", None, None])
        self.assertEqual([day["intake_change_percent"] for day in private["days"]],
                         [None, "50.00", "-50.00", "-100.00", None, None, None])
        public = statistics.public_view(private)
        self.assertEqual([d["intake_change_percent"] for d in public["days"]],
                         [d["intake_change_percent"] for d in private["days"]])
        self.assertTrue(all("intake_change_kcal" not in day for day in public["days"]))
        self.assertIn("-50.00", statistics.render_html(private))
        self.assertIn("-50.00", statistics.render_html(public))

    def test_daily_macros_use_daily_denominators_and_mark_partial_estimated(self):
        report = self.report()
        report["daily_observations"][0]["macronutrient_composition"] = {
            "energy_kcal": {"carbohydrate_g": "4", "protein_g": "4", "fat_g": "9"},
        }
        report["daily_observations"][1]["macronutrient_composition"] = {
            "energy_kcal": {"carbohydrate_g": "8", "protein_g": "4", "fat_g": "9"},
            "incomplete_components": 1, "estimated_components": 1,
        }
        value = statistics.build(report, mode="public")
        first, gap, last = value["days"]
        self.assertEqual(first["carbohydrate_percent"], "23.53")
        self.assertEqual(last["carbohydrate_percent"], "38.10")
        self.assertEqual(last["protein_percent"], "19.05")
        self.assertEqual(last["fat_percent"], "42.86")
        self.assertIsNone(gap["carbohydrate_percent"])
        self.assertTrue(last["composition_partial"])
        self.assertTrue(last["composition_estimated"])

    def test_daily_svg_download_strips_absolute_values_and_uses_relative_dates(self):
        private = statistics.build(self.report(), self.profile())
        public = statistics.public_view(private)
        public["days"][0]["label"] = "PRIVATE_DAY_CANARY"
        public["days"][0]["intake_change_kcal"] = "PRIVATE_DELTA_CANARY"
        public["notes"].append("PRIVATE_NOTE_CANARY")
        svg = statistics.render_public_daily_svg(public)
        from xml.etree import ElementTree
        ElementTree.fromstring(svg)
        self.assertEqual(svg, statistics.render_public_daily_svg(private))
        self.assertIn("1일차", svg)
        self.assertIn("3일차", svg)
        for token in ("2001-01", "12345", "317.125", "2107", "1405", "RAW_REPORT_CANARY",
                      "PRIVATE_NAME_CANARY", "PRIVATE_DAY_CANARY", "PRIVATE_DELTA_CANARY",
                      "PRIVATE_NOTE_CANARY", "height_cm", "<metadata", "<script", "href="):
            self.assertNotIn(token, svg)
        html = statistics.render_html(private)
        images = re.findall(r'href="data:image/svg\+xml;base64,([^"]+)"', html)
        self.assertEqual(len(images), 2)
        self.assertEqual(base64.b64decode(images[1]).decode("utf-8"), svg)

    def test_daily_svg_draws_negative_changes_and_does_not_bridge_gaps(self):
        days = [
            {"label": "PRIVATE_CANARY", "intake": value, "intake_change_percent": change}
            for value, change in (("20", None), ("30", "50"), (None, None), ("10", "-50"))
        ]
        public = {"mode": "public", "days": days}
        svg = statistics.render_public_daily_svg(public)
        self.assertIn("-50.00", svg)
        self.assertNotIn("PRIVATE_CANARY", svg)
        # Two segments for the signed series; the gap must not become a connecting line.
        from xml.etree import ElementTree
        plot = ElementTree.fromstring(svg).find("{http://www.w3.org/2000/svg}svg[@aria-labelledby='daily-change-title']")
        self.assertIsNotNone(plot)
        self.assertEqual(len(plot.findall("{http://www.w3.org/2000/svg}polyline")), 2)
        self.assertNotIn("nan", svg.lower())

    def test_cli_daily_svg_with_empty_journal_stays_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = Journal(root / "journal.sqlite3")
            journal.init()
            output = io.StringIO()
            args = ["--journal", str(root / "journal.sqlite3"), "--catalog", str(root / "catalog"),
                    "stats", "--start", "2001-01-01", "--end", "2001-01-03",
                    "--mode", "public", "--format", "svg", "--svg-view", "daily"]
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(args), 0)
            self.assertIn("공개용 날짜별 비율", output.getvalue())
            self.assertNotIn("2001-01", output.getvalue())
            self.assertEqual(journal.current(), [])


if __name__ == "__main__":
    unittest.main()
