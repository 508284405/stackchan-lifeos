"""Web Bridge USB scan/add surface: enumeration, in-use protection, gating."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from bridge import Bridge, FakeTransport
from bridge import scan as usb_scan
from bridge.api import create_app
from bridge.errors import TransportError


class PathedFakeTransport(FakeTransport):
    """Fake transport that pretends to hold a host serial path."""

    path = "/dev/cu.busy0"


@pytest.fixture()
def connected_in_use_bridge():
    bridge = Bridge(feature_gates={"usb_add": True})
    transport = PathedFakeTransport()
    bridge.discover(transport.candidate())
    device = bridge.claim(transport.candidate().candidate_id)
    asyncio.run(bridge.connect(device.device_id, transport))
    return bridge, device


def test_usb_scan_reports_in_use_ports_without_probing(connected_in_use_bridge, monkeypatch):
    bridge, device = connected_in_use_bridge
    probed: list[str] = []

    def fake_probe(path, **kwargs):
        probed.append(path)
        return {
            "path": path,
            "state": "online",
            "identity": {"device_id": "stackchan-02", "hardware_id": "aa:bb", "board": None, "firmware": None},
            "reason": None,
        }

    monkeypatch.setattr(usb_scan, "candidate_ports", lambda: ["/dev/cu.busy0", "/dev/cu.free0"])
    monkeypatch.setattr(usb_scan, "probe_port", fake_probe)

    with TestClient(create_app(bridge)) as client:
        response = client.post("/api/v1/usb-scan")

    assert response.status_code == 200
    items = response.json()["items"]
    assert {item["path"]: item["state"] for item in items} == {
        "/dev/cu.busy0": "in-use",
        "/dev/cu.free0": "online",
    }
    assert probed == ["/dev/cu.free0"]
    online = next(item for item in items if item["state"] == "online")
    assert online["identity"]["device_id"] == "stackchan-02"


def test_usb_add_registers_and_connects_scanned_device(monkeypatch):
    bridge = Bridge(feature_gates={"usb_add": True})

    def fake_transport(path):
        assert path == "/dev/cu.free0"
        return FakeTransport(
            device_id="stackchan-02",
            hardware_id="aa:bb",
            transport_id=f"usb:{path}",
            capabilities={"status", "protocol", "safety"},
        )

    monkeypatch.setattr(usb_scan, "UsbSerialTransport", fake_transport)

    with TestClient(create_app(bridge)) as client:
        first = client.post(
            "/api/v1/usb-devices",
            json={"path": "/dev/cu.free0", "device_id": "stackchan-02", "hardware_id": "aa:bb"},
        )
        devices_after_first = client.get("/api/v1/devices").json()["items"]
        again = client.post(
            "/api/v1/usb-devices",
            json={
                "path": "/dev/cu.free0",
                "device_id": "stackchan-02",
                "hardware_id": "aa:bb",
                "display_name": "Desk Chan",
            },
        )
        devices_after_again = client.get("/api/v1/devices").json()["items"]

    assert first.status_code == 200
    assert first.json()["device_id"] == "stackchan-02"
    assert first.json()["session"]["state"] == "online"
    assert [device["device_id"] for device in devices_after_first] == ["stackchan-02"]
    assert again.status_code == 200
    # Re-adding a known hardware identity re-connects it; registry semantics
    # keep the original display_name instead of overwriting it.
    assert again.json()["display_name"] == "stackchan-02"
    assert len(devices_after_again) == 1


def test_usb_add_rejects_unknown_fields_and_in_use_path(connected_in_use_bridge):
    bridge, device = connected_in_use_bridge

    with TestClient(create_app(bridge)) as client:
        unknown = client.post(
            "/api/v1/usb-devices",
            json={
                "path": "/dev/cu.free0",
                "device_id": "stackchan-02",
                "hardware_id": "aa:bb",
                "session_id": "forged",
            },
        )
        in_use = client.post(
            "/api/v1/usb-devices",
            json={"path": "/dev/cu.busy0", "device_id": "x", "hardware_id": "y"},
        )

    assert unknown.status_code == 422
    assert in_use.status_code == 409


def test_usb_add_returns_502_when_device_never_completes_hello(monkeypatch):
    bridge = Bridge(feature_gates={"usb_add": True})

    class SilentTransport(FakeTransport):
        async def open(self, receiver):
            raise TransportError("serial.open failed")

    monkeypatch.setattr(usb_scan, "UsbSerialTransport", lambda path: SilentTransport())

    with TestClient(create_app(bridge)) as client:
        response = client.post(
            "/api/v1/usb-devices",
            json={"path": "/dev/cu.free0", "device_id": "stackchan-02", "hardware_id": "aa:bb"},
        )

    assert response.status_code == 502


def test_usb_surface_is_feature_gated():
    bridge = Bridge(feature_gates={"usb_add": False})

    with TestClient(create_app(bridge)) as client:
        scan = client.post("/api/v1/usb-scan")
        add = client.post(
            "/api/v1/usb-devices",
            json={"path": "/dev/cu.free0", "device_id": "stackchan-02", "hardware_id": "aa:bb"},
        )

    assert scan.status_code == 409
    assert scan.json()["detail"]["error"]["required_capability"] == "usb_add"
    assert add.status_code == 409


def test_usb_add_counts_as_high_impact_for_trusted_lan_binding():
    # Explicitly enabled usb_add on trusted LAN requires an origin allowlist,
    # while default gates must still boot trusted-LAN without extra config.
    bridge = Bridge(feature_gates={"usb_add": True})

    with pytest.raises(ValueError, match="origin_allowlist"):
        create_app(bridge, bind_host="192.168.1.10", web_no_auth_trusted_lan=True)
