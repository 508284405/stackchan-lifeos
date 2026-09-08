"""Per-device session negotiation and sequence/freshness bookkeeping."""

from __future__ import annotations

import secrets
from datetime import datetime
from uuid import uuid4

from .domain import DeviceSession, SessionState, transition_session, utc_now
from .errors import ProtocolError
from .persistence import SQLiteStore


class SessionManager:
    """Own independent session counters and duplicate windows per device."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store
        self._sessions: dict[str, DeviceSession] = {}

    def create(
        self,
        *,
        device_id: str,
        transport_id: str,
        edge_id: str = "local",
        connected_at: datetime | None = None,
    ) -> DeviceSession:
        session = DeviceSession(
            session_id=str(uuid4()),
            device_id=device_id,
            edge_id=edge_id,
            transport_id=transport_id,
            nonce=secrets.token_urlsafe(24),
            connected_at=connected_at or utc_now(),
        )
        self._sessions[session.session_id] = session
        self.store.save_session(session)
        return session

    def get(self, session_id: str) -> DeviceSession:
        cached = self._sessions.get(session_id)
        if cached is not None:
            return cached
        session = self.store.get_session(session_id)
        if session is None:
            raise ProtocolError(f"unknown session: {session_id}")
        self._sessions[session_id] = session
        return session

    def save(self, session: DeviceSession) -> None:
        self._sessions[session.session_id] = session
        self.store.save_session(session)

    def change_state(self, session: DeviceSession, target: SessionState) -> DeviceSession:
        transition_session(session.state, target)
        session.state = target
        self.save(session)
        return session

    def accept_rx_sequence(self, session: DeviceSession, seq: int) -> bool:
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            return False
        if session.rx_seq is not None and seq != session.rx_seq + 1:
            return False
        session.rx_seq = seq
        self.save(session)
        return True

    def resync_media_rx_sequence(self, session: DeviceSession, seq: int) -> int | None:
        """Advance only a media receive window after a dropped bounded frame.

        Command, ACK, error, health, and hello envelopes remain strictly
        ordered. Camera data is explicitly lossy and cannot keep the whole
        device session offline after one USB partial write.
        """

        if (
            not isinstance(seq, int)
            or isinstance(seq, bool)
            or seq < 0
            or session.rx_seq is None
            or seq <= session.rx_seq + 1
        ):
            return None
        previous = session.rx_seq
        session.rx_seq = seq
        self.save(session)
        return previous

    def next_tx_sequence(self, session: DeviceSession) -> int:
        session.tx_seq += 1
        self.save(session)
        return session.tx_seq

    def remember_event(self, session: DeviceSession, event_id: str) -> None:
        if event_id not in session.seen_event_ids:
            session.seen_event_ids.append(event_id)
            del session.seen_event_ids[:-16]
            self.save(session)

    @staticmethod
    def is_duplicate(session: DeviceSession, event_id: str) -> bool:
        return event_id in session.seen_event_ids

    def heartbeat(self, session: DeviceSession, at: datetime) -> None:
        session.last_heartbeat_at = at
        if session.state == SessionState.DEGRADED:
            transition_session(session.state, SessionState.ONLINE)
            session.state = SessionState.ONLINE
        self.save(session)

    def mark_offline(self, session: DeviceSession) -> None:
        if session.state != SessionState.OFFLINE:
            transition_session(session.state, SessionState.OFFLINE)
            session.state = SessionState.OFFLINE
            self.save(session)
