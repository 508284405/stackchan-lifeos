"""Provisioned, signed firmware artifacts for recoverable OTA rollout."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .errors import ValidationError


IMAGE_REF_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_FIRMWARE_IMAGE_BYTES = 4 * 1024 * 1024
SIGNATURE_ALGORITHM = "ecdsa-p256-sha256"
PARTITION_LAYOUT = "ota_ab_v1"
PROTOCOL_VERSION = "lifeos.v1"
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "image_ref",
        "version",
        "hardware_id",
        "protocol_version",
        "partition_layout",
        "size_bytes",
        "sha256_hex",
        "secure_version",
        "signature_algorithm",
        "signature_der_b64",
    }
)


@dataclass(frozen=True)
class FirmwareManifest:
    image_ref: str
    version: str
    hardware_id: str
    protocol_version: str
    partition_layout: str
    size_bytes: int
    sha256_hex: str
    secure_version: int
    signature_algorithm: str
    signature_der: bytes

    def canonical_text(self) -> bytes:
        return (
            "lifeos-firmware-v1\n"
            f"{self.image_ref}\n"
            f"{self.version}\n"
            f"{self.hardware_id}\n"
            f"{self.protocol_version}\n"
            f"{self.partition_layout}\n"
            f"{self.size_bytes}\n"
            f"{self.sha256_hex}\n"
            f"{self.secure_version}\n"
        ).encode("utf-8")

    def wire_payload(self, *, rollout_id: str) -> dict[str, object]:
        return {
            "action": "begin",
            "rollout_id": rollout_id,
            "image_ref": self.image_ref,
            "version": self.version,
            "hardware_id": self.hardware_id,
            "protocol_version": self.protocol_version,
            "partition_layout": self.partition_layout,
            "size_bytes": self.size_bytes,
            "sha256_hex": self.sha256_hex,
            "secure_version": self.secure_version,
            "signature_algorithm": self.signature_algorithm,
            "signature_der_b64": base64.b64encode(self.signature_der).decode("ascii"),
        }


@dataclass(frozen=True)
class FirmwareArtifact:
    manifest: FirmwareManifest
    image_path: Path

    def read_chunks(self, chunk_bytes: int = 3072) -> Iterator[tuple[int, bytes]]:
        """Read the provisioned image in bounded chunks and verify its digest.

        ``resolve`` verifies the image before a rollout starts.  Re-checking
        while streaming also closes the small window in which a provisioned
        file could be replaced between preflight and the final chunk.  The
        device still verifies the signed manifest and image hash; this host
        check makes the failure explicit before the next command is emitted.
        """

        if not 1 <= chunk_bytes <= 3072:
            raise ValueError("firmware chunk size must be in [1, 3072]")
        try:
            with self.image_path.open("rb") as image:
                # Images are bounded to 4 MiB.  Snapshot the verified bytes
                # before the first yield so a concurrent replacement cannot
                # fail halfway through a rollout after chunks were sent.
                content = image.read(self.manifest.size_bytes + 1)
        except OSError as exc:
            raise ValidationError("firmware image became unreadable while streaming") from exc
        if (
            len(content) != self.manifest.size_bytes
            or hashlib.sha256(content).hexdigest() != self.manifest.sha256_hex
        ):
            raise ValidationError("firmware image changed after manifest verification")
        for offset in range(0, len(content), chunk_bytes):
            yield offset, content[offset : offset + chunk_bytes]


class FirmwareArtifactStore:
    """Resolve bounded refs and verify both manifest signature and image hash."""

    def __init__(self, artifact_dir: str | Path, trusted_public_key: str | Path) -> None:
        try:
            self.artifact_dir = Path(artifact_dir).expanduser().resolve(strict=True)
            key_path = Path(trusted_public_key).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ValidationError("firmware artifact directory or trust key is unavailable") from exc
        if not self.artifact_dir.is_dir() or not key_path.is_file():
            raise ValidationError("firmware artifact directory or trust key is unavailable")
        try:
            key = serialization.load_pem_public_key(key_path.read_bytes())
        except (OSError, ValueError, TypeError) as exc:
            raise ValidationError("firmware trust key is unreadable or invalid") from exc
        if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValidationError("firmware trust key must be ECDSA P-256")
        self._key = key

    def _child(self, name: str) -> Path:
        path = (self.artifact_dir / name).resolve()
        if path.parent != self.artifact_dir:
            raise ValidationError("firmware artifact path escapes the provisioned directory")
        return path

    def resolve(self, image_ref: str) -> FirmwareArtifact:
        if not isinstance(image_ref, str) or IMAGE_REF_RE.fullmatch(image_ref) is None:
            raise ValidationError("image_ref must be a bounded artifact reference")
        manifest_path = self._child(f"{image_ref}.manifest.json")
        image_path = self._child(f"{image_ref}.bin")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValidationError("firmware manifest is unreadable") from exc
        if not isinstance(raw, dict) or set(raw) != MANIFEST_FIELDS:
            raise ValidationError("firmware manifest has an invalid shape")
        try:
            signature = base64.b64decode(raw["signature_der_b64"], validate=True)
        except (TypeError, ValueError, binascii.Error) as exc:
            raise ValidationError("firmware manifest signature is not valid base64") from exc
        manifest = FirmwareManifest(
            image_ref=raw["image_ref"],
            version=raw["version"],
            hardware_id=raw["hardware_id"],
            protocol_version=raw["protocol_version"],
            partition_layout=raw["partition_layout"],
            size_bytes=raw["size_bytes"],
            sha256_hex=raw["sha256_hex"],
            secure_version=raw["secure_version"],
            signature_algorithm=raw["signature_algorithm"],
            signature_der=signature,
        )
        if (
            raw["schema"] != "lifeos.firmware-manifest.v1"
            or manifest.image_ref != image_ref
            or not isinstance(manifest.version, str)
            or not 1 <= len(manifest.version) <= 31
            or TOKEN_RE.fullmatch(manifest.version) is None
            or not isinstance(manifest.hardware_id, str)
            or not 1 <= len(manifest.hardware_id) <= 64
            or TOKEN_RE.fullmatch(manifest.hardware_id) is None
            or manifest.protocol_version != PROTOCOL_VERSION
            or manifest.partition_layout != PARTITION_LAYOUT
            or not isinstance(manifest.size_bytes, int)
            or isinstance(manifest.size_bytes, bool)
            or not 1 <= manifest.size_bytes <= MAX_FIRMWARE_IMAGE_BYTES
            or not isinstance(manifest.sha256_hex, str)
            or SHA256_RE.fullmatch(manifest.sha256_hex) is None
            or not isinstance(manifest.secure_version, int)
            or isinstance(manifest.secure_version, bool)
            or not 0 <= manifest.secure_version <= 0xFFFFFFFF
            or manifest.signature_algorithm != SIGNATURE_ALGORITHM
            or not 8 <= len(manifest.signature_der) <= 80
        ):
            raise ValidationError("firmware manifest contains invalid or unsupported metadata")
        try:
            self._key.verify(
                manifest.signature_der,
                manifest.canonical_text(),
                ec.ECDSA(hashes.SHA256()),
            )
        except InvalidSignature as exc:
            raise ValidationError("firmware manifest signature verification failed") from exc
        try:
            stat = image_path.stat()
        except OSError as exc:
            raise ValidationError("firmware image is unavailable") from exc
        if not image_path.is_file() or stat.st_size != manifest.size_bytes:
            raise ValidationError("firmware image size does not match the signed manifest")
        digest = hashlib.sha256()
        try:
            with image_path.open("rb") as image:
                for chunk in iter(lambda: image.read(64 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ValidationError("firmware image is unreadable") from exc
        if digest.hexdigest() != manifest.sha256_hex:
            raise ValidationError("firmware image hash does not match the signed manifest")
        return FirmwareArtifact(manifest=manifest, image_path=image_path)
