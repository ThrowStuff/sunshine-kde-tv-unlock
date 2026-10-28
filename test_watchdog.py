import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import watchdog as w


def state(pid="123", active="active", entered="1000000"):
    return {"ActiveState": active, "SubState": "running" if active == "active" else "dead",
            "MainPID": pid, "ActiveEnterTimestampMonotonic": entered}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.json"

    def tearDown(self):
        self.temp.cleanup()

    @patch.object(w.subprocess, "run")
    @patch.object(w, "healthy", return_value=(False, "timeout"))
    @patch.object(w, "host_state", return_value=state())
    def test_restarts_only_after_three_failures(self, host, health, command):
        for number in (1, 2):
            self.assertIn(f"{number}/3", w.check(self.path, now=200))
            command.assert_not_called()
        self.assertIn("RESTART QUEUED", w.check(self.path, now=200))
        command.assert_called_once()
        self.assertEqual(json.loads(self.path.read_text())["failures"], 0)

    @patch.object(w.subprocess, "run")
    @patch.object(w, "healthy", return_value=(True, "healthy"))
    @patch.object(w, "host_state", return_value=state())
    def test_health_success_resets_failures(self, host, health, command):
        w.save(self.path, {"identity": ["123", "1000000"], "failures": 2})
        w.check(self.path, now=200)
        self.assertEqual(json.loads(self.path.read_text())["failures"], 0)
        command.assert_not_called()

    @patch.object(w, "healthy")
    @patch.object(w, "host_state", return_value=state())
    def test_startup_grace(self, host, health):
        self.assertIn("grace", w.check(self.path, now=20))
        health.assert_not_called()

    @patch.object(w, "healthy")
    @patch.object(w, "host_state", return_value=state(pid="0", active="inactive"))
    def test_manual_stop_is_respected(self, host, health):
        self.assertIn("not running", w.check(self.path, now=200))
        health.assert_not_called()

    @patch.object(w.subprocess, "run")
    @patch.object(w, "healthy", return_value=(False, "timeout"))
    @patch.object(w, "host_state", side_effect=[state(), state(pid="0", active="inactive")])
    def test_stop_during_probe_does_not_restart(self, host, health, command):
        w.save(self.path, {"identity": ["123", "1000000"], "failures": 2})
        self.assertIn("changed", w.check(self.path, now=200))
        command.assert_not_called()

    @patch.object(w.subprocess, "run")
    @patch.object(w, "healthy", return_value=(False, "timeout"))
    @patch.object(w, "host_state", return_value=state(pid="456", entered="2000000"))
    def test_old_process_failure_count_is_not_reused(self, host, health, command):
        w.save(self.path, {"identity": ["123", "1000000"], "failures": 2})
        self.assertIn("1/3", w.check(self.path, now=200))
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
