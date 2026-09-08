"""W1 Web Bridge orchestration: registry, sessions, commands, and audit."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .domain import (
    AuditKind,
    AuditRecord,
    BatchState,
    BatchTarget,
    BatchTargetState,
    BatchTask,
    CommandRecord,
    CommandSource,
    CommandState,
    ControlLease,
    DeviceLifecycle,
    DeviceSession,
    DiscoveryCandidate,
    LeaseState,
    SessionState,
    TERMINAL_COMMAND_STATES,
    transition_command,
    transition_batch,
    transition_session,
    utc_now,
)
from .errors import CapabilityUnavailable, ConflictError, NotFoundError, ProtocolError, TransportError, ValidationError
from .events import EventLog
from .intent import build_intent_payload, validate_behavior, validate_speech
from .leases import ControlLeaseManager
from .maintenance import MaintenanceManager
from .rollouts import RolloutManager
from .diagnostics import export_diagnostics
from .media import CameraFrame, CameraFrameError, CameraFrameStore
from .persistence import SQLiteStore
from .registry import DeviceRegistry
from .sessions import SessionManager
from .transports.base import DeviceTransport


MAX_COMMAND_TTL_MS = 1_500
logger = logging.getLogger(__name__)
SESSION_HEARTBEAT_TIMEOUT_MS = 1_500
CAMERA_PREVIEW_FPS = 10
# This TTL only covers start -> first complete frame. Once the first frame is
# observed, the camera session remains active until explicit stop or link loss.
CAMERA_PREVIEW_FIRST_FRAME_TTL_S = 10.0
CAMERA_PREVIEW_COMMAND_TTL_MS = int(CAMERA_PREVIEW_FIRST_FRAME_TTL_S * 1000)
CAMERA_HOST_HEARTBEAT_INTERVAL_S = 0.5
CAMERA_PREVIEW_STOP_WAIT_S = 1.0
MANUAL_CONTROL_ACK_WAIT_S = 0.35
BRIDGE_SUPERVISOR_INTERVAL_S = 0.25
MAX_EVENT_ID_LENGTH = 96
MAX_DEVICE_ID_LENGTH = 64
MAX_TYPE_LENGTH = 64
MAX_COMMAND_PARAMS_BYTES = 8 * 1024
MAX_INTENT_TTL_MS = 30_000
TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
WIRE_KINDS = {"event", "command", "ack", "error", "hello"}
_HEALTH_PAYLOAD_KEYS = frozenset(
    {
        "firmware",
        "motion_enabled",
        "torque_enabled",
        "camera_ready",
        "paused",
        "fault",
        "feedback_frozen",
        "safety_faults",
        "link_lost",
        "yaw_deg",
        "pitch_deg",
        "io_state",
        "last_io_error",
        "command_generation",
        "command_applied_ms",
        "feedback_age_ms",
        "heap_free",
        "psram_free",
        "uptime_ms",
    }
)
TERMINAL_BATCH_STATES = frozenset(
    {
        BatchState.COMPLETED,
        BatchState.PARTIAL,
        BatchState.FAILED,
        BatchState.CANCELLED,
        BatchState.EXPIRED,
    }
)
_CONTROL_COMMANDS = {
    "control.status": ("status", "status"),
    "control.pause": ("pause", "motion"),
    "control.resume": ("resume", "motion"),
    "control.preflight": ("preflight", "manual_preflight_v1"),
    "control.home": ("home", "motion"),
    "control.clear_fault": ("clear_fault", "safety"),
}
_SOURCE_PRIORITY = {
    CommandSource.SYSTEM: 90,
    CommandSource.MAINTENANCE: 70,
    CommandSource.MANUAL: 80,
    CommandSource.BATCH: 50,
    CommandSource.WEB: 40,
    CommandSource.AGENT: 20,
}
_CAPABILITY_ALLOWLIST = frozenset(
    {
        "status",
        "control",
        "motion",
        "safety",
        "protocol",
        "emergency_stop",
        "manual_control_v1",
        "manual_preflight_v1",
        "behavior",
        "speech",
        "touch",
        "imu",
        "display",
        "servo_yaw",
        "servo_pitch",
        "camera",
        "health",
        "audio",
        "maintenance_confirmation",
        "maintenance_execute",
        "firmware_rollout",
    }
)


@dataclass(frozen=True)
class MappedCommand:
    """Validated Web command mapped to a registered device wire command."""

    wire_type: str
    payload: dict[str, Any]
    required_capability: str
    required_feature: str | None = None


class Bridge:
    """A small, per-device Web Bridge suitable for the W1 fake-device gate."""

    def __init__(
        self,
        store: SQLiteStore | None = None,
        *,
        clock_ms: Callable[[], int] | None = None,
        now_factory: Callable[[], datetime] | None = None,
        feature_gates: dict[str, bool] | None = None,
        event_log: EventLog | None = None,
    ) -> None:
        self.store = store or SQLiteStore()
        self.registry = DeviceRegistry(self.store)
        self.sessions = SessionManager(self.store)
        self._clock_ms = clock_ms or (lambda: time.monotonic_ns() // 1_000_000)
        self._now = now_factory or utc_now
        self.events = event_log or EventLog()
        self.leases = ControlLeaseManager(now_factory=self._now)
        self.feature_gates = {
            "control": True,
            "status": True,
            "motion": True,
            "safety": True,
            "emergency_stop": True,
            "usb_add": False,
            "manual_control_v1": False,
            "behavior": False,
            "speech": False,
            "maintenance": False,
            "firmware_rollout": False,
            # Camera preview is explicit opt-in because it is a privacy and
            # bandwidth feature. A local deployment enables it only after the
            # operator has chosen the media gate in its Bridge configuration.
            "media": False,
        }
        if feature_gates:
            self.feature_gates.update(feature_gates)
        self._transports: dict[str, DeviceTransport] = {}
        self._active_sessions: dict[str, str] = {}
        self._device_locks: dict[str, asyncio.Lock] = {}
        # Preview and continuous manual input both consume the same bounded
        # USB path. This lock linearizes their mode hand-off separately from
        # the wire writer lock below, so a camera start cannot race a manual
        # lease acquisition.
        self._control_mode_locks: dict[str, asyncio.Lock] = {}
        # Epochs fence writes that were admitted before a reconnect, transport
        # failure, or shutdown. The per-device asyncio lock is the single
        # writer linearization point for all host -> device envelopes.
        self._session_epochs: dict[str, int] = {}
        self._session_epoch_by_id: dict[str, int] = {}
        self._host_hello_event_ids: dict[str, str] = {}
        self._host_hello_envelopes: dict[str, dict[str, Any]] = {}
        self._last_host_heartbeat_at: dict[str, float] = {}
        self._supervisor_task: asyncio.Task[None] | None = None
        self._supervisor_stopping = False
        self._latest_health: dict[str, dict[str, Any]] = {}
        self.camera_frames = CameraFrameStore()
        self._active_camera_previews: dict[str, str] = {}
        self._camera_heartbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self.maintenance = MaintenanceManager(
            self.store,
            feature_enabled=lambda: self.feature_gates["maintenance"],
            now=self._now,
            session_lookup=self.sessions.get,
        )
        self.rollouts = RolloutManager(self.store, feature_enabled=lambda: self.feature_gates["firmware_rollout"], now=self._now)
        self._recover_state()

    def prepare_maintenance(self, device_id: str, operation: str, *, ttl_ms: int = 60_000):
        session = self._active_session(device_id)
        if session is None:
            raise CapabilityUnavailable("no_online_session", "maintenance_confirmation")
        return self.maintenance.prepare(device_id, session.session_id, operation, ttl_ms=ttl_ms,
                                        capabilities=session.capabilities)

    def confirm_maintenance(
        self,
        challenge_id: str,
        *,
        device_id: str,
        session_id: str,
        operation: str,
        result: str,
        valid_for_ms: int,
    ):
        return self.maintenance.confirm(
            challenge_id,
            device_id=device_id,
            session_id=session_id,
            operation=operation,
            result=result,
            valid_for_ms=valid_for_ms,
        )

    def execute_maintenance(self, task_id: str):
        task = self.maintenance.get(task_id)
        session = self._active_session(task.device_id)
        if session is None or session.session_id != task.session_id:
            self.maintenance.expire(task_id, reason="session_changed")
            raise ConflictError("maintenance task session is no longer current")
        return self.maintenance.execute(task_id, capabilities=session.capabilities if session else frozenset())

    def get_maintenance_task(self, task_id: str):
        return self.maintenance.get(task_id)

    def export_diagnostics(self, device_id: str):
        device = self.registry.get(device_id)
        session = self.latest_session_for_device(device_id)
        bundle = export_diagnostics(
            device,
            session,
            self.latest_health_for_device(device_id),
            self.store.list_audits(device_id=device_id),
            now=self._now,
        )
        self._audit(
            AuditKind.DIAGNOSTIC_EXPORTED,
            device_id=device_id,
            session_id=session.session_id if session is not None else None,
            correlation_id=bundle["bundle_id"],
            payload={"bundle_id": bundle["bundle_id"], "integrity_sha256": bundle["integrity_sha256"]},
        )
        return bundle

    def create_rollout(self, device_id: str, image_ref: str):
        self.registry.get(device_id)
        return self.rollouts.create(device_id, image_ref)

    def preflight_rollout(self, task_id: str, checks: dict[str, bool] | None = None):
        return self.rollouts.preflight(task_id, checks=checks)

    def execute_rollout(self, task_id: str):
        return self.rollouts.execute(task_id)

    def get_rollout(self, task_id: str):
        return self.rollouts.get(task_id)

    def _recover_state(self) -> None:
        """Make restart semantics explicit: sessions renegotiate, actions expire."""

        for session in self.store.list_sessions():
            if session.state != SessionState.OFFLINE:
                transition_session(session.state, SessionState.OFFLINE)
                session.state = SessionState.OFFLINE
                self.store.save_session(session)
                self._audit(
                    AuditKind.SESSION_CHANGED,
                    device_id=session.device_id,
                    session_id=session.session_id,
                    payload={"state": SessionState.OFFLINE.value, "reason": "bridge_restarted"},
                )
        for command in self.store.recover_inflight():
            self._audit(
                AuditKind.RECOVERY_EXPIRED,
                device_id=command.device_id,
                session_id=command.session_id,
                command_id=command.command_id,
                correlation_id=command.correlation_id,
                payload={"state": command.state.value, "reason": "bridge_restarted"},
            )
        for task in self.store.recover_inflight_batches():
            self._audit(
                AuditKind.BATCH_STATE_CHANGED,
                correlation_id=task.correlation_id,
                payload={"task_id": task.task_id, "state": task.aggregate_state.value, "reason": "bridge_restarted"},
            )

    def _lock_for(self, device_id: str) -> asyncio.Lock:
        lock = self._device_locks.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            self._device_locks[device_id] = lock
        return lock

    def _control_mode_lock_for(self, device_id: str) -> asyncio.Lock:
        lock = self._control_mode_locks.get(device_id)
        if lock is None:
            lock = asyncio.Lock()
            self._control_mode_locks[device_id] = lock
        return lock

    def _emergency_lock_for(self, device_id: str) -> asyncio.Lock:
        """Compatibility alias for the single per-device writer lock."""

        return self._lock_for(device_id)

    def _session_epoch(self, session: DeviceSession) -> int:
        return self._session_epochs.get(session.device_id, 0)

    def _session_write_is_current(
        self,
        session: DeviceSession,
        epoch: int,
        *,
        allow_negotiating: bool = False,
    ) -> bool:
        allowed_states = {SessionState.ONLINE, SessionState.DEGRADED}
        if allow_negotiating:
            allowed_states.add(SessionState.NEGOTIATING)
        return (
            self._active_sessions.get(session.device_id) == session.session_id
            and self._session_epochs.get(session.device_id) == epoch
            and session.state in allowed_states
            and not self._supervisor_stopping
        )

    async def _fail_transport(self, session: DeviceSession, reason: str) -> None:
        """Fence a failed transport and close it before any future session."""

        self._mark_session_offline(session, reason=reason)
        transport = self._transports.pop(session.session_id, None)
        if transport is not None:
            try:
                await transport.close()
            except (TransportError, ConnectionError, OSError):
                pass
        if self._active_sessions.get(session.device_id) == session.session_id:
            self._active_sessions.pop(session.device_id, None)
        self._host_hello_event_ids.pop(session.session_id, None)
        self._host_hello_envelopes.pop(session.session_id, None)

    async def _on_transport_disconnect(self, session_id: str, reason: str) -> None:
        """Handle a reader-side USB EOF without cancelling the reader itself."""

        try:
            session = self.sessions.get(session_id)
        except ProtocolError:
            return
        self._mark_session_offline(session, reason=reason)
        # The callback runs inside UsbSerialTransport's reader task. Defer the
        # close so that close() never awaits that same task.
        asyncio.create_task(self._finalize_failed_transport(session_id))

    async def _finalize_failed_transport(self, session_id: str) -> None:
        await asyncio.sleep(0)
        transport = self._transports.pop(session_id, None)
        if transport is not None:
            try:
                await transport.close()
            except (TransportError, ConnectionError, OSError):
                pass
        try:
            session = self.sessions.get(session_id)
        except ProtocolError:
            return
        if self._active_sessions.get(session.device_id) == session_id:
            self._active_sessions.pop(session.device_id, None)
        self._host_hello_event_ids.pop(session_id, None)
        self._host_hello_envelopes.pop(session_id, None)

    async def start_supervisor(self, *, interval_s: float = BRIDGE_SUPERVISOR_INTERVAL_S) -> None:
        """Start one lifecycle owner for session freshness and host heartbeats."""

        if not isinstance(interval_s, (int, float)) or interval_s <= 0:
            raise ValueError("supervisor interval must be positive")
        if self._supervisor_task is not None and not self._supervisor_task.done():
            return
        self._supervisor_stopping = False
        self._supervisor_task = asyncio.create_task(self._supervisor_loop(float(interval_s)))

    async def stop_supervisor(self) -> None:
        """Stop lifecycle tasks before an app releases its transports."""

        self._supervisor_stopping = True
        task = self._supervisor_task
        self._supervisor_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for device_id in list(self._camera_heartbeat_tasks):
            await self._stop_camera_host_heartbeat(device_id)

    async def _supervisor_loop(self, interval_s: float) -> None:
        loop = asyncio.get_running_loop()
        while True:
            self.expire_due()
            self.reap_control_leases()
            # Older firmware does not advertise periodic health reports. Its
            # transport is still kept alive by host heartbeat, while newer
            # health-capable firmware gets strict freshness transitions.
            self.check_freshness(require_capability="health")
            now = loop.time()
            for session_id in list(self._active_sessions.values()):
                session = self.sessions.get(session_id)
                if session.state not in {SessionState.ONLINE, SessionState.DEGRADED}:
                    continue
                if self._active_camera_previews.get(session.device_id) == session.session_id:
                    # The bounded camera heartbeat owns this interval while a
                    # preview is active; do not create a second writer stream.
                    continue
                last = self._last_host_heartbeat_at.get(session.session_id, 0.0)
                if now - last < CAMERA_HOST_HEARTBEAT_INTERVAL_S:
                    continue
                await self._send_host_heartbeat(
                    session.device_id,
                    session.session_id,
                    media_enabled=False,
                )
            await asyncio.sleep(interval_s)

    def _audit(
        self,
        kind: AuditKind,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        command_id: str | None = None,
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditRecord:
        record = AuditRecord(
            kind=kind,
            occurred_at=self._now(),
            device_id=device_id,
            session_id=session_id,
            command_id=command_id,
            correlation_id=correlation_id,
            payload=payload or {},
        )
        self.store.append_audit(record)
        event_type = {
            AuditKind.DEVICE_DISCOVERED: "device.summary.changed",
            AuditKind.DEVICE_CLAIMED: "device.summary.changed",
            AuditKind.SESSION_CHANGED: "device.session.changed",
            AuditKind.COMMAND_STATE_CHANGED: "command.state.changed",
            AuditKind.BATCH_STATE_CHANGED: "batch.state.changed",
            AuditKind.PROTOCOL_REJECTED: "device.safety.changed",
            AuditKind.MEDIA_SEQUENCE_RESYNC: "device.media.resynced",
            AuditKind.COMMAND_LATE_EVIDENCE: "command.state.changed",
            AuditKind.RECOVERY_EXPIRED: "command.state.changed",
            AuditKind.CONTROL_LEASE_CHANGED: "control.lease.changed",
            AuditKind.MAINTENANCE_TASK_CHANGED: "maintenance.task.changed",
            AuditKind.MAINTENANCE_CHALLENGE_CHANGED: "maintenance.challenge.changed",
            AuditKind.DIAGNOSTIC_EXPORTED: "diagnostic.exported",
            AuditKind.ROLLOUT_TASK_CHANGED: "rollout.task.changed",
        }.get(kind)
        if event_type is not None:
            self.events.append(
                type=event_type,
                device_id=device_id,
                session_id=session_id,
                command_id=command_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        return record

    def _audit_lease(self, lease: ControlLease) -> None:
        try:
            session = self.sessions.get(lease.session_id)
        except ProtocolError:
            session = None
        if session is not None:
            if lease.state is LeaseState.ACTIVE:
                session.active_control_lease = lease.lease_id
            elif session.active_control_lease == lease.lease_id:
                session.active_control_lease = None
            self.sessions.save(session)
        self._audit(
            AuditKind.CONTROL_LEASE_CHANGED,
            device_id=lease.device_id,
            session_id=lease.session_id,
            correlation_id=lease.lease_id,
            payload={
                "lease_id": lease.lease_id,
                "state": lease.state.value,
                "last_input_seq": lease.last_input_seq,
                "reason": lease.reason,
            },
        )

    def discover(self, candidate: DiscoveryCandidate) -> DiscoveryCandidate:
        result = self.registry.discover(candidate)
        self._audit(
            AuditKind.DEVICE_DISCOVERED,
            device_id=candidate.device_id,
            payload={
                "candidate_id": candidate.candidate_id,
                "hardware_id": candidate.hardware_id,
                "transport_id": candidate.transport_id,
            },
        )
        return result

    def claim(self, candidate_id: str, *, display_name: str | None = None):
        record = self.registry.claim(candidate_id, display_name=display_name)
        self._audit(
            AuditKind.DEVICE_CLAIMED,
            device_id=record.device_id,
            payload={"hardware_id": record.hardware_id, "display_name": record.display_name},
        )
        return record

    def active_transport_paths(self) -> set[str]:
        """Host device paths (e.g. serial ports) held by live sessions."""

        paths: set[str] = set()
        for session_id in self._active_sessions.values():
            path = getattr(self._transports.get(session_id), "path", None)
            if isinstance(path, str) and path:
                paths.add(path)
        return paths

    async def connect(self, device_id: str, transport: DeviceTransport) -> DeviceSession:
        device = self.registry.get(device_id)
        if device.lifecycle_state != DeviceLifecycle.REGISTERED:
            raise ConflictError(f"device cannot connect from {device.lifecycle_state.value}")
        previous_id = self._active_sessions.get(device_id)
        if previous_id:
            previous = self.store.get_session(previous_id)
            if previous is not None:
                self._mark_session_offline(previous, reason="replaced")

        session = self.sessions.create(device_id=device_id, transport_id=transport.transport_id)
        self._transports[session.session_id] = transport
        self._active_sessions[device_id] = session.session_id
        session_epoch = self._session_epochs.get(device_id, 0) + 1
        self._session_epochs[device_id] = session_epoch
        self._session_epoch_by_id[session.session_id] = session_epoch
        self._last_host_heartbeat_at[session.session_id] = 0.0
        if getattr(transport, "hello_host_first", False):
            self._host_hello_event_ids[session.session_id] = f"bridge-{uuid4()}"
        set_disconnect_handler = getattr(transport, "set_disconnect_handler", None)
        if callable(set_disconnect_handler):
            async def on_disconnect(reason: str, session_id: str = session.session_id) -> None:
                await self._on_transport_disconnect(session_id, reason)

            set_disconnect_handler(on_disconnect)
        self._audit(
            AuditKind.SESSION_CHANGED,
            device_id=device_id,
            session_id=session.session_id,
            payload={"state": session.state.value, "reason": "transport_connected"},
        )
        try:
            await transport.open(lambda frame: self.receive(session.session_id, frame))
            # The current firmware accepts a host-first hello, while the
            # protocol examples and fake device allow device-first hello. A
            # transport that has not already completed negotiation gets the
            # same host hello after its reader is ready.
            current = self.sessions.get(session.session_id)
            if current.state == SessionState.NEGOTIATING:
                await self._send_host_hello(current)
        except Exception:
            self._mark_session_offline(session, reason="transport_open_failed")
            raise
        return self.sessions.get(session.session_id)

    async def disconnect(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        self._mark_session_offline(session, reason="transport_disconnected")
        transport = self._transports.pop(session_id, None)
        if transport is not None:
            await transport.close()
        if self._active_sessions.get(session.device_id) == session_id:
            del self._active_sessions[session.device_id]

    def _mark_session_offline(self, session: DeviceSession, *, reason: str) -> None:
        if (
            self._active_sessions.get(session.device_id) == session.session_id
            and session.state != SessionState.OFFLINE
        ):
            self._session_epochs[session.device_id] = self._session_epochs.get(session.device_id, 0) + 1
            self._last_host_heartbeat_at.pop(session.session_id, None)
        if session.state != SessionState.OFFLINE:
            logger.warning(
                "bridge session offline device=%s session=%s reason=%s",
                session.device_id,
                session.session_id,
                reason,
            )
            transition_session(session.state, SessionState.OFFLINE)
            session.state = SessionState.OFFLINE
            self.sessions.save(session)
        self._audit(
            AuditKind.SESSION_CHANGED,
            device_id=session.device_id,
            session_id=session.session_id,
            payload={"state": SessionState.OFFLINE.value, "reason": reason},
        )
        for lease in self.leases.invalidate_session(session.session_id, reason=reason):
            self._audit_lease(lease)
        self.maintenance.expire_for_session(session.session_id, reason=reason)
        self._deactivate_camera_preview(session.device_id, session.session_id)
        self._mark_device_commands_unavailable(session, reason=reason)

    def _deactivate_camera_preview(self, device_id: str, session_id: str | None = None) -> None:
        """Drop ephemeral preview state for a session without touching SQLite."""

        active_session_id = self._active_camera_previews.get(device_id)
        if active_session_id is None or (session_id is not None and active_session_id != session_id):
            return
        self._active_camera_previews.pop(device_id, None)
        self.camera_frames.invalidate_session(device_id, active_session_id)
        heartbeat = self._camera_heartbeat_tasks.pop(device_id, None)
        if heartbeat is not None and heartbeat is not asyncio.current_task():
            heartbeat.cancel()

    def _mark_device_commands_unavailable(self, session: DeviceSession, *, reason: str) -> None:
        records = self.store.list_commands(device_id=session.device_id)
        for command in records:
            if command.state in TERMINAL_COMMAND_STATES or command.session_id != session.session_id:
                continue
            target = CommandState.TIMEOUT if command.state in {
                CommandState.ACCEPTED,
                CommandState.EXECUTING,
            } else CommandState.OFFLINE
            self._change_command(
                command,
                target,
                error={"code": target.value, "reason": reason},
            )

    def _preempt_lower_priority(
        self,
        session: DeviceSession,
        *,
        priority: int,
        reason: str,
        exclude: set[str] | None = None,
    ) -> None:
        excluded = exclude or set()
        for other in self.store.list_commands(device_id=session.device_id):
            if (
                other.command_id not in excluded
                and other.session_id == session.session_id
                and other.state not in TERMINAL_COMMAND_STATES
                and other.priority < priority
            ):
                self._change_command(
                    other,
                    CommandState.PREEMPTED,
                    error={"code": "preempted", "reason": reason},
                )

    def _change_command(
        self,
        command: CommandRecord,
        target: CommandState,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> CommandRecord:
        transition_command(command.state, target)
        previous = command.state
        command.state = target
        if result is not None:
            command.result = result
        if error is not None:
            command.error = error
        self.store.save_command(command)
        self._audit(
            AuditKind.COMMAND_STATE_CHANGED,
            device_id=command.device_id,
            session_id=command.session_id,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            payload={"from": previous.value, "to": target.value, "result": result, "error": error},
        )
        if command.type == "camera.preview.stop" and target is CommandState.COMPLETED:
            # Keep the preview and its watchdog alive until the device has
            # acknowledged the stop. Dropping it when merely sent made a
            # failed stop indistinguishable from a safe hand-off to manual
            # control.
            self._deactivate_camera_preview(command.device_id, command.session_id)
        return command

    @staticmethod
    def _validate_json_frame(frame: dict[str, Any]) -> None:
        if not isinstance(frame, dict):
            raise ProtocolError("wire frame must be an object")
        allowed = {
            "schema",
            "kind",
            "type",
            "event_id",
            "command_id",
            "correlation_id",
            "device_id",
            "seq",
            "ts_ms",
            "payload",
        }
        unknown = set(frame) - allowed
        if unknown:
            raise ProtocolError(f"unknown envelope fields: {sorted(unknown)}")
        required = {"schema", "kind", "type", "event_id", "device_id", "seq", "ts_ms", "payload"}
        if not required.issubset(frame):
            raise ProtocolError("envelope is missing required fields")
        if frame["schema"] != "lifeos.v1" or frame["kind"] not in WIRE_KINDS:
            raise ProtocolError("unsupported lifeos.v1 envelope")
        if (
            not isinstance(frame["type"], str)
            or not frame["type"]
            or len(frame["type"]) > MAX_TYPE_LENGTH
            or TYPE_RE.fullmatch(frame["type"]) is None
        ):
            raise ProtocolError("invalid envelope type")
        if (
            not isinstance(frame["event_id"], str)
            or not frame["event_id"]
            or len(frame["event_id"]) > MAX_EVENT_ID_LENGTH
        ):
            raise ProtocolError("invalid event_id")
        if (
            not isinstance(frame["device_id"], str)
            or not frame["device_id"]
            or len(frame["device_id"]) > MAX_DEVICE_ID_LENGTH
        ):
            raise ProtocolError("invalid device_id")
        if not isinstance(frame["seq"], int) or isinstance(frame["seq"], bool) or frame["seq"] < 0:
            raise ProtocolError("invalid seq")
        if not isinstance(frame["ts_ms"], int) or isinstance(frame["ts_ms"], bool) or frame["ts_ms"] < 0:
            raise ProtocolError("invalid ts_ms")
        if not isinstance(frame["payload"], dict):
            raise ProtocolError("payload must be an object")
        for field_name in ("command_id", "correlation_id"):
            if field_name in frame and (
                not isinstance(frame[field_name], str)
                or not frame[field_name]
                or len(frame[field_name]) > MAX_EVENT_ID_LENGTH
            ):
                raise ProtocolError(f"invalid {field_name}")
        try:
            encoded = json.dumps(frame, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
                "utf-8"
            )
        except (TypeError, ValueError) as exc:
            raise ProtocolError("envelope contains a non-finite or non-JSON value") from exc
        if len(encoded) > 16 * 1024:
            raise ProtocolError("envelope exceeds 16 KiB line limit")

    async def receive(self, session_id: str, frame: dict[str, Any]) -> bool:
        """Consume one device frame; invalid/late frames never raise into transport."""

        try:
            session = self.sessions.get(session_id)
        except ProtocolError:
            return False
        try:
            self._validate_json_frame(frame)
        except ProtocolError as exc:
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": str(exc)},
            )
            return False

        if frame["device_id"] != session.device_id:
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "device_id_mismatch"},
            )
            return False

        event_id = frame["event_id"]
        event_key = self._event_key(frame)
        if self.sessions.is_duplicate(session, event_key):
            self._audit(
                AuditKind.PROTOCOL_DUPLICATE,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"event_id": event_id, "type": frame["type"]},
            )
            return False

        if frame["kind"] == "hello":
            return await self._receive_hello(session, frame)

        if frame["type"] == "maintenance.confirmed":
            payload = frame["payload"]
            required = {"challenge_id", "operation", "result", "valid_for_ms"}
            if set(payload) != required or not isinstance(payload.get("challenge_id"), str):
                self._audit(
                    AuditKind.PROTOCOL_REJECTED,
                    device_id=session.device_id,
                    session_id=session.session_id,
                    payload={"reason": "invalid_maintenance_confirmation"},
                )
                return False
            try:
                self.confirm_maintenance(
                    payload["challenge_id"],
                    device_id=session.device_id,
                    session_id=session.session_id,
                    operation=payload["operation"],
                    result=payload["result"],
                    valid_for_ms=payload["valid_for_ms"],
                )
            except (ConflictError, ValidationError):
                self._audit(AuditKind.PROTOCOL_REJECTED, device_id=session.device_id, session_id=session.session_id,
                            payload={"reason": "invalid_maintenance_confirmation"})
                return False
            return True

        if session.state not in {SessionState.ONLINE, SessionState.DEGRADED, SessionState.MAINTENANCE}:
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "session_not_online", "state": session.state.value},
            )
            return False
        is_camera_frame = frame["kind"] == "event" and frame["type"] in {
            "camera.frame.begin",
            "camera.frame.chunk",
            "camera.frame.end",
        }
        if not self.sessions.accept_rx_sequence(session, frame["seq"]):
            previous_seq = (
                self.sessions.resync_media_rx_sequence(session, frame["seq"])
                if is_camera_frame
                else None
            )
            if previous_seq is not None:
                self._audit(
                    AuditKind.MEDIA_SEQUENCE_RESYNC,
                    device_id=session.device_id,
                    session_id=session.session_id,
                    payload={
                        "from_seq": previous_seq,
                        "to_seq": frame["seq"],
                        "dropped": frame["seq"] - previous_seq - 1,
                    },
                )
            else:
                self._audit(
                    AuditKind.PROTOCOL_REJECTED,
                    device_id=session.device_id,
                    session_id=session.session_id,
                    payload={"reason": "seq_out_of_order", "seq": frame["seq"]},
                )
                return False
        self.sessions.remember_event(session, event_key)

        if frame["type"] == "health.report":
            self._latest_health[session.device_id] = dict(frame["payload"])
            self.sessions.heartbeat(session, self._now())
            self.registry.mark_seen(session.device_id, seen_at=self._now())
            self._audit(
                AuditKind.HEALTH_RECEIVED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"seq": frame["seq"]},
            )
            self.events.append(
                type="telemetry.sampled",
                device_id=session.device_id,
                session_id=session.session_id,
                payload=frame["payload"],
                telemetry_key=session.device_id,
            )
            return True
        if frame["kind"] == "event" and frame["type"] in {
            "camera.frame.begin",
            "camera.frame.chunk",
            "camera.frame.end",
        }:
            return await self._receive_camera_frame(session, frame)
        if frame["kind"] == "ack" and frame["type"] == "ack.command":
            self._receive_ack(session, frame)
            return True
        if frame["type"] == "motion.completed":
            self._receive_completion(session, frame)
            return True
        if frame["kind"] == "error":
            self._receive_error(session, frame)
            return True
        return True

    async def _receive_hello(self, session: DeviceSession, frame: dict[str, Any]) -> bool:
        if session.state != SessionState.NEGOTIATING or frame["type"] != "hello.device":
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "invalid_hello_state_or_type"},
            )
            return False
        expected_hello_id = self._host_hello_event_ids.get(session.session_id)
        if expected_hello_id is not None and frame.get("correlation_id") != expected_hello_id:
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "hello_correlation_mismatch"},
            )
            return False
        if not self.sessions.accept_rx_sequence(session, frame["seq"]):
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "hello_seq_out_of_order", "seq": frame["seq"]},
            )
            return False
        payload = frame["payload"]
        versions = payload.get("protocol_versions")
        capabilities = payload.get("capabilities", [])
        firmware = payload.get("firmware")
        hardware_id = payload.get("hardware_id") or payload.get("mac")
        if (
            not isinstance(versions, list)
            or "lifeos.v1" not in versions
            or not isinstance(capabilities, list)
            or not all(isinstance(value, str) for value in capabilities)
            or (firmware is not None and not isinstance(firmware, str))
            or (hardware_id is not None and not isinstance(hardware_id, str))
        ):
            transition_session(session.state, SessionState.REJECTED)
            session.state = SessionState.REJECTED
            self.sessions.save(session)
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "hello_capability_or_protocol_mismatch"},
            )
            return False

        device = self.registry.get(session.device_id)
        if hardware_id is not None and hardware_id != device.hardware_id:
            transition_session(session.state, SessionState.REJECTED)
            session.state = SessionState.REJECTED
            self.sessions.save(session)
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "hardware_id_mismatch"},
            )
            return False

        self.sessions.remember_event(session, self._event_key(frame))
        accepted_capabilities = frozenset(capabilities) & _CAPABILITY_ALLOWLIST
        session.capabilities = accepted_capabilities
        session.last_heartbeat_at = self._now()
        transition_session(session.state, SessionState.ONLINE)
        session.state = SessionState.ONLINE
        self.sessions.save(session)
        self.registry.update_from_hello(
            session.device_id,
            firmware_version=firmware,
            protocol_version="lifeos.v1",
            capabilities=accepted_capabilities,
            transport_id=session.transport_id,
        )
        self.registry.mark_seen(session.device_id, seen_at=self._now())
        self._audit(
            AuditKind.SESSION_CHANGED,
            device_id=session.device_id,
            session_id=session.session_id,
            payload={
                "state": SessionState.ONLINE.value,
                "protocol_version": "lifeos.v1",
                "capabilities": sorted(accepted_capabilities),
            },
        )
        return await self._send_host_hello(session)

    async def _receive_camera_frame(self, session: DeviceSession, frame: dict[str, Any]) -> bool:
        """Consume one bounded JPEG frame event without retaining wire chunks."""

        if self._active_camera_previews.get(session.device_id) != session.session_id:
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "camera_preview_not_active", "media": "camera_frame"},
            )
            return False
        payload = frame["payload"]
        try:
            if frame["type"] == "camera.frame.begin":
                if set(payload) != {
                    "frame_id",
                    "format",
                    "width",
                    "height",
                    "size",
                    "chunk_count",
                } or payload.get("format") != "jpeg":
                    raise CameraFrameError("invalid camera frame begin")
                self.camera_frames.begin(
                    device_id=session.device_id,
                    session_id=session.session_id,
                    frame_id=payload["frame_id"],
                    width=payload["width"],
                    height=payload["height"],
                    size=payload["size"],
                    chunk_count=payload["chunk_count"],
                    timestamp_ms=frame["ts_ms"],
                )
                return True
            if frame["type"] == "camera.frame.chunk":
                if set(payload) != {"frame_id", "index", "chunk_count", "data"}:
                    raise CameraFrameError("invalid camera frame chunk")
                self.camera_frames.add_chunk(
                    device_id=session.device_id,
                    session_id=session.session_id,
                    frame_id=payload["frame_id"],
                    index=payload["index"],
                    chunk_count=payload["chunk_count"],
                    data=payload["data"],
                )
                return True
            if set(payload) != {"frame_id", "size"}:
                raise CameraFrameError("invalid camera frame end")
            await self.camera_frames.finish(
                device_id=session.device_id,
                session_id=session.session_id,
                frame_id=payload["frame_id"],
                size=payload["size"],
            )
            for command in reversed(self.store.list_commands(device_id=session.device_id)):
                if (
                    command.type == "camera.preview.start"
                    and command.session_id == session.session_id
                    and command.state is CommandState.ACCEPTED
                ):
                    self._change_command(
                        command,
                        CommandState.COMPLETED,
                        result={"status": "completed", "frame_id": payload["frame_id"]},
                    )
                    break
            # A complete frame is device-originated liveness evidence for
            # legacy camera firmware that does not advertise health reports.
            self.sessions.heartbeat(session, self._now())
            self.registry.mark_seen(session.device_id, seen_at=self._now())
        except (CameraFrameError, KeyError, TypeError) as exc:
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": str(exc), "media": "camera_frame"},
            )
            return False
        # Frame bytes stay on the media path. Do not put even high-rate frame
        # metadata into the general UI event stream; the MJPEG consumer only
        # needs the ephemeral latest-frame store.
        return True

    @staticmethod
    def _event_key(frame: dict[str, Any]) -> str:
        """Deduplicate ACKs by command as a compatibility shim.

        The current ESP-IDF firmware uses a fixed ACK event id while retaining
        a unique command correlation id. Other event kinds keep the strict
        lifeos.v1 event-id window.
        """

        if (
            frame.get("kind") == "ack"
            and frame.get("type") == "ack.command"
            and isinstance(frame.get("correlation_id"), str)
        ):
            return f"ack:{frame['event_id']}:{frame['correlation_id']}"
        return frame["event_id"]

    async def _send_host_hello(self, session: DeviceSession, *, retry: bool = False) -> bool:
        if session.host_hello_sent and not retry:
            return True
        async with self._lock_for(session.device_id):
            transport = self._transports.get(session.session_id)
            if transport is None:
                self._mark_session_offline(session, reason="transport_missing")
                return False
            if session.host_hello_sent and not retry:
                return True
            epoch = self._session_epoch(session)
            if not self._session_write_is_current(session, epoch, allow_negotiating=True):
                return False
            host_hello = self._host_hello_envelopes.get(session.session_id)
            if host_hello is None:
                session.host_hello_sent = True
                self.sessions.save(session)
                # A real USB transport is host-first: the device may not have
                # sent its hello yet when this first host hello is emitted.
                # Keep the trusted discovery capabilities as the bootstrap
                # baseline, then let a device hello replace them once
                # negotiation completes.
                device = self.registry.get(session.device_id)
                advertised_capabilities = set(session.capabilities) | set(device.capabilities)
                if not self.feature_gates.get("manual_control_v1", False):
                    # Do not negotiate the physical dead-man capability with
                    # the device unless the Bridge gate is explicitly enabled.
                    advertised_capabilities.discard("manual_control_v1")
                host_hello = {
                    "schema": "lifeos.v1",
                    "kind": "hello",
                    "type": "hello.host",
                    "event_id": self._host_hello_event_ids.setdefault(
                        session.session_id, f"bridge-{uuid4()}"
                    ),
                    "device_id": session.device_id,
                    "seq": self.sessions.next_tx_sequence(session),
                    "ts_ms": self._clock_ms(),
                    "payload": {
                        "protocol_versions": ["lifeos.v1"],
                        "capabilities": sorted(advertised_capabilities),
                        "session_nonce": session.nonce,
                        # The media feature gate permits preview requests; it
                        # does not mean a freshly connected device should keep
                        # an old camera stream alive.
                        "media_enabled": False,
                    },
                }
                self._host_hello_envelopes[session.session_id] = host_hello
            try:
                await transport.send(host_hello)
            except (TransportError, ConnectionError, OSError) as exc:
                await self._fail_transport(session, str(exc))
                return False
            self._last_host_heartbeat_at[session.session_id] = asyncio.get_running_loop().time()
            return True

    async def retry_host_hello(self, device_id: str) -> bool:
        """Replay the exact host-first hello while a real device is booting."""

        session_id = self._active_sessions.get(device_id)
        if session_id is None:
            return False
        session = self.sessions.get(session_id)
        if session.state is SessionState.ONLINE:
            return True
        if session.state is not SessionState.NEGOTIATING:
            return False
        return await self._send_host_hello(session, retry=True)

    def _receive_ack(self, session: DeviceSession, frame: dict[str, Any]) -> None:
        payload = frame["payload"]
        command_id = frame.get("correlation_id") or payload.get("command_id")
        if not isinstance(command_id, str):
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                payload={"reason": "ack_missing_command_id"},
            )
            return
        command = self.store.get_command(command_id)
        if command is None or command.session_id != session.session_id:
            self._audit(
                AuditKind.COMMAND_LATE_EVIDENCE,
                device_id=session.device_id,
                session_id=session.session_id,
                command_id=command_id,
                payload={"reason": "ack_for_unknown_or_other_session", "status": payload.get("status")},
            )
            return
        status = payload.get("status")
        if status in {"accepted", "clamped"}:
            # Camera start remains ACCEPTED until the first complete JPEG
            # proves the mode is producing frames. Pause/resume mutate the
            # device safety state synchronously, while camera stop and
            # zero-motion preflight are bounded control hand-offs; all are
            # terminal once the device accepts their bounded work. The next
            # health snapshot still decides whether manual controls unlock.
            target = (
                CommandState.COMPLETED
                if command.type in {
                    "control.pause",
                    "control.resume",
                    "camera.preview.stop",
                    "control.preflight",
                    "manual_control",
                }
                else CommandState.ACCEPTED
            )
        elif status == "completed":
            target = CommandState.COMPLETED
        elif status == "duplicate":
            target = CommandState.COMPLETED if command.type == "manual_control" else CommandState.ACCEPTED
        elif status == "rejected":
            target = (
                CommandState.SAFETY_BLOCKED
                if payload.get("error_code") in {"safety_blocked", "fault_latched"}
                else CommandState.REJECTED
            )
        else:
            self._audit(
                AuditKind.PROTOCOL_REJECTED,
                device_id=session.device_id,
                session_id=session.session_id,
                command_id=command_id,
                payload={"reason": "ack_status_unknown", "status": status},
            )
            return
        evidence = {"status": status, "idempotent": bool(payload.get("idempotent", False))}
        if command.type == "control.status":
            evidence.update(
                {
                    key: payload[key]
                    for key in _HEALTH_PAYLOAD_KEYS
                    if key in payload
                }
            )
            health = {
                key: evidence[key]
                for key in _HEALTH_PAYLOAD_KEYS
                if key in evidence
            }
            if health:
                self._latest_health[session.device_id] = health
                self.events.append(
                    type="telemetry.sampled",
                    device_id=session.device_id,
                    session_id=session.session_id,
                    command_id=command.command_id,
                    payload=health,
                    telemetry_key=session.device_id,
                )
        if payload.get("error_code"):
            evidence["error_code"] = payload["error_code"]
        if command.state in TERMINAL_COMMAND_STATES:
            command.late_evidence.append(evidence)
            self.store.save_command(command)
            self._audit(
                AuditKind.COMMAND_LATE_EVIDENCE,
                device_id=command.device_id,
                session_id=command.session_id,
                command_id=command.command_id,
                correlation_id=command.correlation_id,
                payload=evidence,
            )
            return
        self._change_command(command, target, result=evidence, error=(
            {"code": payload["error_code"]} if payload.get("error_code") else None
        ))

    def _receive_completion(self, session: DeviceSession, frame: dict[str, Any]) -> None:
        payload = frame["payload"]
        command_id = frame.get("correlation_id") or payload.get("action_id")
        if not isinstance(command_id, str):
            return
        command = self.store.get_command(command_id)
        if command is None or command.session_id != session.session_id:
            self._audit(
                AuditKind.COMMAND_LATE_EVIDENCE,
                device_id=session.device_id,
                session_id=session.session_id,
                command_id=command_id,
                payload={"reason": "completion_for_unknown_or_other_session"},
            )
            return
        result = {"status": "completed", "actual": payload.get("actual", {})}
        if command.state in TERMINAL_COMMAND_STATES:
            command.late_evidence.append(result)
            self.store.save_command(command)
            self._audit(
                AuditKind.COMMAND_LATE_EVIDENCE,
                device_id=command.device_id,
                session_id=command.session_id,
                command_id=command.command_id,
                payload=result,
            )
            return
        self._change_command(command, CommandState.COMPLETED, result=result)

    def _receive_error(self, session: DeviceSession, frame: dict[str, Any]) -> None:
        payload = frame["payload"]
        command_id = frame.get("correlation_id") or payload.get("command_id")
        if not isinstance(command_id, str):
            return
        command = self.store.get_command(command_id)
        if command is None or command.session_id != session.session_id:
            return
        target = (
            CommandState.SAFETY_BLOCKED
            if payload.get("code") in {"safety_blocked", "fault_latched"}
            else CommandState.REJECTED
        )
        if command.state in TERMINAL_COMMAND_STATES:
            command.late_evidence.append({"error": payload.get("code", "unknown")})
            self.store.save_command(command)
            return
        error = {"code": payload.get("code", "internal")}
        detail = payload.get("detail")
        if isinstance(detail, str) and detail:
            error["detail"] = detail[:256]
        self._change_command(command, target, error=error)
        if command.type == "camera.preview.start" and target in {
            CommandState.REJECTED,
            CommandState.SAFETY_BLOCKED,
            CommandState.OFFLINE,
            CommandState.TIMEOUT,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
            CommandState.CANCELLED,
        }:
            if self._active_camera_previews.get(command.device_id) == command.session_id:
                self._active_camera_previews.pop(command.device_id, None)
            camera_heartbeat = self._camera_heartbeat_tasks.pop(command.device_id, None)
            if camera_heartbeat is not None:
                camera_heartbeat.cancel()

    def _active_session(self, device_id: str) -> DeviceSession | None:
        session_id = self._active_sessions.get(device_id)
        if session_id is None:
            return None
        session = self.sessions.get(session_id)
        if session is None or session.state not in {SessionState.ONLINE, SessionState.DEGRADED}:
            return None
        return session

    @staticmethod
    def _validated_params(params: dict[str, Any] | None) -> dict[str, Any]:
        if params is None:
            normalized: dict[str, Any] = {}
        elif not isinstance(params, dict):
            raise ValidationError("command params must be an object")
        else:
            normalized = dict(params)
        try:
            encoded_params = json.dumps(
                normalized,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValidationError("command params must contain finite JSON values") from exc
        if len(encoded_params) > MAX_COMMAND_PARAMS_BYTES:
            raise ValidationError(f"command params exceed {MAX_COMMAND_PARAMS_BYTES} bytes")
        return normalized

    @staticmethod
    def map_web_command(
        command_type: str,
        params: dict[str, Any],
        *,
        issued_at_ms: int,
        expires_at_ms: int,
    ) -> MappedCommand:
        if not isinstance(command_type, str) or command_type.startswith("command."):
            raise ValidationError("browser cannot submit raw lifeos command types")
        if not isinstance(params, dict):
            raise ValidationError("command params must be an object")
        if command_type == "emergency_stop":
            allowed = {"reason"}
            required = "safety"
            wire_type = "command.emergency_stop"
            action = None
        elif command_type == "manual_control":
            # The name is registered so the API can return a structured
            # capability-unavailable result. It can never be sent while the
            # versioned manual_control_v1 gate is disabled.
            if params:
                raise ValidationError("manual_control_v1 is not enabled")
            return MappedCommand(
                wire_type="",
                payload={},
                required_capability="manual_control_v1",
            )
        elif command_type in _CONTROL_COMMANDS:
            action, required = _CONTROL_COMMANDS[command_type]
            allowed = {"reason"}
            if action == "clear_fault":
                allowed.add("local_confirmation")
            wire_type = "command.control"
        elif command_type in {"camera.preview.start", "camera.preview.stop"}:
            if params:
                raise ValidationError("camera preview accepts no browser camera parameters")
            action = "start" if command_type.endswith(".start") else "stop"
            payload = {"action": action}
            if action == "start":
                payload.update({"fps": CAMERA_PREVIEW_FPS, "duration_ms": 0})
            return MappedCommand(
                wire_type="command.camera_preview",
                payload=payload,
                required_capability="camera",
                required_feature="media",
            )
        else:
            raise ValidationError(f"unsupported web command type: {command_type}")
        unknown = set(params) - allowed
        if unknown:
            raise ValidationError(f"unsupported command params: {sorted(unknown)}")
        # Host and device monotonic clocks are independent. Keep lifecycle
        # expiry on the host CommandRecord, but do not put host timestamps into
        # the device control payload. The device owns its local safety timing.
        payload: dict[str, Any] = {}
        if action is not None:
            payload["action"] = action
        if "reason" in params:
            reason = params["reason"]
            if not isinstance(reason, str) or len(reason) > 128:
                raise ValidationError("reason must be a string of at most 128 characters")
            payload["reason"] = reason
        if action == "clear_fault":
            if params.get("local_confirmation") is not True:
                raise ValidationError("clear_fault requires local_confirmation=true")
            payload["local_confirmation"] = True
        return MappedCommand(
            wire_type=wire_type,
            payload=payload,
            required_capability=required,
            required_feature="manual_control_v1" if action == "preflight" else None,
        )

    async def submit_behavior(
        self,
        device_id: str,
        *,
        name: str,
        intensity: float = 0.5,
        duration_ms: int = 1_000,
        source: CommandSource | str = CommandSource.WEB,
        ttl_ms: int = MAX_INTENT_TTL_MS,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> CommandRecord:
        return await self.submit_intent(
            device_id,
            behaviors=[
                {"name": name, "intensity": intensity, "duration_ms": duration_ms}
            ],
            source=source,
            ttl_ms=ttl_ms,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def submit_speech(
        self,
        device_id: str,
        *,
        text: str,
        voice: str = "default",
        source: CommandSource | str = CommandSource.WEB,
        ttl_ms: int = MAX_INTENT_TTL_MS,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> CommandRecord:
        return await self.submit_intent(
            device_id,
            speech={"text": text, "voice": voice},
            source=source,
            ttl_ms=ttl_ms,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )

    async def submit_intent(
        self,
        device_id: str,
        *,
        behaviors: list[dict[str, Any]] | None = None,
        speech: dict[str, Any] | None = None,
        source: CommandSource | str = CommandSource.WEB,
        ttl_ms: int = MAX_INTENT_TTL_MS,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> CommandRecord:
        """Submit an allowlisted semantic intent; never raw device parameters."""

        behavior_values = list(behaviors or [])
        behavior_specs = []
        for item in behavior_values:
            if not isinstance(item, dict) or set(item) != {"name", "intensity", "duration_ms"}:
                raise ValidationError("behavior requires only name, intensity, and duration_ms")
            behavior_specs.append(
                validate_behavior(item["name"], item["intensity"], item["duration_ms"])
            )
        speech_spec = None
        if speech is not None:
            if not isinstance(speech, dict) or set(speech) - {"text", "voice"}:
                raise ValidationError("speech accepts only text and voice")
            speech_spec = validate_speech(speech.get("text"), speech.get("voice", "default"))
        if not behavior_specs and speech_spec is None:
            raise ValidationError("intent must contain behavior or speech")
        try:
            source_value = CommandSource(source)
        except ValueError as exc:
            raise ValidationError(f"unknown command source: {source}") from exc
        if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or not 0 < ttl_ms <= MAX_INTENT_TTL_MS:
            raise ValidationError(f"intent ttl_ms must be between 1 and {MAX_INTENT_TTL_MS}")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > MAX_EVENT_ID_LENGTH
        ):
            raise ValidationError("idempotency_key must be a non-empty bounded string")
        if correlation_id is not None and (
            not isinstance(correlation_id, str)
            or not correlation_id
            or len(correlation_id) > MAX_EVENT_ID_LENGTH
        ):
            raise ValidationError("correlation_id must be a non-empty bounded string")
        command_type = "intent" if behavior_specs and speech_spec else (
            "behavior" if behavior_specs else "speech"
        )
        if idempotency_key:
            existing = self.store.get_command_by_idempotency(device_id, idempotency_key)
            if existing is not None:
                if existing.type != command_type:
                    raise ConflictError("idempotency key is already bound to another command")
                return existing

        command_id = f"cmd-{uuid4()}"
        issued_at_ms = self._clock_ms()
        issued_at = self._now()
        payload = build_intent_payload(
            run_id=correlation_id or command_id,
            expires_at_ms=issued_at_ms + ttl_ms,
            source=source_value.value,
            behaviors=behavior_specs,
            speech=speech_spec,
        )
        session = self._active_session(device_id)
        command = CommandRecord(
            command_id=command_id,
            device_id=device_id,
            session_id=session.session_id if session else None,
            source=source_value,
            type=command_type,
            wire_type="command.intent",
            payload=payload,
            priority=_SOURCE_PRIORITY[source_value],
            issued_at=issued_at,
            expires_at=issued_at + timedelta(milliseconds=ttl_ms),
            issued_at_ms=issued_at_ms,
            expires_at_ms=issued_at_ms + ttl_ms,
            correlation_id=correlation_id or command_id,
            idempotency_key=idempotency_key,
        )
        self.store.save_command(command)
        self._audit(
            AuditKind.COMMAND_STATE_CHANGED,
            device_id=device_id,
            session_id=command.session_id,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            payload={"from": None, "to": CommandState.CREATED.value, "type": command.type},
        )
        required_features = []
        if behavior_specs:
            required_features.append(("behavior", "behavior"))
        if speech_spec is not None:
            required_features.append(("speech", "speech"))
        for feature, capability in required_features:
            if not self.feature_gates.get(feature, False):
                return self._change_command(
                    command,
                    CommandState.REJECTED,
                    error={
                        "code": "capability_unavailable",
                        "reason": "feature_gate_disabled",
                        "required_capability": capability,
                    },
                )
        if session is None:
            return self._change_command(
                command,
                CommandState.OFFLINE,
                error={"code": "offline", "reason": "no_online_session"},
            )
        for feature, capability in required_features:
            if capability not in session.capabilities:
                return self._change_command(
                    command,
                    CommandState.REJECTED,
                    error={
                        "code": "capability_unavailable",
                        "reason": "device_not_declared",
                        "required_capability": capability,
                    },
                )
        self._preempt_lower_priority(
            session,
            priority=command.priority,
            reason=f"{command.type}_requested",
            exclude={command.command_id},
        )
        self._change_command(command, CommandState.VALIDATED)
        session_epoch = self._session_epoch(session)
        async with self._lock_for(device_id):
            latest_before_dispatch = self.store.get_command(command.command_id)
            if latest_before_dispatch is not None and latest_before_dispatch.state in TERMINAL_COMMAND_STATES:
                return latest_before_dispatch
            current = self._active_session(device_id)
            transport = self._transports.get(command.session_id or "")
            if (
                current is None
                or transport is None
                or current.session_id != command.session_id
                or not self._session_write_is_current(current, session_epoch)
            ):
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": "session_changed_before_dispatch"},
                )
            if command.expires_at_ms <= self._clock_ms():
                return self._change_command(
                    command,
                    CommandState.EXPIRED,
                    error={"code": "expired", "reason": "command_ttl_elapsed"},
                )
            self._change_command(command, CommandState.ROUTED)
            envelope = {
                "schema": "lifeos.v1",
                "kind": "command",
                "type": command.wire_type,
                "event_id": command.command_id,
                "correlation_id": command.correlation_id,
                "device_id": command.device_id,
                "seq": self.sessions.next_tx_sequence(current),
                "ts_ms": issued_at_ms,
                "payload": payload,
            }
            self._change_command(command, CommandState.SENT)
            try:
                await transport.send(envelope)
            except (TransportError, ConnectionError, OSError) as exc:
                await self._fail_transport(current, str(exc))
                latest = self.store.get_command(command.command_id)
                if latest is not None and latest.state in TERMINAL_COMMAND_STATES:
                    return latest
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": str(exc)},
                )
        return self.store.get_command(command.command_id) or command

    async def submit_emergency_stop(
        self,
        device_id: str,
        *,
        reason: str | None = None,
        source: CommandSource | str = CommandSource.WEB,
        ttl_ms: int = MAX_COMMAND_TTL_MS,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> CommandRecord:
        """Send an emergency stop through the dedicated high-priority hook."""

        device = self.registry.get(device_id)
        try:
            source_value = CommandSource(source)
        except ValueError as exc:
            raise ValidationError(f"unknown command source: {source}") from exc
        if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or not 0 < ttl_ms <= MAX_COMMAND_TTL_MS:
            raise ValidationError(f"ttl_ms must be between 1 and {MAX_COMMAND_TTL_MS}")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 128):
            raise ValidationError("reason must be a string of at most 128 characters")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not idempotency_key
            or len(idempotency_key) > MAX_EVENT_ID_LENGTH
        ):
            raise ValidationError("idempotency_key must be a non-empty bounded string")
        if correlation_id is not None and (
            not isinstance(correlation_id, str)
            or not correlation_id
            or len(correlation_id) > MAX_EVENT_ID_LENGTH
        ):
            raise ValidationError("correlation_id must be a non-empty bounded string")
        if idempotency_key:
            existing = self.store.get_command_by_idempotency(device_id, idempotency_key)
            if existing is not None:
                if existing.type != "emergency_stop":
                    raise ConflictError("idempotency key is already bound to another command")
                return existing

        command_id = f"cmd-{uuid4()}"
        issued_at_ms = self._clock_ms()
        issued_at = self._now()
        session = self._active_session(device_id)
        command = CommandRecord(
            command_id=command_id,
            device_id=device.device_id,
            session_id=session.session_id if session else None,
            source=source_value,
            type="emergency_stop",
            wire_type="command.emergency_stop",
            payload={"reason": reason} if reason is not None else {},
            priority=100,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(milliseconds=ttl_ms),
            issued_at_ms=issued_at_ms,
            expires_at_ms=issued_at_ms + ttl_ms,
            correlation_id=correlation_id or command_id,
            idempotency_key=idempotency_key,
        )
        self.store.save_command(command)
        self._audit(
            AuditKind.COMMAND_STATE_CHANGED,
            device_id=command.device_id,
            session_id=command.session_id,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            payload={"from": None, "to": CommandState.CREATED.value, "type": command.type},
        )
        if session is None:
            return self._change_command(
                command,
                CommandState.OFFLINE,
                error={"code": "offline", "reason": "no_online_session", "not_delivered": True},
            )
        if not self.feature_gates.get("safety", False) or not (
            "safety" in session.capabilities or "emergency_stop" in session.capabilities
        ):
            return self._change_command(
                command,
                CommandState.REJECTED,
                error={
                    "code": "capability_unavailable",
                    "reason": "device_not_declared",
                    "required_capability": "safety",
                },
            )

        # A remote emergency does not claim to be a local stop, but it does
        # preempt ordinary host commands already admitted for this session.
        for lease in self.leases.list():
            if lease.session_id == session.session_id and lease.state is LeaseState.ACTIVE:
                self._audit_lease(self.leases.preempt(lease.lease_id, reason="emergency_stop"))
        self._preempt_lower_priority(
            session,
            priority=command.priority,
            reason="emergency_stop",
            exclude={command.command_id},
        )

        session_epoch = self._session_epoch(session)
        async with self._lock_for(device_id):
            current = self._active_session(device_id)
            transport = self._transports.get(command.session_id or "")
            if (
                current is None
                or transport is None
                or current.session_id != command.session_id
                or not self._session_write_is_current(current, session_epoch)
            ):
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": "session_changed_before_dispatch", "not_delivered": True},
                )
            if command.expires_at_ms <= self._clock_ms():
                return self._change_command(
                    command,
                    CommandState.EXPIRED,
                    error={"code": "expired", "reason": "command_ttl_elapsed", "not_delivered": True},
                )
            self._change_command(command, CommandState.VALIDATED)
            self._change_command(command, CommandState.ROUTED)
            envelope = {
                "schema": "lifeos.v1",
                "kind": "command",
                "type": command.wire_type,
                "event_id": command.command_id,
                "correlation_id": command.correlation_id,
                "device_id": command.device_id,
                "seq": self.sessions.next_tx_sequence(current),
                "ts_ms": issued_at_ms,
                "payload": command.payload,
            }
            self._change_command(command, CommandState.SENT)
            send_priority = getattr(transport, "send_priority", transport.send)
            try:
                await send_priority(envelope)
            except (TransportError, ConnectionError, OSError) as exc:
                await self._fail_transport(current, str(exc))
                latest = self.store.get_command(command.command_id)
                if latest is not None and latest.state in TERMINAL_COMMAND_STATES:
                    return latest
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": str(exc), "not_delivered": True},
                )
        return self.store.get_command(command.command_id) or command

    async def submit_command(
        self,
        device_id: str,
        command_type: str,
        *,
        params: dict[str, Any] | None = None,
        source: CommandSource | str = CommandSource.WEB,
        ttl_ms: int = MAX_COMMAND_TTL_MS,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> CommandRecord:
        if command_type == "emergency_stop":
            command_params = params or {}
            if not isinstance(command_params, dict):
                raise ValidationError("command params must be an object")
            unknown = set(command_params) - {"reason"}
            if unknown:
                raise ValidationError(f"unsupported command params: {sorted(unknown)}")
            return await self.submit_emergency_stop(
                device_id,
                reason=command_params.get("reason"),
                source=source,
                ttl_ms=ttl_ms,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )
        device = self.registry.get(device_id)
        try:
            source_value = CommandSource(source)
        except ValueError as exc:
            raise ValidationError(f"unknown command source: {source}") from exc
        max_ttl_ms = (
            CAMERA_PREVIEW_COMMAND_TTL_MS
            if command_type == "camera.preview.start"
            else MAX_COMMAND_TTL_MS
        )
        if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or not 0 < ttl_ms <= max_ttl_ms:
            raise ValidationError(f"ttl_ms must be between 1 and {max_ttl_ms}")
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > MAX_EVENT_ID_LENGTH
        ):
            raise ValidationError("idempotency_key must be a non-empty bounded string")
        if correlation_id is not None and (
            not isinstance(correlation_id, str) or not correlation_id or len(correlation_id) > MAX_EVENT_ID_LENGTH
        ):
            raise ValidationError("correlation_id must be a non-empty bounded string")
        params = self._validated_params(params)
        if idempotency_key:
            existing = self.store.get_command_by_idempotency(device_id, idempotency_key)
            if existing is not None:
                if existing.type != command_type:
                    raise ConflictError("idempotency key is already bound to another command")
                return existing

        command_id = f"cmd-{uuid4()}"
        issued_at_ms = self._clock_ms()
        expires_at_ms = issued_at_ms + ttl_ms
        issued_at = self._now()
        mapped: MappedCommand | None = None
        validation_error: dict[str, Any] | None = None
        try:
            mapped = self.map_web_command(
                command_type,
                params,
                issued_at_ms=issued_at_ms,
                expires_at_ms=expires_at_ms,
            )
        except ValidationError as exc:
            validation_error = {"code": "validation_error", "reason": str(exc)}

        session = self._active_session(device_id)
        wire_type = mapped.wire_type if mapped else ""
        payload = mapped.payload if mapped else params
        command = CommandRecord(
            command_id=command_id,
            device_id=device.device_id,
            session_id=session.session_id if session else None,
            source=source_value,
            type=command_type,
            wire_type=wire_type,
            payload=payload,
            priority=100 if command_type == "emergency_stop" else _SOURCE_PRIORITY[source_value],
            issued_at=issued_at,
            expires_at=issued_at + timedelta(milliseconds=ttl_ms),
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            correlation_id=correlation_id or command_id,
            idempotency_key=idempotency_key,
        )
        self.store.save_command(command)
        self._audit(
            AuditKind.COMMAND_STATE_CHANGED,
            device_id=device_id,
            session_id=command.session_id,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            payload={"from": None, "to": CommandState.CREATED.value, "type": command_type},
        )

        if validation_error is not None:
            return self._change_command(command, CommandState.REJECTED, error=validation_error)
        assert mapped is not None
        feature_gate = mapped.required_feature or mapped.required_capability
        if not self.feature_gates.get(feature_gate, False):
            error = {
                "code": "capability_unavailable",
                "reason": "feature_gate_disabled",
                "required_capability": mapped.required_capability,
            }
            if mapped.required_feature is not None:
                error["required_feature"] = mapped.required_feature
            return self._change_command(
                command,
                CommandState.REJECTED,
                error=error,
            )
        if command_type == "manual_control":
            return self._change_command(
                command,
                CommandState.REJECTED,
                error={
                    "code": "capability_unavailable",
                    "reason": "control_lease_required",
                    "required_capability": "manual_control_v1",
                },
            )
        if session is None:
            return self._change_command(
                command,
                CommandState.OFFLINE,
                error={"code": "offline", "reason": "no_online_session"},
            )
        if mapped.required_capability not in session.capabilities:
            return self._change_command(
                command,
                CommandState.REJECTED,
                error={
                    "code": "capability_unavailable",
                    "reason": "device_not_declared",
                    "required_capability": mapped.required_capability,
                },
            )

        session_epoch = self._session_epoch(session)
        self._change_command(command, CommandState.VALIDATED)
        async with self._lock_for(device_id):
            latest_before_dispatch = self.store.get_command(command.command_id)
            if latest_before_dispatch is not None and latest_before_dispatch.state in TERMINAL_COMMAND_STATES:
                return latest_before_dispatch
            session = self._active_session(device_id)
            transport = self._transports.get(command.session_id or "")
            if (
                session is None
                or transport is None
                or session.session_id != command.session_id
                or not self._session_write_is_current(session, session_epoch)
            ):
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": "session_changed_before_dispatch"},
                )
            if command.expires_at_ms <= self._clock_ms():
                return self._change_command(
                    command,
                    CommandState.EXPIRED,
                    error={"code": "expired", "reason": "command_ttl_elapsed"},
                )
            self._change_command(command, CommandState.ROUTED)
            envelope = {
                "schema": "lifeos.v1",
                "kind": "command",
                "type": mapped.wire_type,
                "event_id": command.command_id,
                "correlation_id": command.correlation_id,
                "device_id": command.device_id,
                "seq": self.sessions.next_tx_sequence(session),
                "ts_ms": issued_at_ms,
                "payload": mapped.payload,
            }
            self._change_command(command, CommandState.SENT)
            if command_type == "camera.preview.start":
                self._active_camera_previews[device_id] = session.session_id
            try:
                await transport.send(envelope)
            except (TransportError, ConnectionError, OSError) as exc:
                if command_type == "camera.preview.start" and self._active_camera_previews.get(device_id) == session.session_id:
                    self._active_camera_previews.pop(device_id, None)
                await self._fail_transport(session, str(exc))
                latest = self.store.get_command(command.command_id)
                if latest is not None and latest.state in TERMINAL_COMMAND_STATES:
                    return latest
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": str(exc)},
                )
        result = self.store.get_command(command.command_id) or command
        if command_type == "camera.preview.start" and result.state in {
            CommandState.REJECTED,
            CommandState.SAFETY_BLOCKED,
            CommandState.OFFLINE,
            CommandState.TIMEOUT,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
            CommandState.CANCELLED,
        }:
            if self._active_camera_previews.get(device_id) == result.session_id:
                self._active_camera_previews.pop(device_id, None)
        return result

    def expire_due(self, *, now_ms: int | None = None) -> list[CommandRecord]:
        current_ms = self._clock_ms() if now_ms is None else now_ms
        expired: list[CommandRecord] = []
        for command in self.store.list_commands():
            if command.state in TERMINAL_COMMAND_STATES or command.expires_at_ms > current_ms:
                continue
            target = (
                CommandState.TIMEOUT
                if command.state in {CommandState.SENT, CommandState.ACCEPTED, CommandState.EXECUTING}
                else CommandState.EXPIRED
            )
            changed = self._change_command(
                command,
                target,
                error={"code": target.value, "reason": "command_ttl_elapsed"},
            )
            expired.append(changed)
            if command.type == "camera.preview.start":
                self._deactivate_camera_preview(command.device_id, command.session_id)
        return expired

    def check_freshness(
        self,
        *,
        now: datetime | None = None,
        timeout_ms: int = SESSION_HEARTBEAT_TIMEOUT_MS,
        require_capability: str | None = None,
    ) -> list[DeviceSession]:
        current = now or self._now()
        changed: list[DeviceSession] = []
        for session_id in list(self._active_sessions.values()):
            session = self.sessions.get(session_id)
            if session is None or session.state not in {SessionState.ONLINE, SessionState.DEGRADED}:
                continue
            if require_capability is not None and require_capability not in session.capabilities:
                continue
            last = session.last_heartbeat_at or session.connected_at
            age_ms = int((current - last).total_seconds() * 1000)
            if age_ms <= timeout_ms:
                continue
            if session.state == SessionState.ONLINE:
                transition_session(session.state, SessionState.DEGRADED)
                session.state = SessionState.DEGRADED
                self.sessions.save(session)
                self._audit(
                    AuditKind.SESSION_CHANGED,
                    device_id=session.device_id,
                    session_id=session.session_id,
                    payload={"state": SessionState.DEGRADED.value, "reason": "heartbeat_stale"},
                )
            if age_ms > timeout_ms * 2:
                self._mark_session_offline(session, reason="heartbeat_timeout")
            changed.append(self.sessions.get(session.session_id))
        return changed

    def _change_batch(self, task: BatchTask, target: BatchState) -> BatchTask:
        transition_batch(task.aggregate_state, target)
        previous = task.aggregate_state
        task.aggregate_state = target
        self.store.save_batch(task)
        self._audit(
            AuditKind.BATCH_STATE_CHANGED,
            correlation_id=task.correlation_id,
            payload={
                "task_id": task.task_id,
                "from": previous.value,
                "to": target.value,
            },
        )
        return task

    @staticmethod
    def _target_from_command(command: CommandRecord, *, finished_at: datetime) -> BatchTarget:
        if command.state is CommandState.COMPLETED:
            state = BatchTargetState.COMPLETED
        elif command.state is CommandState.OFFLINE:
            state = BatchTargetState.OFFLINE
        elif command.state is CommandState.TIMEOUT:
            state = BatchTargetState.TIMEOUT
        elif command.state is CommandState.EXPIRED:
            state = BatchTargetState.EXPIRED
        elif command.state in TERMINAL_COMMAND_STATES:
            state = BatchTargetState.REJECTED
        else:
            state = BatchTargetState.DISPATCHED
        return BatchTarget(
            device_id=command.device_id,
            state=state,
            command_id=command.command_id,
            result=command.result,
            error=command.error,
            finished_at=finished_at if state is not BatchTargetState.DISPATCHED else None,
        )

    async def submit_batch(
        self,
        device_ids: list[str],
        command_type: str,
        *,
        params: dict[str, Any] | None = None,
        ttl_ms: int = MAX_COMMAND_TTL_MS,
        deadline_ms: int = 5_000,
    ) -> BatchTask:
        """Run a bounded best-effort fan-out through the same command service.

        W1 uses this for a one-device task; the implementation already keeps
        target state independent so W4 can add richer cancellation/retry policy
        without creating a second single-device path.
        """

        if (
            not isinstance(device_ids, list)
            or not device_ids
            or not all(isinstance(device_id, str) and device_id for device_id in device_ids)
            or len(set(device_ids)) != len(device_ids)
        ):
            raise ValidationError("batch device_ids must be a non-empty unique list")
        if not isinstance(deadline_ms, int) or isinstance(deadline_ms, bool) or not 0 < deadline_ms <= 60_000:
            raise ValidationError("deadline_ms must be between 1 and 60000")
        if not isinstance(ttl_ms, int) or isinstance(ttl_ms, bool) or not 0 < ttl_ms <= MAX_COMMAND_TTL_MS:
            raise ValidationError(f"ttl_ms must be between 1 and {MAX_COMMAND_TTL_MS}")
        normalized_params = self._validated_params(params)
        created_at = self._now()
        task = BatchTask(
            task_id=f"task-{uuid4()}",
            command_type=command_type,
            params=normalized_params,
            targets=[BatchTarget(device_id=device_id) for device_id in device_ids],
            created_at=created_at,
            expires_at=created_at + timedelta(milliseconds=deadline_ms),
            correlation_id=f"batch-{uuid4()}",
        )
        self.store.save_batch(task)
        self._audit(
            AuditKind.BATCH_STATE_CHANGED,
            correlation_id=task.correlation_id,
            payload={"task_id": task.task_id, "from": None, "to": task.aggregate_state.value},
        )
        self._change_batch(task, BatchState.RUNNING)
        # Mark the target as dispatching before starting the fan-out. A
        # concurrent cancellation may only cancel targets that have not
        # entered this boundary; already-dispatching commands keep reporting
        # their own real terminal state.
        for target in task.targets:
            target.state = BatchTargetState.DISPATCHED
        self.store.save_batch(task)

        async def dispatch(device_id: str) -> BatchTarget:
            try:
                command = await self.submit_command(
                    device_id,
                    command_type,
                    params=normalized_params,
                    source=CommandSource.BATCH,
                    ttl_ms=ttl_ms,
                    idempotency_key=f"{task.task_id}:{device_id}",
                    correlation_id=task.correlation_id,
                )
            except NotFoundError as exc:
                return BatchTarget(
                    device_id=device_id,
                    state=BatchTargetState.OFFLINE,
                    error={"code": "offline", "reason": str(exc)},
                    finished_at=self._now(),
                )
            except ValidationError as exc:
                return BatchTarget(
                    device_id=device_id,
                    state=BatchTargetState.REJECTED,
                    error={"code": "validation_error", "reason": str(exc)},
                    finished_at=self._now(),
                )
            return self._target_from_command(command, finished_at=self._now())

        results = await asyncio.gather(*(dispatch(device_id) for device_id in device_ids))
        task.targets = list(results)
        persisted_task = self.store.get_batch(task.task_id)
        if persisted_task is not None and persisted_task.aggregate_state is BatchState.CANCELLED:
            # Cancellation never rewrites an already dispatched target into a
            # fictional rollback. Preserve terminal command evidence while the
            # aggregate remains cancelled.
            persisted_by_device = {target.device_id: target for target in persisted_task.targets}
            for result in results:
                existing = persisted_by_device.get(result.device_id)
                if existing is None or existing.state is not BatchTargetState.CANCELLED:
                    persisted_by_device[result.device_id] = result
            persisted_task.targets = [persisted_by_device[device_id] for device_id in device_ids]
            self.store.save_batch(persisted_task)
            return persisted_task
        states = [target.state for target in task.targets]
        completed = sum(state is BatchTargetState.COMPLETED for state in states)
        failures = sum(
            state
            in {
                BatchTargetState.REJECTED,
                BatchTargetState.OFFLINE,
                BatchTargetState.TIMEOUT,
                BatchTargetState.EXPIRED,
                BatchTargetState.CANCELLED,
            }
            for state in states
        )
        if completed == len(states):
            aggregate = BatchState.COMPLETED
        elif completed and failures:
            aggregate = BatchState.PARTIAL
        elif failures == len(states):
            aggregate = BatchState.FAILED
        else:
            aggregate = BatchState.RUNNING
        if aggregate is not BatchState.RUNNING:
            self._change_batch(task, aggregate)
        else:
            self.store.save_batch(task)
        return self.store.get_batch(task.task_id) or task

    def cancel_batch(self, task_id: str) -> BatchTask:
        """Cancel only work that has not crossed the per-target dispatch boundary."""

        task = self.get_batch(task_id)
        if task.aggregate_state in TERMINAL_BATCH_STATES:
            return task
        for target in task.targets:
            if target.state is BatchTargetState.PENDING:
                target.state = BatchTargetState.CANCELLED
                target.error = {"code": "cancelled", "reason": "batch_cancelled"}
                target.finished_at = self._now()
        self._change_batch(task, BatchState.CANCELLED)
        return self.store.get_batch(task_id) or task

    def get_command(self, command_id: str) -> CommandRecord:
        command = self.store.get_command(command_id)
        if command is None:
            raise NotFoundError(f"command not found: {command_id}")
        return command

    def get_batch(self, task_id: str) -> BatchTask:
        task = self.store.get_batch(task_id)
        if task is None:
            raise NotFoundError(f"batch task not found: {task_id}")
        return task

    def get_session(self, session_id: str) -> DeviceSession:
        return self.sessions.get(session_id)

    def active_session_for_device(self, device_id: str) -> DeviceSession | None:
        return self._active_session(device_id)

    def latest_session_for_device(self, device_id: str) -> DeviceSession | None:
        active = self._active_session(device_id)
        if active is not None:
            return active
        sessions = self.store.list_sessions(device_id)
        if not sessions:
            return None
        return max(sessions, key=lambda session: session.connected_at)

    def latest_health_for_device(self, device_id: str) -> dict[str, Any] | None:
        health = self._latest_health.get(device_id)
        return dict(health) if health is not None else None

    def latest_camera_frame(self, device_id: str, *, session_id: str | None = None) -> CameraFrame | None:
        return self.camera_frames.latest(device_id, session_id=session_id)

    async def wait_for_camera_frame(
        self,
        device_id: str,
        *,
        session_id: str,
        after_frame_id: str | None = None,
        timeout_s: float = 3.0,
    ) -> CameraFrame | None:
        return await self.camera_frames.wait_for(
            device_id,
            session_id=session_id,
            after_frame_id=after_frame_id,
            timeout_s=timeout_s,
        )

    def camera_preview_session(self, device_id: str) -> DeviceSession:
        if not self.feature_gates.get("media", False):
            raise CapabilityUnavailable("feature_gate_disabled", "media")
        session = self._active_session(device_id)
        if session is None:
            raise ConflictError("camera preview requires an online session")
        if "camera" not in session.capabilities:
            raise CapabilityUnavailable("device_not_declared", "camera")
        if self._active_camera_previews.get(device_id) != session.session_id:
            raise ConflictError("camera preview has not been started")
        return session

    async def _send_host_heartbeat(
        self,
        device_id: str,
        session_id: str,
        *,
        media_enabled: bool,
    ) -> bool:
        """Send the only host heartbeat stream for one device session."""

        async with self._lock_for(device_id):
            session = self._active_session(device_id)
            transport = self._transports.get(session_id)
            if session is None or transport is None or session.session_id != session_id:
                return False
            epoch = self._session_epoch(session)
            if not self._session_write_is_current(session, epoch):
                return False
            envelope = {
                "schema": "lifeos.v1",
                "kind": "event",
                "type": "host.heartbeat",
                "event_id": f"bridge-heartbeat-{uuid4()}",
                "device_id": device_id,
                "seq": self.sessions.next_tx_sequence(session),
                "ts_ms": self._clock_ms(),
                "payload": {"media_enabled": bool(media_enabled)},
            }
            try:
                await transport.send(envelope)
            except (ProtocolError, TransportError, ConnectionError, OSError) as exc:
                await self._fail_transport(session, str(exc))
                return False
            self._last_host_heartbeat_at[session.session_id] = asyncio.get_running_loop().time()
            return True

    async def _send_camera_host_heartbeat(self, device_id: str, session_id: str) -> bool:
        """Refresh the device-side link watchdog during a camera preview."""

        if not self.feature_gates.get("media", False):
            return False
        return await self._send_host_heartbeat(
            device_id,
            session_id,
            media_enabled=True,
        )

    async def _camera_host_heartbeat_loop(self, device_id: str, session_id: str) -> None:
        """Keep the firmware's 1.5 s host-loss watchdog satisfied while streaming."""

        try:
            while self._active_camera_previews.get(device_id) == session_id:
                if not await self._send_camera_host_heartbeat(device_id, session_id):
                    return
                await asyncio.sleep(CAMERA_HOST_HEARTBEAT_INTERVAL_S)
        except asyncio.CancelledError:
            raise
        except (ProtocolError, TransportError, ConnectionError, OSError):
            session = self._active_session(device_id)
            if session is not None and session.session_id == session_id:
                self._mark_session_offline(session, reason="camera_heartbeat_failed")
        finally:
            current = asyncio.current_task()
            if self._camera_heartbeat_tasks.get(device_id) is current:
                self._camera_heartbeat_tasks.pop(device_id, None)
                self._deactivate_camera_preview(device_id, session_id)

    async def _start_camera_host_heartbeat(self, device_id: str, session_id: str) -> None:
        previous = self._camera_heartbeat_tasks.pop(device_id, None)
        if previous is not None:
            previous.cancel()
            await asyncio.gather(previous, return_exceptions=True)
        self._camera_heartbeat_tasks[device_id] = asyncio.create_task(
            self._camera_host_heartbeat_loop(device_id, session_id)
        )

    async def _stop_camera_host_heartbeat(self, device_id: str) -> None:
        task = self._camera_heartbeat_tasks.pop(device_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _manual_lease_is_active(self, device_id: str) -> bool:
        """Return whether this device currently owns an unreleased manual lease."""

        self.reap_control_leases()
        session = self._active_session(device_id)
        return bool(session is not None and session.active_control_lease)

    async def _wait_for_terminal_command(
        self,
        command_id: str,
        *,
        timeout_s: float,
    ) -> CommandRecord:
        """Boundedly wait for the device ACK that settles a mode transition."""

        deadline = asyncio.get_running_loop().time() + timeout_s
        command = self.get_command(command_id)
        while command.state not in TERMINAL_COMMAND_STATES:
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.02)
            command = self.get_command(command_id)
        return command

    async def await_control_input_ack(
        self,
        command_id: str,
        *,
        lease_id: str,
        connection_id: str,
    ) -> CommandRecord:
        """Wait for a manual-input ACK before admitting another browser frame.

        USB Serial/JTAG can deliver a browser's independent WebSocket writes in
        a short device-side burst. The firmware deliberately rejects such a
        burst to preserve its dead-man sequence. Treat an absent ACK as a safe
        stop condition instead of letting the browser enqueue more frames.
        """

        command = await self._wait_for_terminal_command(
            command_id,
            timeout_s=MANUAL_CONTROL_ACK_WAIT_S,
        )
        if command.state in TERMINAL_COMMAND_STATES:
            if command.state is not CommandState.COMPLETED:
                try:
                    lease = self.leases.release(lease_id, connection_id, reason="device_ack_failed")
                except (ConflictError, NotFoundError):
                    return command
                self._audit_lease(lease)
            return command
        command = self._change_command(
            command,
            CommandState.TIMEOUT,
            error={"code": "timeout", "reason": "manual_ack_timeout"},
        )
        try:
            lease = self.leases.release(lease_id, connection_id, reason="device_ack_timeout")
        except (ConflictError, NotFoundError):
            return command
        self._audit_lease(lease)
        return command

    async def _submit_camera_preview(
        self,
        device_id: str,
        action: str,
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> CommandRecord:
        if action not in {"start", "stop"}:
            raise ValidationError("camera preview action must be start or stop")
        session = self._active_session(device_id)
        if session is not None and action == "start":
            # Arm the device media gate immediately before an explicit start.
            # Ordinary session heartbeats leave it off, which clears stale
            # streams after a Bridge restart.
            if not await self._send_host_heartbeat(device_id, session.session_id, media_enabled=True):
                raise ConflictError("camera preview requires an online session")
        command = await self.submit_command(
            device_id,
            f"camera.preview.{action}",
            params={},
            ttl_ms=(CAMERA_PREVIEW_COMMAND_TTL_MS if action == "start" else MAX_COMMAND_TTL_MS),
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
        )
        failure_states = {
            CommandState.REJECTED,
            CommandState.SAFETY_BLOCKED,
            CommandState.OFFLINE,
            CommandState.TIMEOUT,
            CommandState.EXPIRED,
            CommandState.PREEMPTED,
            CommandState.CANCELLED,
        }
        if action == "start":
            if command.state not in failure_states and command.session_id:
                self._active_camera_previews[device_id] = command.session_id
                await self._start_camera_host_heartbeat(device_id, command.session_id)
        elif session is not None:
            # This heartbeat is intentionally ordered after the stop command:
            # firmware accepts the explicit stop while media is enabled, then
            # this independent gate prevents a stale stream from surviving a
            # dropped ACK or a later Bridge restart.
            await self._send_host_heartbeat(device_id, session.session_id, media_enabled=False)
        return command

    async def submit_camera_preview(
        self,
        device_id: str,
        action: str,
        *,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> CommandRecord:
        """Change preview mode without allowing it to overlap manual control."""

        async with self._control_mode_lock_for(device_id):
            if action == "start" and self._manual_lease_is_active(device_id):
                raise ConflictError("release manual control before starting camera preview")
            return await self._submit_camera_preview(
                device_id,
                action,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
            )

    async def acquire_control_lease_with_camera_interlock(
        self,
        device_id: str,
        connection_id: str,
        *,
        ttl_ms: int = 400,
        max_duration_ms: int = 30_000,
    ) -> tuple[ControlLease, bool]:
        """Stop a live preview before giving a browser a continuous-control lease."""

        async with self._control_mode_lock_for(device_id):
            session = self._manual_session(device_id)
            preview_active = self._active_camera_previews.get(device_id) == session.session_id
            if preview_active:
                stop = await self._submit_camera_preview(device_id, "stop")
                settled = await self._wait_for_terminal_command(
                    stop.command_id,
                    timeout_s=CAMERA_PREVIEW_STOP_WAIT_S,
                )
                if settled.state is not CommandState.COMPLETED:
                    raise ConflictError("camera preview did not stop before manual control")
            # The stop wait can expose a link or safety change; revalidate
            # before the lease is created.
            self._manual_session(device_id)
            lease = self.acquire_control_lease(
                device_id,
                connection_id,
                ttl_ms=ttl_ms,
                max_duration_ms=max_duration_ms,
            )
            return lease, preview_active

    def _manual_session(self, device_id: str) -> DeviceSession:
        if not self.feature_gates.get("manual_control_v1", False):
            raise CapabilityUnavailable("feature_gate_disabled", "manual_control_v1")
        session = self._active_session(device_id)
        if session is None:
            raise ConflictError("manual control requires an online session")
        if "manual_control_v1" not in session.capabilities:
            raise CapabilityUnavailable("device_not_declared", "manual_control_v1")
        transport = self._transports.get(session.session_id)
        if not (
            getattr(transport, "host_test_only", False)
            or getattr(transport, "manual_control_verified", False)
        ):
            raise CapabilityUnavailable("real_transport_not_verified", "manual_control_v1")
        health = self.latest_health_for_device(device_id) or {}
        if health.get("fault") is True or health.get("paused") is True:
            raise ConflictError("manual control is blocked by device safety state")
        return session

    def acquire_control_lease(
        self,
        device_id: str,
        connection_id: str,
        *,
        ttl_ms: int = 400,
        max_duration_ms: int = 30_000,
    ) -> ControlLease:
        session = self._manual_session(device_id)
        lease = self.leases.acquire(
            session,
            connection_id,
            ttl_ms=ttl_ms,
            max_duration_ms=max_duration_ms,
            now=self._now(),
        )
        self._audit_lease(lease)
        return lease

    def renew_control_lease(
        self,
        lease_id: str,
        connection_id: str,
        *,
        ttl_ms: int = 400,
    ) -> ControlLease:
        lease = self.leases.get(lease_id)
        session = self._manual_session(lease.device_id)
        if session.session_id != lease.session_id:
            self.leases.invalidate_session(lease.session_id, reason="session_changed")
            raise ConflictError("control lease session is no longer current")
        renewed = self.leases.renew(lease_id, connection_id, ttl_ms=ttl_ms, now=self._now())
        self._audit_lease(renewed)
        return renewed

    def release_control_lease(self, lease_id: str, connection_id: str) -> ControlLease:
        lease = self.leases.release(lease_id, connection_id)
        self._audit_lease(lease)
        return lease

    def get_control_lease(self, lease_id: str) -> ControlLease:
        return self.leases.get(lease_id)

    def close_control_connection(self, connection_id: str) -> list[ControlLease]:
        """Invalidate all leases when their owning WebSocket disappears."""

        changed: list[ControlLease] = []
        for lease in self.leases.list():
            if lease.connection_id != connection_id or lease.state is not LeaseState.ACTIVE:
                continue
            changed.append(self.leases.preempt(lease.lease_id, reason="websocket_closed"))
        for lease in changed:
            self._audit_lease(lease)
        return changed

    def reap_control_leases(self, *, now: datetime | None = None) -> list[ControlLease]:
        expired = self.leases.expire(now=now or self._now())
        for lease in expired:
            self._audit_lease(lease)
        return expired

    async def submit_control_input(
        self,
        lease_id: str,
        connection_id: str,
        *,
        input_seq: int,
        action: str,
        direction: dict[str, Any] | None = None,
        ttl_ms: int = 400,
    ) -> CommandRecord:
        """Submit a normalized manual frame when the explicit gate is enabled."""

        lease = self.leases.get(lease_id)
        existing_key = f"manual:{lease_id}:{input_seq}"
        existing = self.store.get_command_by_idempotency(lease.device_id, existing_key)
        if existing is not None:
            return existing
        session = self._manual_session(lease.device_id)
        if session.session_id != lease.session_id:
            raise ConflictError("control lease session is no longer current")
        session_epoch = self._session_epoch(session)
        payload = self.leases.accept_input(
            lease_id,
            device_id=lease.device_id,
            session_id=session.session_id,
            connection_id=connection_id,
            input_seq=input_seq,
            action=action,
            direction=direction,
            ttl_ms=ttl_ms,
            now=self._now(),
        )
        command_id = f"cmd-{uuid4()}"
        issued_at_ms = self._clock_ms()
        issued_at = self._now()
        command = CommandRecord(
            command_id=command_id,
            device_id=lease.device_id,
            session_id=session.session_id,
            source=CommandSource.MANUAL,
            type="manual_control",
            wire_type="command.manual_control",
            payload=payload,
            priority=_SOURCE_PRIORITY[CommandSource.MANUAL],
            issued_at=issued_at,
            expires_at=issued_at + timedelta(milliseconds=ttl_ms),
            issued_at_ms=issued_at_ms,
            expires_at_ms=issued_at_ms + ttl_ms,
            correlation_id=command_id,
            idempotency_key=existing_key,
        )
        self.store.save_command(command)
        self._audit(
            AuditKind.COMMAND_STATE_CHANGED,
            device_id=command.device_id,
            session_id=command.session_id,
            command_id=command.command_id,
            correlation_id=command.correlation_id,
            payload={"from": None, "to": CommandState.CREATED.value, "type": command.type},
        )
        self._change_command(command, CommandState.VALIDATED)
        async with self._lock_for(command.device_id):
            latest_before_dispatch = self.store.get_command(command.command_id)
            if latest_before_dispatch is not None and latest_before_dispatch.state in TERMINAL_COMMAND_STATES:
                return latest_before_dispatch
            current = self._active_session(command.device_id)
            transport = self._transports.get(command.session_id)
            if (
                current is None
                or transport is None
                or current.session_id != command.session_id
                or not self._session_write_is_current(current, session_epoch)
            ):
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": "session_changed_before_dispatch"},
                )
            current_lease = self.leases.get(lease_id)
            lease_valid = (
                current_lease.session_id == current.session_id
                and current_lease.last_input_seq == input_seq
                and (
                    (action == "input" and current_lease.state is LeaseState.ACTIVE)
                    or (
                        action == "release"
                        and current_lease.state is LeaseState.RELEASED
                        and current_lease.reason == "input_release"
                    )
                )
            )
            if not lease_valid:
                target = (
                    CommandState.EXPIRED
                    if current_lease.state is LeaseState.EXPIRED
                    else CommandState.PREEMPTED
                )
                return self._change_command(
                    command,
                    target,
                    error={"code": target.value, "reason": "manual_lease_no_longer_current"},
                )
            if command.expires_at_ms <= self._clock_ms():
                return self._change_command(
                    command,
                    CommandState.EXPIRED,
                    error={"code": "expired", "reason": "manual_input_ttl_elapsed"},
                )
            self._change_command(command, CommandState.ROUTED)
            envelope = {
                "schema": "lifeos.v1",
                "kind": "command",
                "type": command.wire_type,
                "event_id": command.command_id,
                "correlation_id": command.correlation_id,
                "device_id": command.device_id,
                "seq": self.sessions.next_tx_sequence(current),
                "ts_ms": issued_at_ms,
                "payload": payload,
            }
            self._change_command(command, CommandState.SENT)
            try:
                await transport.send(envelope)
            except (TransportError, ConnectionError, OSError) as exc:
                await self._fail_transport(current, str(exc))
                latest = self.store.get_command(command.command_id)
                if latest is not None and latest.state in TERMINAL_COMMAND_STATES:
                    return latest
                return self._change_command(
                    command,
                    CommandState.OFFLINE,
                    error={"code": "offline", "reason": str(exc)},
                )
        updated = self.store.get_command(command.command_id)
        if updated is not None:
            if action == "release":
                self._audit_lease(lease)
            return updated
        return command
