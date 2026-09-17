"""Signed firmware artifact verification and streaming integrity tests."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from bridge.errors import ValidationError
from bridge.firmware_artifacts import FirmwareArtifactStore


def provision_artifact(tmp_path, *, image_ref: str = "release-1"):
    image = b"bounded firmware image"
    digest = hashlib.sha256(image).hexdigest()
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_path = tmp_path / "ota-public.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    canonical = (
        "lifeos-firmware-v1\n"
        f"{image_ref}\n"
        "lifeos-phase1-0.6.0\n"
        "fake-hw-01\n"
        "lifeos.v1\n"
        "ota_ab_v1\n"
        f"{len(image)}\n"
        f"{digest}\n"
        "1\n"
    ).encode()
    signature = private_key.sign(canonical, ec.ECDSA(hashes.SHA256()))
    manifest = {
        "schema": "lifeos.firmware-manifest.v1",
        "image_ref": image_ref,
        "version": "lifeos-phase1-0.6.0",
        "hardware_id": "fake-hw-01",
        "protocol_version": "lifeos.v1",
        "partition_layout": "ota_ab_v1",
        "size_bytes": len(image),
        "sha256_hex": digest,
        "secure_version": 1,
        "signature_algorithm": "ecdsa-p256-sha256",
        "signature_der_b64": base64.b64encode(signature).decode("ascii"),
    }
    (tmp_path / f"{image_ref}.bin").write_bytes(image)
    (tmp_path / f"{image_ref}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return FirmwareArtifactStore(tmp_path, public_path), image


def test_signed_firmware_artifact_resolves_and_streams_bounded_chunks(tmp_path):
    store, image = provision_artifact(tmp_path)

    artifact = store.resolve("release-1")

    assert b"".join(chunk for _, chunk in artifact.read_chunks(7)) == image
    assert artifact.manifest.partition_layout == "ota_ab_v1"


def test_firmware_image_change_after_preflight_is_rejected_before_first_chunk(tmp_path):
    store, image = provision_artifact(tmp_path)
    artifact = store.resolve("release-1")
    artifact.image_path.write_bytes(b"x" * len(image))

    with pytest.raises(ValidationError, match="changed after manifest verification"):
        list(artifact.read_chunks())
