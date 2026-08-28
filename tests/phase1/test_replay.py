import tempfile
import unittest
from pathlib import Path

from simulator.phase1.replay import ReplayDevice, replay_file


def envelope(event_id, seq, *, kind="command", type_="command.control", **payload):
    return {"schema": "lifeos.v1", "kind": kind, "type": type_,
            "event_id": event_id, "device_id": "stackchan-01", "seq": seq,
            "ts_ms": 1000, "payload": payload}


def hello(seq=1):
    return envelope("hello-1", seq, kind="hello", type_="hello.host",
                    protocol_versions=["lifeos.v1"], session_nonce="test")


class Phase1ReplayTests(unittest.TestCase):
    def ready(self):
        device = ReplayDevice()
        self.assertEqual(device.feed(hello()), "accepted")
        return device

    def test_duplicate_does_not_repeat_motion(self):
        device = self.ready()
        command = envelope("home-1", 2, action="home")
        self.assertEqual(device.feed(command), "accepted")
        self.assertEqual(device.feed(command), "duplicate")
        self.assertEqual(device.result.motion_count, 1)

    def test_expired_and_sequence_gap_are_rejected(self):
        device = self.ready()
        expired = envelope("old-1", 2, type_="command.intent",
                           issued_at_ms=0, expires_at_ms=999)
        self.assertEqual(device.feed(expired), "rejected")
        self.assertEqual(device.feed(envelope("gap-1", 4, action="status")), "rejected")
        self.assertIn("seq.out_of_order", device.result.reasons)

    def test_disconnect_requires_hello_but_estop_remains_available(self):
        device = self.ready()
        device.disconnect()
        self.assertEqual(device.feed(envelope("pause-1", 1, action="pause")), "rejected")
        self.assertEqual(device.feed(envelope("stop-1", 2,
                                               type_="command.emergency_stop")), "accepted")
        self.assertFalse(device.snapshot()["torque_enabled"])

    def test_hard_limit_and_local_fault_clear(self):
        device = self.ready()
        motion = envelope("motion-1", 2, type_="command.maintenance_motion",
                          issued_at_ms=900, expires_at_ms=2000,
                          yaw_deg=91, pitch_deg=45)
        self.assertEqual(device.feed(motion), "rejected")
        device.inject_fault("FAULT_STALL")
        self.assertEqual(device.feed(envelope("clear-1", 3,
                                               action="clear_fault")), "rejected")
        self.assertEqual(device.snapshot()["fault_latched"], "FAULT_STALL")

    def test_invalid_json_and_large_line_are_rejected_before_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("not-json\n" + "{" + "x" * 16_384 + "}\n",
                            encoding="utf-8")
            result = replay_file(path)
        self.assertIn("json.invalid:1", result.reasons)
        self.assertIn("line.too_large:2", result.reasons)

    def test_unknown_action_and_schema_are_rejected(self):
        device = self.ready()
        self.assertEqual(device.feed(envelope("bad-1", 2, action="launch")), "rejected")
        bad = envelope("bad-2", 3, action="status")
        bad["schema"] = "lifeos.v2"
        self.assertEqual(device.feed(bad), "rejected")

    def test_new_session_resets_duplicate_window(self):
        device = self.ready()
        command = envelope("reused-1", 2, action="status")
        self.assertEqual(device.feed(command), "accepted")
        device.disconnect()
        device.connect()
        self.assertEqual(device.feed(hello()), "accepted")
        reused = envelope("reused-1", 2, action="status")
        self.assertEqual(device.feed(reused), "accepted")


if __name__ == "__main__":
    unittest.main()
