import asyncio
import hashlib, json
from bridge.diagnostics import export_diagnostics
from bridge.domain import DeviceRecord, DeviceSession, SessionState
from bridge import Bridge, FakeTransport

def test_diagnostics_is_allowlisted_and_integrity_verifiable():
    device = DeviceRecord("d1", "hw1", "StackChan")
    session = DeviceSession("s1", "d1", "local", "usb", "secret-nonce", state=SessionState.ONLINE)
    bundle = export_diagnostics(device, session, {"heap": 10, "uptime_ms": 4, "faults": [], "path": "/secret"}, [])
    assert "nonce" not in json.dumps(bundle)
    assert "path" not in json.dumps(bundle)
    content = bundle["content"]
    assert set(content["health"]) <= {"heap", "uptime_ms", "faults"}
    unsigned = {key: value for key, value in bundle.items() if key != "integrity_sha256"}
    expected = hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert bundle["integrity_sha256"] == expected


def test_bridge_diagnostic_export_is_audited_for_the_target_device():
    bridge = Bridge()
    transport = FakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))

    bundle = bridge.export_diagnostics(device.device_id)

    audits = bridge.store.list_audits(device_id=device.device_id)
    assert bundle["bundle_id"]
    assert any(audit.kind.value == "diagnostic.exported" for audit in audits)
    assert all(audit.device_id == device.device_id for audit in audits)
