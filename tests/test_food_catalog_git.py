"""Ephemeral Git/arithmetic protocol tests, never a production food dataset."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from meal_management.catalog import Catalog, canonical
from meal_management.catalog_git import ACCEPTED_REF, MANIFEST, check_directory, import_snapshot, sync_catalog


class FoodCatalogGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="food-git-mechanism-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self.git("config", "user.name", "Protocol Test")
        self.git("config", "user.email", "protocol@example.invalid")
        (self.repo / "catalog.json").write_text(json.dumps(MANIFEST))
        self.catalog = Catalog(self.root / "local-catalog")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args]).decode().strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-q", "-m", "technical protocol")
        return self.git("rev-parse", "HEAD")

    def record(self, **changes):
        value = {"food_id": "__protocol_arithmetic__", "name": "__mechanism_only__",
                 "basis": {"quantity": "2", "unit": "g"}, "nutrients": {"protein_g": "1"},
                 "source": {"url": "https://example.invalid/technical", "publisher": "protocol",
                            "observed_on": "2026-01-01"}, "review_state": "verified"}
        value.update(changes)
        return value

    def add(self, record, group="records"):
        ref = hashlib.sha256(canonical(record).encode()).hexdigest()
        directory = self.repo / group
        directory.mkdir(exist_ok=True)
        path = directory / (ref + ".json")
        path.write_text(canonical(record) + "\n")
        return ref, path

    def import_at(self, commit):
        return import_snapshot(self.repo / ".git", commit, self.catalog)

    def test_real_git_snapshot_import_and_replay(self):
        ref, _ = self.add(self.record())
        commit = self.commit()
        first = self.import_at(commit)
        self.assertEqual(first["added"], 1)
        self.assertEqual(self.import_at(commit)["added"], 0)
        self.assertEqual(self.catalog.get(ref)["nutrients"]["protein_g"], "1")
        self.assertEqual(self.git("rev-parse", ACCEPTED_REF), commit)

    def test_empty_repository_has_explicit_zero_records(self):
        result = self.import_at(self.commit())
        self.assertEqual(result["record_count"], 0)
        self.assertFalse(result["personal_journal_accessed"])

    def test_all_records_validated_before_any_local_write(self):
        self.add(self.record())
        wrong = self.repo / "records" / ("a" * 64 + ".json")
        wrong.write_text("{}")
        with self.assertRaises(ValueError):
            self.import_at(self.commit())
        self.assertFalse(self.catalog.root.exists())

    def test_content_hash_and_review_state_are_checked(self):
        self.add(self.record(review_state="unreviewed"))
        with self.assertRaisesRegex(ValueError, "review_mismatch"):
            self.import_at(self.commit())
        self.assertFalse(self.catalog.root.exists())

    def test_personal_field_is_not_accepted_in_reference_record(self):
        self.add(self.record(actor="__technical_forbidden_field__"))
        with self.assertRaisesRegex(ValueError, "invalid_catalog_fields"):
            self.import_at(self.commit())

    def test_metadata_cannot_hide_nested_personal_containers(self):
        for changes in [{"brand": {"actor": "protocol"}},
                        {"source": {"url": "https://example.invalid/technical", "publisher": {"actor": "protocol"}, "observed_on": "2026-01-01"}}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.catalog.put(self.record(**changes))

    def test_historical_records_cannot_be_removed(self):
        ref, path = self.add(self.record())
        self.import_at(self.commit())
        path.unlink()
        with self.assertRaisesRegex(ValueError, "historical_catalog_records_removed"):
            self.import_at(self.commit())
        self.assertEqual(self.catalog.get(ref)["food_id"], "__protocol_arithmetic__")

    def test_rewound_history_is_not_fresh(self):
        first = self.commit()
        self.add(self.record())
        self.import_at(self.commit())
        with self.assertRaisesRegex(ValueError, "not_fast_forward"):
            self.import_at(first)

    def test_symlink_record_is_rejected(self):
        directory = self.repo / "records"
        directory.mkdir()
        (directory / ("a" * 64 + ".json")).symlink_to("../catalog.json")
        with self.assertRaisesRegex(ValueError, "tree_entry"):
            self.import_at(self.commit())

    def test_directory_proposals_and_accepted_records_are_separate(self):
        self.add(self.record())
        self.add(self.record(review_state="unreviewed"), "proposals")
        self.assertEqual(check_directory(self.repo)["records"], 1)
        self.assertEqual(check_directory(self.repo)["proposals"], 1)
        (self.repo / "unapproved-export.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "unexpected_catalog_repository_entry"):
            check_directory(self.repo)

    def test_existing_cache_origin_is_never_replaced_or_fetched(self):
        cache = self.repo / ".git"
        self.git("config", "core.bare", "true")
        self.git("config", "foodcatalog.cacheVersion", "1")
        self.git("remote", "add", "origin", "git@github.com:example/unchanged.git")
        identity = self.root / "technical-identity-placeholder"
        identity.write_text("protocol only; not a credential")
        config = self.root / "source.json"
        config.write_text(json.dumps({"schema_version": 1, "remote": "git@github.com:example/approved.git",
                                     "cache": str(cache), "ssh_key": str(identity)}))
        with self.assertRaisesRegex(ValueError, "remote_mismatch"):
            sync_catalog(self.catalog, config)
        self.assertEqual(self.git("remote", "get-url", "origin"), "git@github.com:example/unchanged.git")
