"""Device discovery, claiming, and stable identity management."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable
from uuid import uuid4

from .domain import DeviceLifecycle, DeviceRecord, DiscoveryCandidate
from .errors import ConflictError, NotFoundError
from .persistence import SQLiteStore


class DeviceRegistry:
    """Keep discovery candidates separate from trusted registered devices."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self._candidates: dict[str, DiscoveryCandidate] = {}

    def discover(self, candidate: DiscoveryCandidate) -> DiscoveryCandidate:
        if not candidate.candidate_id or not candidate.hardware_id or not candidate.device_id:
            raise ValueError("discovery candidate must contain identity fields")
        self._candidates[candidate.candidate_id] = candidate
        return candidate

    def list_candidates(self) -> list[DiscoveryCandidate]:
        return list(self._candidates.values())

    def get_candidate(self, candidate_id: str) -> DiscoveryCandidate:
        candidate = self._candidates.get(candidate_id)
        if candidate is None:
            raise NotFoundError(f"discovery candidate not found: {candidate_id}")
        return candidate

    def claim(self, candidate_id: str, *, display_name: str | None = None) -> DeviceRecord:
        candidate = self.get_candidate(candidate_id)
        existing = self.find_by_hardware_id(candidate.hardware_id)
        if existing is not None:
            if existing.lifecycle_state == DeviceLifecycle.REVOKED:
                raise ConflictError(f"device identity is revoked: {existing.device_id}")
            if existing.device_id != candidate.device_id:
                raise ConflictError("hardware identity is already bound to another device id")
            del self._candidates[candidate_id]
            return existing

        record = DeviceRecord(
            device_id=candidate.device_id or f"device-{uuid4()}",
            hardware_id=candidate.hardware_id,
            display_name=display_name or candidate.device_id,
            transport_hint=candidate.transport_id,
            firmware_version=candidate.firmware_version,
            capabilities=frozenset(candidate.capabilities),
            lifecycle_state=DeviceLifecycle.REGISTERED,
        )
        if self.store.get_device(record.device_id) is not None:
            raise ConflictError(f"device id already registered: {record.device_id}")
        self.store.save_device(record)
        del self._candidates[candidate_id]
        return record

    def get(self, device_id: str) -> DeviceRecord:
        record = self.store.get_device(device_id)
        if record is None:
            raise NotFoundError(f"device not found: {device_id}")
        return record

    def list(self) -> list[DeviceRecord]:
        return self.store.list_devices()

    def find_by_hardware_id(self, hardware_id: str) -> DeviceRecord | None:
        return next(
            (record for record in self.store.list_devices() if record.hardware_id == hardware_id),
            None,
        )

    def update_from_hello(
        self,
        device_id: str,
        *,
        firmware_version: str | None,
        protocol_version: str,
        capabilities: Iterable[str],
        transport_id: str | None = None,
    ) -> DeviceRecord:
        record = self.get(device_id)
        updated = replace(
            record,
            firmware_version=firmware_version,
            protocol_version=protocol_version,
            capabilities=frozenset(capabilities),
            transport_hint=transport_id or record.transport_hint,
        )
        self.store.save_device(updated)
        return updated

    def mark_seen(self, device_id: str, *, seen_at) -> DeviceRecord:
        record = self.get(device_id)
        updated = replace(record, last_seen_at=seen_at)
        self.store.save_device(updated)
        return updated
