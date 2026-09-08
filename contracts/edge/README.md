# `lifeos.edge.v1` contract

`envelope.schema.json` is the versioned control-plane ↔ Edge envelope. It is not the
browser DTO and it is not the device `lifeos.v1` envelope. The examples are deliberately
small positive/negative fixtures used by `tests/bridge/test_edge.py`.

The contract requires an explicit sender identity and binds every frame to one
`edge_id`, `device_id`, and `session_id`. Control-plane and Edge sequence spaces are
independent and both start at `0` for each new session. `nonce` is hello-only runtime
material; implementations must not log or export it.
