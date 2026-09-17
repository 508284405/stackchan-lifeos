# StackChan LifeOS Web Console Design

## Source of truth

- Status: Draft
- Last refreshed: 2026-08-30
- Primary product surfaces: fleet overview, device list, device console, batch tasks, audit and diagnostics
- Evidence reviewed: `docs/product-requirements.md`, `docs/architecture.md`,
  `docs/protocol.md`, `docs/security.md`, `docs/roadmap.md`, `docs/testing.md`,
  `brain/api.py`, `brain/models.py`, and `contracts/phase1/envelope.schema.json`
- Detailed system contract: `docs/rfc/0001-web-bridge-fleet-control.md`
- Delivery plan: `docs/web-bridge-implementation-plan.md`
- Current implementation/acceptance status: `docs/phase4-acceptance.md`
- Confirmed video-control, update recovery and reset requirements:
  `docs/rfc/0008-web-control-safety-and-recovery.md` (2026-09-16)

This document is the UI and product-experience source of truth for the LifeOS
Web Console. It does not replace the device wire protocol or firmware safety
contract. If a screen design conflicts with `lifeos.v1`, `SafetyGate`, or the
device-side safety loop, the screen design must change.

## Brand

- Personality: calm operations console, precise, restrained, and friendly
  enough for a desktop companion without looking like a toy remote.
- Trust signals: explicit device identity, last-seen time, command TTL, physical
  state, fault reason, and separate transport/command/execution status.
- Avoid: decorative dashboards, unexplained green indicators, hidden failures,
  optimistic “success” before device ACK, joystick-like controls without a
  dead-man state, and animation that obscures safety state.

## Product goals

- Goals:
  - Let one operator discover, name, inspect, control, maintain, and diagnose
    M5Stack/StackChan devices from an internal-network Web page.
  - Make one connected USB device fully useful while preserving an architecture
    that can later grow toward 200 registered devices and multiple sites.
  - Support individual and best-effort batch commands with per-device results.
  - Support read-only monitoring, safe controls, behavior/speech, bounded manual
    movement, maintenance, firmware operations, and later media transport.
  - Keep physical safety enforceable on the device when the browser, Bridge,
    Agent, transport, or operator input fails.
- Non-goals:
  - User accounts, administrator/operator roles, RBAC, SSO, or Web login.
  - Claiming 200-device performance before that scale is actually tested.
  - Exact cross-device motion synchronization over ordinary Web/USB/Wi-Fi links.
  - Sending raw PWM, current, GPIO, shell, URL, or arbitrary device envelopes
    from the browser.
  - Public Internet exposure in the first deployment.
- Success signals:
  - One real USB device completes discovery through command result and audit.
  - An operator can always distinguish offline, accepted, executing, completed,
    rejected, timed out, and safety-blocked states.
  - Losing focus, releasing a control, or losing the WebSocket invalidates a
    manual movement stream within its short TTL.
  - A batch operation reports partial failure instead of collapsing it into one
    misleading success/failure value.

## Personas and jobs

- Primary persona: one technically capable owner/operator on a trusted internal
  network.
- User jobs:
  - Find the correct physical device and understand whether it is safe to use.
  - Pause, resume, home, emergency-stop, speak, express, and run registered
    behaviors.
  - Temporarily steer one device while maintaining visible control ownership.
  - Select several devices and issue a best-effort discrete command.
  - Diagnose faults, calibrate supported settings, upgrade firmware, and export
    a diagnostic bundle.
  - Understand exactly which devices accepted, rejected, timed out, or were
    offline for every operation.
- Key contexts of use: desktop browser on the same private network; initially
  one USB device on the Bridge host; later multiple devices and sites through
  Edge Agents.

## Information architecture

- Primary navigation:
  - Overview
  - Devices
  - Tasks
  - Audit & diagnostics
  - System
- Core routes/screens:
  - `/`: fleet/site summary, online/offline/fault counts, active safety events,
    active batch tasks, and recent failures.
  - `/devices`: searchable, filterable device table with selection and batch
    action bar, plus a gated "scan USB devices" affordance that enumerates
    local serial ports (read-only hello probe; ports held by the Bridge are
    skipped) and lets the operator add an identified device in one click.
    Scanning resets the ESP32-S3 USB peripheral and states so before running.
  - `/devices/:device_id`: identity, capabilities, live state, safe control,
    manual control, behavior/speech, media, maintenance, telemetry, and command
    history.
  - `/tasks/:task_id`: aggregate batch state plus an immutable per-device result
    table.
  - `/audit`: command and safety-event history with correlation IDs.
  - `/system`: Bridge health, discovered transports, Edge Agents, versions,
    storage status, and accepted security limitations.
