import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.check_public_tree import audit

class PublicTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.file=self.root/"module.py";self.file.write_text("answer = 2 + 3\n")
        self.manifest={"files":{"module.py":hashlib.sha256(self.file.read_bytes()).hexdigest()}}
        self.save()
    def save(self):
        (self.root/"PUBLIC-FILES.json").write_text(json.dumps(self.manifest))
    def test_explicit_clean_code_passes_and_hash_change_fails(self):
        self.assertTrue(audit(self.root)["passed"])
        self.file.write_text("answer = 7\n")
        self.assertFalse(audit(self.root)["passed"])
    def test_unexpected_key_file_is_rejected_without_reading_it(self):
        extra=self.root/".env";extra.write_text("__technical_noncredential_sentinel__")
        original=Path.read_bytes
        def read(path):
            self.assertNotEqual(path,extra)
            return original(path)
        with patch.object(Path,"read_bytes",read):
            result=audit(self.root)
        self.assertFalse(result["passed"])
        self.assertIn("unexpected_or_data_file",[v["code"] for v in result["findings"]])
    def test_common_secret_signature_and_symlink_are_rejected(self):
        self.file.write_text("value = '"+("ghp_"+"a"*36)+"'\n")
        self.manifest["files"]["module.py"]=hashlib.sha256(self.file.read_bytes()).hexdigest();self.save()
        self.assertFalse(audit(self.root)["passed"])
        self.file.write_text("answer = 2 + 3\n")
        self.manifest["files"]["module.py"]=hashlib.sha256(self.file.read_bytes()).hexdigest();self.save()
        (self.root/"link").symlink_to(self.file)
        self.assertFalse(audit(self.root)["passed"])
