"""W3.2 semantic behavior and speech allowlist tests."""

from __future__ import annotations

import asyncio
import math

import pytest

from bridge import Bridge, FakeTransport
from bridge.domain import CommandState
from bridge.errors import ValidationError
from bridge.intent import validate_behavior, validate_speech


def test_intent_catalog_rejects_unknown_or_unbounded_semantic_values():
    with pytest.raises(ValidationError):
        validate_behavior("wave_raw_servo", 0.5, 1000)
    with pytest.raises(ValidationError):
        validate_behavior("blink", math.nan, 1000)
    with pytest.raises(ValidationError):
        validate_behavior("blink", 0.5, 30_001)
    with pytest.raises(ValidationError):
        validate_speech("hello", "unregistered-voice")
    with pytest.raises(ValidationError):
        validate_speech(" ")


def test_behavior_and_speech_are_feature_gated_without_wire_side_effects():
    async def scenario():
        bridge = Bridge()
        transport = FakeTransport()
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        behavior = await bridge.submit_behavior(device.device_id, name="greet")
        speech = await bridge.submit_speech(device.device_id, text="hello")
        return behavior, speech, transport

    behavior, speech, transport = asyncio.run(scenario())
    assert behavior.state is CommandState.REJECTED
    assert behavior.error == {
        "code": "capability_unavailable",
        "reason": "feature_gate_disabled",
        "required_capability": "behavior",
    }
    assert speech.state is CommandState.REJECTED
    assert not any(frame.get("kind") == "command" for frame in transport.sent_frames)


def test_enabled_fake_intents_map_to_semantic_command_intent_and_complete():
    async def scenario():
        bridge = Bridge(feature_gates={"behavior": True, "speech": True})
        transport = FakeTransport(capabilities={"behavior", "speech"})
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        behavior = await bridge.submit_behavior(
            device.device_id,
            name="small_nod",
            intensity=0.7,
            duration_ms=1200,
        )
        speech = await bridge.submit_speech(
            device.device_id,
            text="  welcome back  ",
            voice="calm",
        )
        return behavior, speech, transport

    behavior, speech, transport = asyncio.run(scenario())
    assert behavior.state is CommandState.COMPLETED
    assert speech.state is CommandState.COMPLETED
    intent_frames = [frame for frame in transport.sent_frames if frame.get("type") == "command.intent"]
    assert len(intent_frames) == 2
    assert intent_frames[0]["payload"]["behaviors"] == [
        {"name": "small_nod", "intensity": 0.7, "duration_ms": 1200}
    ]
    assert intent_frames[1]["payload"]["speech"] == {"text": "welcome back", "voice": "calm"}
    assert "yaw_deg" not in intent_frames[0]["payload"]
    assert "pwm" not in intent_frames[0]["payload"]


def test_web_intent_preempts_lower_priority_agent_intent():
    async def scenario():
        bridge = Bridge(feature_gates={"behavior": True})
        transport = FakeTransport(capabilities={"behavior"}, auto_complete=False)
        bridge.discover(transport.candidate())
        device = bridge.claim(transport.candidate().candidate_id)
        await bridge.connect(device.device_id, transport)
        agent = await bridge.submit_behavior(
            device.device_id,
            name="thinking",
            source="agent",
        )
        transport.auto_complete = True
        web = await bridge.submit_behavior(device.device_id, name="greet")
        return bridge, agent, web, transport

    bridge, agent, web, transport = asyncio.run(scenario())
    assert bridge.get_command(agent.command_id).state is CommandState.PREEMPTED
    assert web.state is CommandState.COMPLETED
    assert [frame["type"] for frame in transport.sent_frames if frame.get("kind") == "command"] == [
        "command.intent",
        "command.intent",
    ]
