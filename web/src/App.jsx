import { useEffect, useMemo, useState } from "react";
import { useI18n } from "./i18n.jsx";
import { useBridgeData } from "./useBridgeData.js";
import { DeviceControls } from "./DeviceControls.jsx";
import {
  currentSession,
  deriveAttention,
  describeEvent,
  deviceMatchesFilter,
  eventTimeString,
  formatAge,
  formatUptime,
  labelState,
} from "./lib.js";

const BATCH_COMMANDS = [
  { type: "control.status", labelKey: "tasks.command.status", capability: "status", gate: "status" },
  { type: "control.pause", labelKey: "tasks.command.pause", capability: "motion", gate: "motion" },
];
const BATCH_TERMINAL_STATES = new Set(["completed", "partial", "failed", "cancelled", "expired"]);
const KNOWN_FEATURE_GATES = [
  "control",
  "status",
  "motion",
  "safety",
  "emergency_stop",
  "manual_control_v1",
  "manual_camera_preview",
  "media",
  "behavior",
  "speech",
  "usb_add",
  "diagnostics",
  "maintenance",
  "factory_reset",
  "firmware_rollout",
];

function isGateEnabled(featureGates, gate) {
  return featureGates?.[gate] === true;
}

function batchStateLabel(state, t) {
  return t(`batch.state.${state}`, state);
}