- Content hierarchy:
  1. Critical safety state and operator action.
  2. Device identity and connectivity freshness.
  3. Current behavior/command state.
  4. Control affordances.
  5. Telemetry, history, and diagnostic detail.

## Design principles

- Device truth over UI optimism: submitting a command never renders as completed
  until the target device reports completion or an explicitly documented
  accepted terminal state.
- Safety state is global: `FAULT`, `PAUSED`, and `EMERGENCY_STOP` remain visible
  across tabs and cannot be covered by media, dialogs, or companion emotion.
- One device first, fleet-ready structure: one connected device uses the same
  registry, session, task, and result models as a future fleet.
- Progressive disclosure: routine controls are prominent; calibration, network,
  reset, and firmware operations live in a maintenance section with consequence
  previews.
- Partial failure is first-class: batch progress and results remain per-device.
- No hidden control channel: all write actions produce a command record and
  correlation ID, including manual control frames and maintenance operations.
- Tradeoff: the first internal-network deployment intentionally has no Web user
  authentication. The UI must state this deployment assumption; public binding
  is out of scope.

## Visual language

- Color:
  - Neutral surfaces carry normal information.
  - Green is reserved for confirmed online/healthy/completed states.
  - Amber indicates degraded, pending, clamped, or stale state.
  - Red is reserved for fault, rejected safety action, and emergency stop.
  - Blue indicates operator-controlled or selected state, never safety.
- Typography: system sans-serif for UI; tabular numerals and monospace only for
  IDs, versions, timestamps, angles, and diagnostic fields.
- Spacing/layout rhythm: 4 px base grid; 8/12/16/24/32 px semantic spacing;
  dense tables on desktop but no compressed safety controls.
- Shape/radius/elevation: restrained 6–10 px radius; borders and spacing before
  shadows; overlays only for focused, reversible tasks.
- Motion: short state transitions only. Emergency, fault, or connection-loss
  feedback appears immediately without decorative animation.
- Imagery/iconography: simple line icons plus text labels. Color or icon alone
  never communicates safety status.

## Components

- Existing components to reuse: none; the repository has no frontend component
  system at the time of this design.
- New/changed components:
  - `SafetyBanner`, `ConnectionFreshness`, `DeviceIdentity`, `CapabilityList`
  - `DeviceStateBadge`, `CommandStateBadge`, `FaultSummary`
  - `SafeControlBar`, `DeadManControl`, `BehaviorLauncher`, `SpeechComposer`
  - `DeviceTable`, `BatchActionBar`, `BatchResultTable`
  - `TelemetryChart`, `EventTimeline`, `DiagnosticExport`
  - `MaintenanceAction`, `FirmwareRollout`, `ConsequenceDialog`
- Variants and states:
  - Every control defines loading, disabled, unavailable-capability, offline,
    rejected, safety-blocked, timed-out, accepted, and completed states.
  - `DeadManControl` additionally defines acquiring, active, expiring, released,
    focus-lost, link-lost, and preempted states.
  - Batch results never use a single tri-state badge; they show aggregate counts
    and one result per target.
- Token/component ownership: frontend owns presentation tokens; domain enums,
  safety states, command states, and capability names are generated from or
  checked against shared contracts rather than duplicated as ad hoc strings.

## Accessibility

- Target standard: WCAG 2.2 AA for the Web Console.
- Keyboard/focus behavior:
  - All non-continuous actions are keyboard accessible.
  - Focus is visible and restored after dialogs.
  - Manual motion requires an intentional held interaction; keyboard release,
    window blur, or visibility loss releases control.
  - Emergency stop has a stable keyboard-accessible target but no ambiguous
    shortcut that could fire while typing.
- Contrast/readability: state text meets AA contrast; critical states include
  label, icon, and detail instead of color alone.
- Screen-reader semantics: live regions announce connection loss, fault,
  emergency stop, and terminal command results without streaming telemetry spam.
- Reduced motion and sensory considerations: respect `prefers-reduced-motion`;
  never use flashing fault indicators.

## Responsive behavior

- Supported breakpoints/devices: desktop is primary; tablet is supported for
  monitoring and discrete actions; phone layouts support status and emergency
  actions but do not expose precision manual movement by default.
