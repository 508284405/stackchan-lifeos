# Contract ownership

`phase1/envelope.schema.json` is the sole wire-envelope source of truth for the
Phase 1 USB JSONL link. It matches `docs/protocol.md` and the firmware parser.

The root `device-event`, `device-command`, and `cognitive-decision` schemas are
earlier host-domain/Phase 2 drafts. They are not accepted directly by Phase 1
firmware and must pass through a future Device Gateway mapper before use. Keeping
this distinction explicit prevents a second device protocol from emerging.