function batchTargetDetail(target, t) {
  const reason = target?.error?.reason || target?.error?.code;
  if (reason) return t("batch.targetError", reason);
  if (target?.result?.state) return t(`command.state.${target.result.state}`);
  return "—";
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function Topbar({ transport, activeSection = "overview", onNavigate = () => {} }) {
  const { t, lang, toggleLang } = useI18n();
  const navItems = [
    ["overview", "nav.overview"],
    ["devices", "nav.devices"],
    ["tasks", "nav.tasks"],
    ["audit", "nav.audit"],
    ["system", "nav.system"],
  ];
  return (
    <header className="topbar">
      <div className="brand-lockup" aria-label="StackChan LifeOS Web Bridge">
        <span className="brand-mark" aria-hidden="true"><span></span><span></span><span></span></span>
        <div>
          <p className="eyebrow">STACKCHAN LIFEOS</p>
          <p className="brand-name">Web Bridge</p>
        </div>
      </div>
      <nav className="main-nav" aria-label="Primary navigation">
        {navItems.map(([id, labelKey]) => (
          <a
            key={id}
            className={activeSection === id ? "active" : undefined}
            href={`#${id}`}
            aria-current={activeSection === id ? "page" : undefined}
            onClick={() => onNavigate(id)}
          >
            {t(labelKey)}
          </a>
        ))}
      </nav>
      <div className="topbar-actions">
        <div className="transport-status" data-status={transport.status}>
          <span className="status-dot" aria-hidden="true"></span>
          <span id="transport-label">{t(transport.labelKey)}</span>
        </div>
        <button
          className="lang-toggle"
          type="button"
          onClick={toggleLang}
          aria-label="切换语言 / Switch language"
        >
          {t("lang.toggle")}
        </button>
      </div>
    </header>
  );
}

function OverviewSection({ devices, health, onRefresh }) {
  const { t } = useI18n();
  const registered = devices.length;
  const online = devices.filter((device) => currentSession(device)?.state === "online").length;
  const attention = devices.filter(deriveAttention).length;

  let bannerLevel = "neutral";
  let bannerLabel = "safety.label";
  let bannerDetail = "safety.waiting";
  if (health) {
    if (attention > 0) {
      bannerLevel = "warn";
      bannerLabel = "banner.attention.label";
      bannerDetail = "banner.attention.detail";
    } else if (online > 0) {
      bannerLevel = "ok";
      bannerLabel = "banner.ok.label";
      bannerDetail = "banner.ok.detail";
    } else {
      bannerLevel = "neutral";
      bannerLabel = "banner.idle.label";
      bannerDetail = "banner.idle.detail";
    }
  } else {
    bannerLevel = "neutral";
    bannerLabel = "banner.unavailable.label";
    bannerDetail = "banner.unavailable.detail";
  }

  return (
    <section id="overview" className="overview-section" aria-labelledby="overview-title">
      <div className="section-intro">
        <p className="eyebrow">{t("intro.eyebrow")}</p>
        <h1 id="overview-title">{t("overview.title")}</h1>
        <p className="intro-copy">{t("overview.copy")}</p>
        <button className="quiet-button" type="button" onClick={onRefresh}>
          <span aria-hidden="true">↻</span> {t("refresh")}
        </button>
      </div>
      <div className="safety-banner" data-level={bannerLevel} role="status" aria-live="polite">
        <span className="banner-icon" aria-hidden="true">i</span>
        <div>
          <p className="banner-label">{t(bannerLabel)}</p>
          <p>{t(bannerDetail, attention > 0 ? attention : online)}</p>
        </div>
      </div>
      <dl className="metric-row" aria-label="Fleet summary">
        <div className="metric-block">
          <dt>{t("metric.registered")}</dt>
          <dd>{registered}</dd>
          <p>{t("metric.registered.hint")}</p>
        </div>
        <div className="metric-block">
          <dt>{t("metric.online")}</dt>
          <dd>{online}</dd>
          <p>{t("metric.online.hint")}</p>
        </div>
        <div className="metric-block">
          <dt>{t("metric.attention")}</dt>
          <dd>{attention}</dd>
          <p>{t("metric.attention.hint")}</p>
        </div>
      </dl>
    </section>
  );
}

function DeviceList({ devices, filter, selectedId, selectedIds = [], onSelect, onToggle = () => {} }) {
  const { t } = useI18n();
  const trimmed = filter.trim().toLowerCase();
  const visible = devices.filter((device) => deviceMatchesFilter(device, trimmed));

  const emptyText = devices.length ? t("devices.emptyFiltered") : t("devices.empty");

  return (
    <aside className="device-list-panel" aria-label="Registered devices">
      <div className="list-caption">
        <span>{t("devices.count", visible.length)}</span>
        <span>{t("list.snapshot")}</span>
      </div>
      <div className="device-list" role="listbox" aria-label="Registered devices">
        {visible.length === 0 && <div className="empty-state">{emptyText}</div>}
        {visible.map((device) => {
          const sessionState = currentSession(device)?.state || "offline";
          return (
            <div
              key={device.device_id}
              className="device-row"
              data-state={sessionState}
              role="option"
              aria-selected={String(device.device_id === selectedId)}
            >
              <label className="device-select-control">
                <input
                  type="checkbox"
                  checked={selectedIds.includes(device.device_id)}
                  onChange={() => onToggle(device.device_id)}
                  aria-label={t("batch.selectDevice", device.display_name || device.device_id)}
                />
                <span className="sr-only">{t("batch.select")}</span>
              </label>
              <button
                type="button"
                className="device-row-main"
                aria-label={device.display_name || device.device_id}
                onClick={() => onSelect(device.device_id)}
              >
                <span className="row-dot" aria-hidden="true"></span>
                <span>
                  <span className="row-name">{device.display_name || device.device_id}</span>
                  <span className="row-id">{device.device_id}</span>
                </span>
              </button>
              <span className="row-state">{labelState(sessionState, t)}</span>
            </div>
          );
        })}
      </div>
    </aside>
  );
}

function DiagnosticExport({ device, capabilities, featureGates }) {
  const { t } = useI18n();
  const [state, setState] = useState("idle");
  const [message, setMessage] = useState(null);
  const hasDiagnosticCapability = capabilities.has("diagnostics") || capabilities.has("health") || capabilities.has("status");
  const gateEnabled = featureGates?.diagnostics !== false;
  const canExport = gateEnabled && hasDiagnosticCapability;

  const exportBundle = async () => {
    if (!canExport || state === "exporting") return;
    setState("exporting");
    setMessage(null);
    try {
      const response = await fetch(`/api/v1/devices/${encodeURIComponent(device.device_id)}/diagnostics`, {
        cache: "no-store",
      });
      const data = await readJson(response);
      if (!response.ok || !data.bundle_id) throw new Error(data?.detail || t("diagnostics.failed"));
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `lifeos-diagnostics-${device.device_id}-${Date.now()}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setState("done");
      setMessage(t("diagnostics.downloaded"));
    } catch (error) {
      setState("error");
      setMessage(error.message || t("diagnostics.failed"));
    }
  };

  const disabledReason = !gateEnabled
    ? t("diagnostics.gateDisabled")
    : !hasDiagnosticCapability
      ? t("diagnostics.capabilityMissing")
      : null;

  return (
    <section className="detail-section diagnostics-section" aria-labelledby="diagnostics-heading">
      <div className="detail-section-heading">
        <div>
          <h4 id="diagnostics-heading">{t("diagnostics.heading")}</h4>
          <p className="detail-section-copy">{t("diagnostics.copy")}</p>
        </div>
        <button
          className="quiet-button"
          type="button"
          disabled={!canExport || state === "exporting"}
          onClick={exportBundle}
          title={disabledReason || undefined}
        >
          {state === "exporting" ? t("diagnostics.exporting") : t("diagnostics.export")}
        </button>
      </div>
      <p className="diagnostics-status" data-state={canExport ? state : "disabled"} role={message ? "status" : undefined}>
        {message || disabledReason || t("diagnostics.copy")}
      </p>
    </section>
  );
}

function Inspector({ device, featureGates, onChanged }) {
  const { t } = useI18n();
  if (!device) {
    return (
      <div className="inspector-empty">
        <span className="empty-glyph" aria-hidden="true">+</span>
        <h3>{t("inspector.select.title")}</h3>
        <p>{t("inspector.select.copy")}</p>
      </div>
    );
  }

  const session = currentSession(device);
  const health = device.health || {};
  const sessionState = session?.state || "offline";
  const faults = Array.isArray(health.faults) ? health.faults : [];
  const hasFault = health.fault === true || faults.length > 0;
  const capabilities = [...(session?.capabilities || device.capabilities || [])].sort();
  const heartbeatIso = session?.last_heartbeat_at || device.last_seen_at;

  return (
    <div className="inspector-content">
      <div className="inspector-header">
        <div>
          <p className="eyebrow">{t("detail.eyebrow")}</p>
          <h3 id="inspector-title">{device.display_name || device.device_id}</h3>
          <p className="mono muted">
            {device.hardware_id} · {device.protocol_version || t("protocol.unknown")}
          </p>
        </div>
        <span className="state-badge" data-state={sessionState}>{labelState(sessionState, t)}</span>
      </div>
      <div className="detail-columns">
        <section className="detail-section" aria-labelledby="session-heading">
          <div className="detail-section-heading">
            <h4 id="session-heading">{t("session.heading")}</h4>
            <span className="freshness" data-state={sessionState === "online" ? "fresh" : "stale"}>
              {sessionState === "online" ? t("freshness.fresh") : t("freshness.stale")}
            </span>
          </div>
          <dl className="detail-list">
            <div><dt>{t("session.id")}</dt><dd className="mono">{session?.session_id || t("session.none")}</dd></div>
            <div><dt>{t("session.transport")}</dt><dd>{session?.transport_id || device.transport_hint || "—"}</dd></div>
            <div><dt>{t("session.heartbeat")}</dt><dd>{formatAge(heartbeatIso, t)}</dd></div>
            <div><dt>{t("session.sequence")}</dt><dd className="mono">{session?.rx_seq ?? "—"} / {session?.tx_seq ?? "—"}</dd></div>
          </dl>
        </section>
        <section className="detail-section" aria-labelledby="health-heading">
          <div className="detail-section-heading">
            <h4 id="health-heading">{t("health.heading")}</h4>
            <span className="health-state" data-state={hasFault ? "danger" : health.uptime_ms ? "ok" : "unknown"}>
              {hasFault ? t("health.danger") : health.uptime_ms ? t("health.ok") : t("health.none")}
            </span>
          </div>
          <dl className="detail-list">
            <div><dt>{t("health.firmware")}</dt><dd className="mono">{health.firmware || device.firmware_version || "—"}</dd></div>
            <div>
              <dt>{t("health.motion")}</dt>
              <dd>{health.motion_enabled === true ? t("motion.on") : health.motion_enabled === false ? t("motion.off") : t("motion.unknown")}</dd>
            </div>
            <div>
              <dt>{t("health.faults")}</dt>
              <dd>{health.fault === true ? t("faults.reported") : faults.length ? t("faults.count", faults.length) : t("faults.none")}</dd>
            </div>
            <div><dt>{t("health.uptime")}</dt><dd>{formatUptime(health.uptime_ms, t)}</dd></div>
          </dl>
        </section>
      </div>
      <section className="detail-section capabilities-section" aria-labelledby="capabilities-heading">
        <div className="detail-section-heading">
          <h4 id="capabilities-heading">{t("capabilities.heading")}</h4>
          <span className="muted">{capabilities.length}</span>
        </div>
        <ul className="capability-list">
          {capabilities.map((capability) => (
            <li key={capability}>{capability}</li>
          ))}
        </ul>
      </section>
      <DiagnosticExport device={device} capabilities={new Set(capabilities)} featureGates={featureGates} />
      <div className="monitoring-note">
        <span aria-hidden="true">⌁</span>
        <p>{t("monitoring.note")}</p>
      </div>
      <DeviceControls device={device} featureGates={featureGates} onChanged={onChanged} />
    </div>
  );
}

function UsbScanPanel({ onDeviceAdded }) {
  const { t } = useI18n();
  const [status, setStatus] = useState("idle");
  const [items, setItems] = useState([]);
  const [note, setNote] = useState(null);
  const [addingPath, setAddingPath] = useState(null);
  const [addedPaths, setAddedPaths] = useState([]);

  useEffect(() => {
    scan();
    // Scan exactly once when the panel is opened.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const scan = async () => {
    setStatus("scanning");
    setNote(null);
    try {
      const response = await fetch("/api/v1/usb-scan", { method: "POST", cache: "no-store" });
      if (response.status === 409) {
        setItems([]);
        setNote(t("usb.gate"));
        setStatus("done");
        return;
      }
      if (!response.ok) throw new Error("scan failed");
      setItems((await response.json()).items || []);
      setStatus("done");
    } catch {
      setItems([]);
      setNote(t("usb.failed"));
      setStatus("done");
    }
  };

  const add = async (item) => {
    setAddingPath(item.path);
    setNote(null);
    try {
      const response = await fetch("/api/v1/usb-devices", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          path: item.path,
          device_id: item.identity?.device_id,
          hardware_id: item.identity?.hardware_id,
        }),
      });
      if (!response.ok) throw new Error("add failed");
      setAddedPaths((current) => [...current, item.path]);
      onDeviceAdded();
    } catch {
      setNote(t("usb.addFailed"));
    } finally {
      setAddingPath(null);
    }
  };

  if (status === "idle") return null;

  return (
    <div className="usb-scan-panel" role="region" aria-label={t("usb.title")}>
      <div className="usb-scan-heading">
        <h3>{t("usb.title")}</h3>
        <span className="usb-scan-note">{t("usb.note")}</span>
      </div>
      {note && <p className="usb-scan-message" role="status">{note}</p>}
      {status === "done" && items.length === 0 && !note && (
        <p className="usb-scan-message">{t("usb.none")}</p>
      )}
      {items.length > 0 && (
        <ul className="usb-scan-list">
          {items.map((item) => {
            const identity = item.identity || {};
            const added = addedPaths.includes(item.path);
            return (
              <li key={item.path} className="usb-scan-item" data-state={item.state}>
                <span className="usb-item-path mono">{item.path}</span>
                <span className="usb-item-identity">
                  {[identity.device_id, identity.hardware_id, identity.board, identity.firmware]
                    .filter(Boolean)
                    .join(" · ") || "—"}
                </span>
                <span className="usb-item-state">{t(`usb.state.${item.state}`)}</span>
                {item.state === "online" && (
                  added ? (
                    <span className="usb-item-added">{t("usb.added")}</span>
                  ) : (
                    <button
                      className="quiet-button"
                      type="button"
                      disabled={addingPath !== null}
                      onClick={() => add(item)}
                    >
                      {addingPath === item.path ? t("usb.adding") : t("usb.add")}
                    </button>
                  )
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function DevicesSection({
  devices,
  featureGates,
  selectedId,
  selectedIds = [],
  onSelect,
  onToggle = () => {},
  onToggleAll = () => {},
  onClearSelection = () => {},
  onDeviceAdded,
  onChanged,
  onCreateBatch = () => {},
  batchState,
}) {
  const { t } = useI18n();
  const [filter, setFilter] = useState("");
  const [scanOpen, setScanOpen] = useState(false);
  const trimmed = filter.trim().toLowerCase();
  const visible = devices.filter((device) => deviceMatchesFilter(device, trimmed));
  const visibleIds = visible.map((device) => device.device_id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedIds.includes(id));
  const selectedDevices = devices.filter((device) => selectedIds.includes(device.device_id));
  const [batchCommand, setBatchCommand] = useState(BATCH_COMMANDS[0].type);
  const command = BATCH_COMMANDS.find((item) => item.type === batchCommand) || BATCH_COMMANDS[0];
  const controlGate = featureGates?.control === true;
  const hasCapableTarget = selectedDevices.some((device) => {
    const session = currentSession(device);
    const capabilities = session?.capabilities || device.capabilities || [];
    return capabilities.includes(command.capability);
  });
  const batchReady = selectedIds.length > 0 && controlGate && isGateEnabled(featureGates, command.gate) && hasCapableTarget;
  const batchReason = selectedIds.length === 0
    ? t("batch.none")
    : !controlGate || !isGateEnabled(featureGates, command.gate)
      ? t("batch.gate")
      : !hasCapableTarget
        ? t("batch.capability")
        : null;

  return (
    <section id="devices" className="workspace-section" aria-labelledby="devices-title">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">{t("devices.eyebrow")}</p>
          <h2 id="devices-title">{t("devices.title")}</h2>
        </div>
        <div className="workspace-heading-actions">
          <button className="quiet-button" type="button" onClick={() => setScanOpen((open) => !open)}>
            <span aria-hidden="true">⌖</span> {t("usb.scan")}
          </button>
          <label className="search-control">
            <span className="sr-only">{t("filter.label")}</span>
            <input
              type="search"
              placeholder={t("filter.placeholder")}
              autoComplete="off"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </label>
        </div>
      </div>
      {scanOpen && <UsbScanPanel onDeviceAdded={onDeviceAdded} />}
      <div className="batch-action-bar" role="region" aria-label={t("batch.action")}>
        <div className="batch-selection-summary">
          <span className="batch-count">{t("batch.selected", selectedIds.length)}</span>
          <button className="text-button" type="button" onClick={() => onToggleAll(visibleIds)} disabled={!visibleIds.length}>
            {allVisibleSelected ? t("batch.clear") : t("batch.selectAll")}
          </button>
          {selectedIds.length > 0 && !allVisibleSelected && (
            <button className="text-button" type="button" onClick={onClearSelection}>{t("batch.clear")}</button>
          )}
        </div>
        <div className="batch-action-controls">
          <label className="batch-command-select">
            <span className="sr-only">{t("batch.action")}</span>
            <select value={batchCommand} onChange={(event) => setBatchCommand(event.target.value)}>
              {BATCH_COMMANDS.map((item) => <option key={item.type} value={item.type}>{t(item.labelKey)}</option>)}
            </select>
          </label>
          <button
            className="quiet-button"
            type="button"
            disabled={!batchReady || batchState === "submitting"}
            onClick={() => onCreateBatch({
              deviceIds: selectedIds,
              commandType: command.type,
              params: {},
            })}
            title={batchReason || undefined}
          >
            {batchState === "submitting" ? t("batch.submitting") : t("batch.submit")}
          </button>
        </div>
        {batchReason && <p className="batch-message" role="status">{batchReason}</p>}
        {batchState === "error" && <p className="batch-message" role="alert">{t("batch.failed")}</p>}
      </div>
      <div className="workspace-grid">
        <DeviceList
          devices={devices}
          filter={filter}
          selectedId={selectedId}
          selectedIds={selectedIds}
          onSelect={onSelect}
          onToggle={onToggle}
        />
        <article className="inspector-panel" aria-labelledby="inspector-title">
          <Inspector
            device={devices.find((item) => item.device_id === selectedId) || null}
            featureGates={featureGates}
            onChanged={onChanged}
          />
        </article>
      </div>
    </section>
  );
}

function TasksSection({ tasks, refreshing }) {
  const { t } = useI18n();
  const ordered = [...tasks].reverse();
  return (
    <section id="tasks" className="workspace-section tasks-section" aria-labelledby="tasks-title">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">{t("tasks.eyebrow")}</p>
          <h2 id="tasks-title">{t("tasks.title")}</h2>
          <p className="muted">{t("tasks.copy")}</p>
        </div>
        {refreshing && <span className="freshness" data-state="pending">{t("tasks.refreshing")}</span>}
      </div>
      {ordered.length === 0 ? <div className="empty-state">{t("tasks.empty")}</div> : (
        <div className="task-list">
          {ordered.map((task) => (
            <article className="task-card" key={task.task_id}>
              <div className="task-card-heading">
                <div>
                  <p className="mono">{task.task_id}</p>
                  <h3>{task.command_type}</h3>
                </div>
                <span className="state-badge" data-state={task.aggregate_state}>
                  {batchStateLabel(task.aggregate_state, t)}
                </span>
              </div>
              <dl className="task-summary">
                <div><dt>{t("tasks.targets")}</dt><dd>{task.targets?.length || 0}</dd></div>
                <div><dt>{t("tasks.correlation")}</dt><dd className="mono">{task.correlation_id || "—"}</dd></div>
                <div><dt>{t("tasks.created")}</dt><dd>{eventTimeString(task.created_at)}</dd></div>
              </dl>
              <div className="task-table-wrap">
                <table className="task-table">
                  <thead><tr><th>{t("tasks.device")}</th><th>{t("tasks.state")}</th><th>{t("tasks.detail")}</th></tr></thead>
                  <tbody>
                    {(task.targets || []).map((target) => (
                      <tr key={target.device_id}>
                        <td className="mono">{target.device_id}</td>
                        <td>{batchStateLabel(target.state, t)}</td>
                        <td>{batchTargetDetail(target, t)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function AuditSection({ events }) {
  const { t } = useI18n();
  const recent = useMemo(() => [...events].reverse().slice(0, 24), [events]);

  return (
    <section id="audit" className="events-section" aria-labelledby="events-title">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">{t("events.eyebrow")}</p>
          <h2 id="events-title">{t("events.title")}</h2>
          <p className="muted">{t("events.copy")}</p>
        </div>
        <p className="muted">{t("events.retained", events.length)}</p>
      </div>
      <ol className="event-timeline" aria-live="polite">
        {recent.length === 0 && <li className="empty-state">{t("events.empty")}</li>}
        {recent.map((event, index) => (
          <li className="event-item" key={`${event.cursor}-${index}`}>
            <time className="event-time" dateTime={event.occurred_at || ""}>
              {eventTimeString(event.occurred_at)}
            </time>
            <div>
              <span className="event-type">{event.type}</span>
              <span className="event-detail">{describeEvent(event, t)}</span>
            </div>
            <span className="event-cursor">#{event.cursor}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function SystemSection({ health }) {
  const { t } = useI18n();
  const featureGates = health?.feature_gates || {};
  const loopback = health?.bind_host === "127.0.0.1" || health?.bind_host === "::1";
  return (
    <section id="system" className="workspace-section system-section" aria-labelledby="system-title">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">{t("system.eyebrow")}</p>
          <h2 id="system-title">{t("system.title")}</h2>
          <p className="muted">{t("system.copy")}</p>
        </div>
      </div>
      <div className="system-grid">
        <section className="detail-section">
          <div className="detail-section-heading"><h3>{t("system.health")}</h3></div>
          <dl className="detail-list">
            <div><dt>{t("system.health")}</dt><dd>{health?.ok === true ? t("system.health.ok") : t("system.health.unknown")}</dd></div>
            <div><dt>{t("system.service")}</dt><dd className="mono">{health?.service || "—"}</dd></div>
            <div><dt>{t("system.bind")}</dt><dd className="mono">{health?.bind_host || "—"}</dd></div>
            <div><dt>{t("system.registered")}</dt><dd>{health?.registered_devices ?? "—"}</dd></div>
            <div><dt>{t("system.online")}</dt><dd>{health?.online_devices ?? "—"}</dd></div>
            <div><dt>{t("system.origin")}</dt><dd>{health?.origin_allowlist_configured ? t("system.configured") : t("system.notConfigured")}</dd></div>
          </dl>
        </section>
        <section className="detail-section">
          <div className="detail-section-heading"><h3>{t("system.deployment")}</h3></div>
          <p>{t("system.noAuth")}</p>
          <p className="system-boundary" data-state={loopback ? "ok" : "warn"}>
            {loopback ? t("system.loopback") : t("system.trustedLan")}
          </p>
          <p className="muted">{t("system.maintenanceHidden")}</p>
        </section>
      </div>
      <section className="detail-section system-gates">
        <div className="detail-section-heading"><h3>{t("system.featureGates")}</h3></div>
        <ul className="gate-list">
          {KNOWN_FEATURE_GATES.map((gate) => {
            const value = featureGates[gate];
            return (
              <li key={gate} data-state={value === true ? "enabled" : value === false ? "disabled" : "unknown"}>
                <span>{t(`system.gate.${gate}`, gate)}</span>
                <strong>{value === true ? t("system.gate.enabled") : value === false ? t("system.gate.disabled") : t("system.gate.unknown")}</strong>
              </li>
            );
          })}
        </ul>
      </section>
    </section>
  );
}

export default function App() {
  const { t } = useI18n();
  const { devices, health, events, transport, refreshSnapshot } = useBridgeData();
  const [selectedId, setSelectedId] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [batchState, setBatchState] = useState("idle");
  const [activeSection, setActiveSection] = useState("overview");

  useEffect(() => {
    setSelectedId((current) =>
      current && devices.some((device) => device.device_id === current)
        ? current
        : devices[0]?.device_id || null,
    );
  }, [devices]);

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => devices.some((device) => device.device_id === id)));
  }, [devices]);

  const toggleSelected = (deviceId) => {
    setSelectedIds((current) => current.includes(deviceId)
      ? current.filter((id) => id !== deviceId)
      : [...current, deviceId]);
  };

  const toggleAll = (deviceIds) => {
    setSelectedIds((current) => deviceIds.every((id) => current.includes(id))
      ? current.filter((id) => !deviceIds.includes(id))
      : [...new Set([...current, ...deviceIds])]);
  };

  const pollBatch = async (taskId) => {
    setBatchState("refreshing");
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const response = await fetch(`/api/v1/batch-tasks/${encodeURIComponent(taskId)}`, { cache: "no-store" });
      const task = await readJson(response);
      if (!response.ok) break;
      setTasks((current) => current.map((item) => item.task_id === taskId ? task : item));
      if (BATCH_TERMINAL_STATES.has(task.aggregate_state)) break;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    setBatchState("idle");
  };

  const createBatch = async ({ deviceIds, commandType, params }) => {
    setBatchState("submitting");
    try {
      const response = await fetch("/api/v1/batch-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ device_ids: deviceIds, command_type: commandType, params }),
      });
      const task = await readJson(response);
      if (!response.ok || !task.task_id) throw new Error("batch_failed");
      setTasks((current) => [...current.filter((item) => item.task_id !== task.task_id), task]);
      setActiveSection("tasks");
      window.location.hash = "tasks";
      await pollBatch(task.task_id);
    } catch {
      setBatchState("error");
    }
  };

  return (
    <>
      <a className="skip-link" href="#main-content">{t("skip.link")}</a>
      <Topbar transport={transport} activeSection={activeSection} onNavigate={setActiveSection} />
      <main id="main-content" className="app-shell">
        <OverviewSection devices={devices} health={health} onRefresh={refreshSnapshot} />
        <DevicesSection
          devices={devices}
          featureGates={health?.feature_gates}
          selectedId={selectedId}
          selectedIds={selectedIds}
          onSelect={setSelectedId}
          onToggle={toggleSelected}
          onToggleAll={toggleAll}
          onClearSelection={() => setSelectedIds([])}
          onDeviceAdded={refreshSnapshot}
          onChanged={refreshSnapshot}
          onCreateBatch={createBatch}
          batchState={batchState}
        />
        <TasksSection tasks={tasks} refreshing={batchState === "refreshing"} />
        <AuditSection events={events} />
        <SystemSection health={health} />
      </main>
      <footer className="footer-bar">
        <span>LifeOS / Web Bridge</span>
        <span>{t("footer.local")}</span>
      </footer>
    </>
  );
}