- Layout adaptations:
  - Device tables become summary rows on narrow screens.
  - Device detail tabs become ordered sections.
  - The safety banner remains sticky; dense telemetry moves below controls.
- Touch/hover differences: destructive and continuous controls never rely on
  hover; touch manual control uses press-and-hold with visible release state.

## Interaction states

- Loading: show structural placeholders and the freshness timestamp of retained
  data; never convert unknown state into healthy state.
- Empty: distinguish no registered devices, no discovered devices, and no
  devices matching filters.
- Error: state whether failure occurred in browser, Bridge, Edge transport,
  protocol validation, device safety, or hardware execution.
- Success: use `accepted` for admission and `completed` for confirmed execution;
  do not call both “success.”
- Disabled: show the reason, such as offline, unsupported capability, active
  fault, conflicting command, or maintenance mode.
- Offline/slow network: show last-seen age and invalidate continuous control.
  Discrete motion and speech commands are not queued for later replay.
- Manual direction control requires a displayed frame from the same device and
  session whose capture age is at most 500 ms. Unknown/stale video revokes the
  lease; fresh video never automatically resumes motion. Camera receipt and an
  open image connection alone do not prove that the operator sees a fresh frame.
- When the last viewer leaves, stop capture after a bounded grace period. A
  stopped preview requires an explicit start; losing control focus releases
  movement immediately without waiting for the camera grace period.
- Firmware rollout distinguishes local boot acceptance from host confirmation.
  A locally healthy new image remains installed while the Bridge is offline;
  the UI shows awaiting confirmation and the device remains stopped. A failed
  or unconfirmed target pauses unstarted rollout targets until explicit resume.
- Factory-reset consequences state that device user settings and pairing are
  cleared, while host conversations, memories and audit remain available.

## Content voice

- Tone: concise, factual, calm, and action-oriented.
- Terminology:
  - “Online” means a current, negotiated device session, not merely a detected
    USB port.
  - “Accepted” means admitted by the target gateway; it does not mean movement
    completed.
  - “Safety blocked” is not a generic error and must include the blocking state.
  - “Partial” is the normal batch outcome when targets differ.
- Microcopy rules: name the target device, consequence, timeout, and recovery
  action. Avoid “Something went wrong” when a protocol or safety reason exists.

## Implementation constraints

- Framework/styling system: React/Vite source lives in `web/src`; the committed
  build in `web/dist` is served by FastAPI. Runtime dependencies remain limited
  to React and React DOM, with no CDN assets.
- Design-token constraints: begin with a small semantic token set; do not build a
  general-purpose design system before the four primary screens exist.
- Performance constraints:
  - Subscribe only to visible/selected device summaries.
  - Coalesce high-rate telemetry separately from command and safety events.
  - The architecture targets eventual 200-device operation, but current
    acceptance is explicitly one complete device, not a simulated 200-device
    performance claim.
- Compatibility constraints:
  - Browser API is not the device protocol.
  - The Web Console cannot write arbitrary `lifeos.v1` envelopes.
  - WebSocket is for UI event delivery and manual-control freshness; USB JSONL
    remains the initial device transport.
  - No user authentication or RBAC in the internal-network version.
  - Device/Edge identity, pairing, nonce, sequence, TTL, and firmware safety are
    still mandatory and are not Web user authentication.
- Test/screenshot expectations:
  - Component and API contract tests cover every interaction state.
  - Browser end-to-end tests cover one-device discovery, control, disconnect,
    partial batch result, and emergency priority.
  - Visual regression baselines are added only after a real frontend stack and
    approved screen baseline exist.

## Open questions

- [x] Select the packaging model: React/Vite with committed static build output
  served by FastAPI; owner: Web implementation. Rebuild `web/dist` with source
  changes so deployed assets match the reviewed UI.
- [x] Select the current single-device media transport: continuous QVGA MJPEG over
  the bounded USB JSONL frame path, surfaced as HTTP MJPEG; owner: RFC 0006;
  impact: real 10 fps target, privacy, and UI playback. H.264/WebRTC/HLS, audio,
  and network media remain separate future transport decisions.
- [ ] Define measured capacity and load-test gates when more devices or credible
  simulators are available; owner: scale milestone; impact: the 200-device goal.
- [ ] Revisit Web authentication before any exposure beyond the explicitly
  trusted internal network; owner: deployment operator; impact: remote access.
