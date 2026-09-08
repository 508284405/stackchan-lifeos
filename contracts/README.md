# Contract ownership

`phase1/envelope.schema.json` is the sole wire-envelope source of truth for the
Phase 1 USB JSONL link. It matches `docs/protocol.md` and the firmware parser.

The root `device-event`, `device-command`, and `cognitive-decision` schemas are
earlier host-domain/Phase 2 drafts. They are not accepted directly by Phase 1
firmware and must pass through a future Device Gateway mapper before use. Keeping
this distinction explicit prevents a second device protocol from emerging.

`web-bridge/` contains the browser-facing OpenAPI contract. It is a separate
host API contract and must never be sent to the firmware as a `lifeos.v1`
envelope.

`phase1/manual-control-v1.schema.json` is a proposed payload extension only.
It does not enable the capability; production and HIL gates remain closed until
the firmware/parser/replay/HIL evidence required by RFC 0002 exists.

`phase1/camera-preview-v1.schema.json` defines the bounded camera preview
command and frame event payloads. It does not authorize recording, raw media
storage, or high-rate network video.
