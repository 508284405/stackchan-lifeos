"""Pure, deterministic validation for the W6 control-plane design contract.

This test intentionally imports no Bridge implementation.  The repository does
not require a third-party JSON-Schema package, so it exercises the small JSON
Schema vocabulary used by these checked-in contracts and then applies the
cross-field replay rules that JSON Schema cannot express.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = ROOT / "contracts" / "control-plane"
FIXTURE_DIR = CONTRACT_DIR / "fixtures"
NOW_MS = 1_700_000_010_000


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_schema(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the strict schema subset used by the control-plane contracts."""

    if "const" in schema and instance != schema["const"]:
        raise AssertionError(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise AssertionError(f"{path}: value is not in enum")

    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(instance, dict):
            raise AssertionError(f"{path}: expected object")
        required = schema.get("required", [])
        missing = [name for name in required if name not in instance]
        if missing:
            raise AssertionError(f"{path}: missing {missing}")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise AssertionError(f"{path}: too many properties")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(instance) - set(properties))
            if unknown:
                raise AssertionError(f"{path}: unknown properties {unknown}")
        for name, value in instance.items():
            if name in properties:
                validate_schema(value, properties[name], f"{path}.{name}")
    elif expected_type == "array":
        if not isinstance(instance, list):
            raise AssertionError(f"{path}: expected array")
        if len(instance) < schema.get("minItems", 0):
            raise AssertionError(f"{path}: too few items")
        if len(instance) > schema.get("maxItems", len(instance)):
            raise AssertionError(f"{path}: too many items")
        if schema.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in instance}) != len(instance):
            raise AssertionError(f"{path}: duplicate items")
        for index, value in enumerate(instance):
            validate_schema(value, schema["items"], f"{path}[{index}]")
    elif expected_type == "string":
        if not isinstance(instance, str):
            raise AssertionError(f"{path}: expected string")
        if len(instance) < schema.get("minLength", 0):
            raise AssertionError(f"{path}: string is too short")
        if len(instance) > schema.get("maxLength", len(instance)):
            raise AssertionError(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise AssertionError(f"{path}: string does not match pattern")
    elif expected_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise AssertionError(f"{path}: expected integer")
        if instance < schema.get("minimum", instance):
            raise AssertionError(f"{path}: integer is below minimum")
        if instance > schema.get("maximum", instance):
            raise AssertionError(f"{path}: integer is above maximum")
    elif expected_type is not None:
        raise AssertionError(f"{path}: unsupported test schema type {expected_type!r}")


def semantic_validate(
    envelope: dict[str, Any],
    *,
    now_ms: int,
    last_seq: dict[tuple[str, str, str], int] | None = None,
    seen_ids: set[tuple[str, str, str]] | None = None,
) -> None:
    """Apply sender binding, expiry, direction, and replay invariants."""

    last_seq = last_seq if last_seq is not None else {}
    seen_ids = seen_ids if seen_ids is not None else set()
    trusted = {
        ("edge_agent", "edge-a"): (
            "site-a",
            "a" * 64,
        ),
        ("control_plane", "control-plane"): (
            "control",
            "c" * 64,
        ),
    }
    sender = envelope["sender"]
    sender_key = (sender["entity_type"], sender["entity_id"])
    if trusted.get(sender_key) != (sender["site_id"], sender["certificate_fingerprint_sha256"]):
        raise AssertionError("sender is not bound to the trusted registry")

    session = envelope["session"]
    expected_direction = "edge_to_control" if sender["entity_type"] == "edge_agent" else "control_to_edge"
    if session["direction"] != expected_direction:
        raise AssertionError("sender and session direction do not match")
    if envelope["expires_at_ms"] <= envelope["issued_at_ms"]:
        raise AssertionError("expiry must be after issue time")
    if now_ms >= envelope["expires_at_ms"]:
        raise AssertionError("envelope is expired")

    replay_key = (sender["entity_id"], session["session_id"], session["direction"])
    if (replay_key[0], replay_key[1], envelope["message_id"]) in seen_ids:
        raise AssertionError("message_id replay")
    previous = last_seq.get(replay_key)
    if previous is not None and envelope["seq"] != previous + 1:
        raise AssertionError("seq replay, rollback, or gap")
    last_seq[replay_key] = envelope["seq"]
    seen_ids.add((replay_key[0], replay_key[1], envelope["message_id"]))


def envelope_schema() -> dict[str, Any]:
    return load_json(CONTRACT_DIR / "control-envelope.v1.schema.json")


def test_all_control_plane_schemas_have_unique_draft_ids_and_strict_roots():
    ids = set()
    for path in sorted(CONTRACT_DIR.glob("*.schema.json")):
        schema = load_json(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] not in ids
        ids.add(schema["$id"])
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_valid_identity_migration_limits_and_gates_fixtures_match_their_schemas():
    for fixture_path, schema_name in (
        (FIXTURE_DIR / "valid-identity.json", "identity.v1.schema.json"),
        (FIXTURE_DIR / "valid-site-migration.json", "site-migration.v1.schema.json"),
        (CONTRACT_DIR / "limits.v1.json", "limits.v1.schema.json"),
        (CONTRACT_DIR / "gates.v1.json", "gates.v1.schema.json"),
    ):
        validate_schema(load_json(fixture_path), load_json(CONTRACT_DIR / schema_name))

    gates = load_json(CONTRACT_DIR / "gates.v1.json")
    assert gates["status"] == "design_only"
    assert gates["web_authentication_required"] is True
    assert gates["gates"] == {
        "public_network_ingress": False,
        "remote_motion": False,
        "destructive_operations": False,
        "ota": False,
    }


def test_valid_envelope_fixture_passes_schema_and_semantic_checks():
    envelope = load_json(FIXTURE_DIR / "valid-control-envelope.json")
    validate_schema(envelope, envelope_schema())
    semantic_validate(envelope, now_ms=NOW_MS)


def test_unknown_top_level_field_is_rejected_by_schema():
    with pytest.raises(AssertionError, match="unknown properties"):
        validate_schema(load_json(FIXTURE_DIR / "unknown-field-control-envelope.json"), envelope_schema())


def test_expired_envelope_is_schema_valid_but_rejected_before_dispatch():
    envelope = load_json(FIXTURE_DIR / "expired-control-envelope.json")
    validate_schema(envelope, envelope_schema())
    with pytest.raises(AssertionError, match="expired"):
        semantic_validate(envelope, now_ms=NOW_MS)
    with pytest.raises(AssertionError, match="expired"):
        semantic_validate(envelope, now_ms=envelope["expires_at_ms"])


def test_sender_certificate_mismatch_is_rejected_before_payload_processing():
    envelope = load_json(FIXTURE_DIR / "wrong-sender-control-envelope.json")
    validate_schema(envelope, envelope_schema())
    with pytest.raises(AssertionError, match="sender"):
        semantic_validate(envelope, now_ms=NOW_MS)


def test_same_session_seq_and_message_id_replay_is_rejected():
    case = load_json(FIXTURE_DIR / "seq-replay-control-envelope.json")
    schema = envelope_schema()
    for envelope in case.values():
        validate_schema(envelope, schema)

    last_seq: dict[tuple[str, str, str], int] = {}
    seen_ids: set[tuple[str, str, str]] = set()
    semantic_validate(case["first"], now_ms=NOW_MS, last_seq=last_seq, seen_ids=seen_ids)
    with pytest.raises(AssertionError, match="replay"):
        semantic_validate(case["seq_replay"], now_ms=NOW_MS, last_seq=last_seq, seen_ids=seen_ids)

    last_seq = {}
    seen_ids = set()
    semantic_validate(case["first"], now_ms=NOW_MS, last_seq=last_seq, seen_ids=seen_ids)
    with pytest.raises(AssertionError, match="message_id replay"):
        semantic_validate(case["message_replay"], now_ms=NOW_MS, last_seq=last_seq, seen_ids=seen_ids)


def test_serialized_message_over_16_kib_is_rejected_by_wire_budget():
    case = load_json(FIXTURE_DIR / "oversized-control-envelope.json")
    envelope = load_json(FIXTURE_DIR / case["base_fixture"])
    envelope["payload"][case["payload_field"]] = "x" * case["extra_bytes"]
    validate_schema(envelope, envelope_schema())
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert len(encoded) > case["expected_limit"]
    assert len(encoded) > load_json(CONTRACT_DIR / "limits.v1.json")["wire"]["max_message_bytes"]
