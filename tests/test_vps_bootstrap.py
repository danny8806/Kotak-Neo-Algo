"""Offline, no-credential tests for the one-time SSH deployment bootstrap."""
import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ops.bootstrap_deploy_access import fingerprint, local_password, run


class VpsBootstrapTests(unittest.TestCase):
    def test_ssh_host_key_fingerprint_sha256(self):
        blob=b"synthetic-non-secret-host-public-key"
        expected="SHA256:"+base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        self.assertEqual(fingerprint(blob),expected)

    def test_local_private_env_seed_loaded_without_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            seed=Path(directory)/"not-committed.env"
            seed.write_text("VPS_PASS='synthetic-test-value'\n",encoding="utf-8")
            with patch.dict(os.environ,{"VPS_PASS":""}):
                self.assertEqual(local_password(seed),"synthetic-test-value")

    def test_bad_fingerprint_blocked_before_auth_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError,"fingerprint"):
                run("200.234.44.93","root","unverified","danny8806/Indian-Market-Intelligence-AI",
                    Path(directory)/"not-present.env",Path(directory)/"deploy_key")


if __name__=="__main__":
    unittest.main()
