"""Errors exposed by the Web Bridge domain boundary."""


class BridgeError(Exception):
    """Base class for expected bridge failures."""


class NotFoundError(BridgeError):
    """The requested bridge resource does not exist."""


class ConflictError(BridgeError):
    """The request conflicts with an existing resource or state."""


class CapabilityUnavailable(ConflictError):
    """A feature or negotiated device capability is not available."""

    code = "capability_unavailable"

    def __init__(self, reason: str, required_capability: str) -> None:
        self.reason = reason
        self.required_capability = required_capability
        super().__init__(f"{required_capability} unavailable: {reason}")


class ValidationError(BridgeError):
    """The request or wire frame is malformed."""


class TransportError(BridgeError):
    """A transport could not deliver a frame."""


class ProtocolError(BridgeError):
    """A device frame violates the lifeos.v1 contract."""
