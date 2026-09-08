# AGENTS.md — StackChan LifeOS

Local-first desktop robot OS for M5Stack StackChan (ESP32-S3). Dual-brain architecture:
`firmware/` = ESP32-S3 body/reflex layer (CGraph-shaped C++17 DAG + out-of-graph safety loop);
`brain/` = host-side Python LangGraph cognitive layer (LangGraph + pydantic + FastAPI);
`bridge/` = Web Bridge host service (FastAPI, device registry/sessions/transports);
`web/` = React web console (Vite source in `web/src`, committed build output in `web/dist`
served by FastAPI — keep runtime deps limited to react/react-dom, no CDN assets);
`contracts/` = versioned JSON Schemas (sole wire-format source of truth);
`simulator/` + `tests/` = JSONL replay and acceptance harnesses; `tools/` = build/HIL scripts;
`docs/` = specs, ADRs, acceptance reports (mostly Chinese).

## Commands

```bash
make test                 # runs everything below
make brain-test           # PYTHONPATH=. python3 -m pytest brain/tests -q
make bridge-test          # pytest tests/bridge
make web-check            # vite build of web/ console (dist/ is committed, so no node is strictly required)
make firmware-test        # host cmake build of firmware/ (falls back to tools/run_firmware_tests.sh)
make phase1-acceptance    # unittest tests/phase1 + simulator replay
make schemas              # python3 tools/validate_schemas.py
make brain-demo           # .venv/bin/python -m brain.cli simulate --text "..."
```

- Python setup: `python3.12 -m venv .venv && .venv/bin/pip install -e 'brain[dev]'`.
  Always run Python with `PYTHONPATH=.` from repo root.
- ESP-IDF target build (requires `IDF_PATH` pointing at the pinned ESP-IDF 5.5.4 checkout):
  `tools/build_target.sh [production|hil]`. HIL builds additionally use
  `firmware/idf/sdkconfig.hil.defaults`.

## Coding-agent delegation

- For a coding task small enough to implement, verify, and report in one focused pass with no
  material benefit from parallel work, the current main agent implements it directly.
- For every other coding task, the current main agent creates a bounded implementation subagent
  using `gpt-5.6-luna` with `max` reasoning effort ("luna-max"), with explicit file or module
  ownership and verification expectations.
- Exception: when the current main agent is already a Luna model, it implements coding tasks
  directly and does not create another Luna subagent.

## Hard safety boundaries (do not violate in any edit)

- The LLM path (sub2api / LangGraph) must never emit PWM, GPIO, I²C, or raw servo angles.
  Models produce only high-level, TTL-bounded `CognitiveDecision`s; every layer
  (schema validator → host Policy Validator → Behavior Arbiter → device Safety Controller)
  may reject.
- Pitch is hard-limited to 5°–85°. Any safety fault outranks reflexes, user input, agent
  plans, proactive behaviors, and idle animations.
- Firmware graph nodes produce semantic behavior commands only; the actuator driver clamps
  again before touching hardware. ISR / 20–100 Hz servo control / watchdog stay outside the
  CGraph DAG.
- Maintenance/HIL-only commands are strictly gated by `LIFEOS_HIL_TEST_MODE` — never loosen
  this gate in production builds.
- Tests must use the deterministic fake provider; do not call real providers or spend quota.

## Contract rules

- `contracts/phase1/envelope.schema.json` is the sole `lifeos.v1` wire-envelope source of
  truth (matches `docs/protocol.md` and the firmware parser).
- Root `device-event` / `device-command` / `cognitive-decision` schemas are Phase 2 drafts,
  NOT accepted by Phase 1 firmware.
- `contracts/web-bridge/` is the browser-facing API contract — never send it to firmware.
- `contracts/phase1/manual-control-v1.schema.json` is a proposed payload extension only;
  capability stays disabled until RFC 0002 evidence exists.

## Gotchas

- Device wire transport is USB JSONL (`lifeos.v1` envelopes) with pairing, seq, TTL, and
  nonce rules; the WebSocket is UI/event delivery only.
- Web Console semantics: "accepted" ≠ "completed"; batch results are per-device (partial
  failure is first-class); safety state is global and never optimistic. `DESIGN.md` is the
  UI source of truth — if a screen design conflicts with `lifeos.v1` or `SafetyGate`, the
  screen changes, not the protocol.
- Phase 1 exit is BLOCKED/PARTIAL (see `docs/phase1-acceptance.md`); do not claim untested
  capabilities (touch, soak, OTA, 200-device scale) in docs or code comments.
- Related repo `/Users/wangyu/product/stackchan-person-tracker` is the Phase 1 vision baseline;
  do not overwrite it from here.

## Read before touching sensitive areas

- `docs/architecture.md` — layer boundaries and LangGraph state design
- `docs/protocol.md` + `contracts/README.md` — wire protocol ownership
- `docs/security.md` — threat model and provider config rules
- `firmware/README.md` — why firmware is CGraph-shaped, not upstream CGraph
- `docs/adr/` (0001 LangGraph, 0002 CGraph, 0003 sub2api) — architecture decisions
- Acceptance status lives in `docs/*-report.md` and `docs/phase1-acceptance.md` — update
  these when changing gated behavior.
