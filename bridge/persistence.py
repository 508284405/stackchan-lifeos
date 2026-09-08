"""Small, migration-backed SQLite repositories for the Web Bridge."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Iterable

from .domain import (
    AuditRecord,
    BatchState,
    BatchTargetState,
    BatchTask,
    CommandRecord,
    CommandState,
    DeviceRecord,
    DeviceSession,
    MaintenanceTask,
    RolloutTask,
    TERMINAL_COMMAND_STATES,
    transition_batch,
    transition_command,
)


class SQLiteStore:
    """Persist bridge facts without introducing a service dependency.

    Records are stored as versioned JSON blobs so adding a domain field does not
    require duplicating every field in SQL. Indexed identity/state columns keep
    the queries needed by the first bridge milestone cheap and explicit.
    """

    LATEST_SCHEMA_VERSION = 3

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER NOT NULL)"
            )
            row = self._connection.execute("SELECT version FROM schema_version").fetchone()
            version = int(row[0]) if row else 0
            if version < 1:
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS devices (
                        device_id TEXT PRIMARY KEY,
                        lifecycle_state TEXT NOT NULL,
                        data TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        device_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        data TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS sessions_device_idx
                        ON sessions(device_id);
                    CREATE TABLE IF NOT EXISTS commands (
                        command_id TEXT PRIMARY KEY,
                        device_id TEXT NOT NULL,
                        state TEXT NOT NULL,
                        idempotency_key TEXT,
                        data TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS commands_device_idx
                        ON commands(device_id);
                    CREATE INDEX IF NOT EXISTS commands_state_idx
                        ON commands(state);
                    CREATE UNIQUE INDEX IF NOT EXISTS commands_idempotency_idx
                        ON commands(device_id, idempotency_key)
                        WHERE idempotency_key IS NOT NULL;
                    CREATE TABLE IF NOT EXISTS audits (
                        audit_id TEXT PRIMARY KEY,
                        occurred_at TEXT NOT NULL,
                        device_id TEXT,
                        command_id TEXT,
                        kind TEXT NOT NULL,
                        data TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS audits_device_time_idx
                        ON audits(device_id, occurred_at);
                    """
                )
                version = 1
            if version < 2:
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS batches (
                        task_id TEXT PRIMARY KEY,
                        aggregate_state TEXT NOT NULL,
                        data TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS batches_state_idx
                        ON batches(aggregate_state);
                    """
                )
                version = 2
            if version < 3:
                self._connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS maintenance_tasks (task_id TEXT PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS maintenance_tasks_state_idx ON maintenance_tasks(state);
                    CREATE TABLE IF NOT EXISTS rollout_tasks (task_id TEXT PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS rollout_tasks_state_idx ON rollout_tasks(state);
                    """
                )
                version = 3
            if row:
                self._connection.execute(
                    "UPDATE schema_version SET version = ?",
                    (version,),
                )
            else:
                self._connection.execute(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    (version,),
                )

    @staticmethod
    def _json(data: dict) -> str:
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )

    def save_device(self, record: DeviceRecord) -> None:
        data = self._json(record.to_dict())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO devices(device_id, lifecycle_state, data) VALUES (?, ?, ?) "
                "ON CONFLICT(device_id) DO UPDATE SET lifecycle_state=excluded.lifecycle_state, "
                "data=excluded.data",
                (record.device_id, record.lifecycle_state.value, data),
            )

    def get_device(self, device_id: str) -> DeviceRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        return DeviceRecord.from_dict(json.loads(row[0])) if row else None

    def list_devices(self) -> list[DeviceRecord]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT data FROM devices ORDER BY device_id"
            ).fetchall()
        return [DeviceRecord.from_dict(json.loads(row[0])) for row in rows]

    def save_session(self, record: DeviceSession) -> None:
        # The nonce is an in-memory handshake secret. Sessions are never
        # resumed after a bridge restart, so the persistent record deliberately
        # omits it instead of treating SQLite as a secret store.
        data = self._json(record.to_dict())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO sessions(session_id, device_id, state, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET state=excluded.state, data=excluded.data",
                (record.session_id, record.device_id, record.state.value, data),
            )

    def get_session(self, session_id: str) -> DeviceSession | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return DeviceSession.from_dict(json.loads(row[0])) if row else None

    def list_sessions(self, device_id: str | None = None) -> list[DeviceSession]:
        with self._lock:
            if device_id is None:
                rows = self._connection.execute(
                    "SELECT data FROM sessions ORDER BY session_id"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT data FROM sessions WHERE device_id = ? ORDER BY session_id",
                    (device_id,),
                ).fetchall()
        return [DeviceSession.from_dict(json.loads(row[0])) for row in rows]

    def save_command(self, record: CommandRecord) -> None:
        data = self._json(record.to_dict())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO commands(command_id, device_id, state, idempotency_key, data) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(command_id) DO UPDATE SET state=excluded.state, "
                "idempotency_key=excluded.idempotency_key, data=excluded.data",
                (
                    record.command_id,
                    record.device_id,
                    record.state.value,
                    record.idempotency_key,
                    data,
                ),
            )

    def get_command(self, command_id: str) -> CommandRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM commands WHERE command_id = ?", (command_id,)
            ).fetchone()
        return CommandRecord.from_dict(json.loads(row[0])) if row else None

    def save_batch(self, record: BatchTask) -> None:
        data = self._json(record.to_dict())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO batches(task_id, aggregate_state, data) VALUES (?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET aggregate_state=excluded.aggregate_state, "
                "data=excluded.data",
                (record.task_id, record.aggregate_state.value, data),
            )

    def get_batch(self, task_id: str) -> BatchTask | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM batches WHERE task_id = ?", (task_id,)
            ).fetchone()
        return BatchTask.from_dict(json.loads(row[0])) if row else None

    def save_maintenance_task(self, record: MaintenanceTask) -> None:
        data = self._json(record.to_dict())
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO maintenance_tasks(task_id, state, data) VALUES (?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET state=excluded.state, data=excluded.data", (record.task_id, record.state.value, data))

    def get_maintenance_task(self, task_id: str) -> MaintenanceTask | None:
        with self._lock:
            row = self._connection.execute("SELECT data FROM maintenance_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return MaintenanceTask.from_dict(json.loads(row[0])) if row else None

    def list_maintenance_tasks(self) -> list[MaintenanceTask]:
        with self._lock:
            rows = self._connection.execute("SELECT data FROM maintenance_tasks ORDER BY task_id").fetchall()
        return [MaintenanceTask.from_dict(json.loads(row[0])) for row in rows]

    def save_rollout_task(self, record: RolloutTask) -> None:
        data = self._json(record.to_dict())
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO rollout_tasks(task_id, state, data) VALUES (?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET state=excluded.state, data=excluded.data", (record.task_id, record.state.value, data))

    def get_rollout_task(self, task_id: str) -> RolloutTask | None:
        with self._lock:
            row = self._connection.execute("SELECT data FROM rollout_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return RolloutTask.from_dict(json.loads(row[0])) if row else None

    def list_rollout_tasks(self) -> list[RolloutTask]:
        with self._lock:
            rows = self._connection.execute("SELECT data FROM rollout_tasks ORDER BY task_id").fetchall()
        return [RolloutTask.from_dict(json.loads(row[0])) for row in rows]

    def list_batches(self, states: Iterable[BatchState] | None = None) -> list[BatchTask]:
        state_values = [state.value for state in states] if states is not None else []
        params: list[str] = []
        where = ""
        if state_values:
            where = " WHERE aggregate_state IN (" + ",".join("?" for _ in state_values) + ")"
            params.extend(state_values)
        with self._lock:
            rows = self._connection.execute(
                "SELECT data FROM batches" + where + " ORDER BY task_id", params
            ).fetchall()
        return [BatchTask.from_dict(json.loads(row[0])) for row in rows]

    def recover_inflight_batches(self) -> list[BatchTask]:
        records = self.list_batches(states={BatchState.PENDING, BatchState.RUNNING})
        for record in records:
            transition_batch(record.aggregate_state, BatchState.EXPIRED)
            record.aggregate_state = BatchState.EXPIRED
            for target in record.targets:
                if target.state in {BatchTargetState.PENDING, BatchTargetState.DISPATCHED}:
                    target.state = BatchTargetState.EXPIRED
            self.save_batch(record)
        return records

    def get_command_by_idempotency(
        self, device_id: str, idempotency_key: str
    ) -> CommandRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data FROM commands WHERE device_id = ? AND idempotency_key = ?",
                (device_id, idempotency_key),
            ).fetchone()
        return CommandRecord.from_dict(json.loads(row[0])) if row else None

    def list_commands(
        self,
        *,
        device_id: str | None = None,
        states: Iterable[CommandState] | None = None,
    ) -> list[CommandRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            params.append(device_id)
        state_values = [state.value for state in states] if states is not None else []
        if state_values:
            clauses.append("state IN (" + ",".join("?" for _ in state_values) + ")")
            params.extend(state_values)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT data FROM commands" + where + " ORDER BY command_id", params
            ).fetchall()
        return [CommandRecord.from_dict(json.loads(row[0])) for row in rows]

    def recover_inflight(self) -> list[CommandRecord]:
        """Expire all persisted non-terminal physical commands after restart."""

        records = self.list_commands(states=set(CommandState) - TERMINAL_COMMAND_STATES)
        recovered: list[CommandRecord] = []
        for record in records:
            transition_command(record.state, CommandState.EXPIRED)
            record.state = CommandState.EXPIRED
            record.error = {
                "code": "expired",
                "reason": "bridge_restarted",
            }
            self.save_command(record)
            recovered.append(record)
        return recovered

    def append_audit(self, record: AuditRecord) -> None:
        data = self._json(record.to_dict())
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO audits(audit_id, occurred_at, device_id, command_id, kind, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.audit_id,
                    record.occurred_at.isoformat(),
                    record.device_id,
                    record.command_id,
                    record.kind.value,
                    data,
                ),
            )

    def list_audits(
        self,
        *,
        device_id: str | None = None,
        command_id: str | None = None,
    ) -> list[AuditRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            params.append(device_id)
        if command_id is not None:
            clauses.append("command_id = ?")
            params.append(command_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                "SELECT data FROM audits" + where + " ORDER BY occurred_at, audit_id",
                params,
            ).fetchall()
        return [AuditRecord.from_dict(json.loads(row[0])) for row in rows]
