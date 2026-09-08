import json
import pathlib
import subprocess
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import codex_protocol_contract as contract  # noqa: E402


class CodexProtocolContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(
            (ROOT / "contracts" / "codex" / "ClientRequest.json").read_text()
        )

    def test_required_method_surface_is_generated(self):
        variants = contract._method_variants(self.schema)
        self.assertEqual(set(contract.REQUIRED_METHODS) - variants.keys(), set())

    def test_offline_samples_match_generated_request_contracts(self):
        contract.run_offline_contract_tests()

    def test_offline_contract_path_does_not_spawn_codex_or_agent_turn(self):
        with mock.patch.object(
            contract.subprocess,
            "run",
            side_effect=AssertionError("offline contract must not spawn a process"),
        ):
            contract.run_offline_contract_tests()

    def test_cli_reports_clear_pass_without_starting_agent_turn(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "codex_protocol_contract.py"), "--test"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS offline initialize/thread/start/turn/interrupt method contracts", result.stdout)
        self.assertIn("PASS no credentials required; no real agent turn started", result.stdout)
        self.assertNotIn("turn/start sent", result.stdout)


if __name__ == "__main__":
    unittest.main()
