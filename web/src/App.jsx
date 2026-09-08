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

function Topbar({ transport }) {
  const { t, lang, toggleLang } = useI18n();
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
        <a className="active" href="#overview">{t("nav.overview")}</a>
        <a href="#devices">{t("nav.devices")}</a>
        <a href="#events">{t("nav.events")}</a>
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

function DeviceList({ devices, filter, selectedId, onSelect }) {
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
            <button
              key={device.device_id}
              type="button"
              className="device-row"
              data-state={sessionState}
              role="option"
              aria-selected={String(device.device_id === selectedId)}
              onClick={() => onSelect(device.device_id)}
            >
              <span className="row-dot" aria-hidden="true"></span>
              <span>
                <span className="row-name">{device.display_name || device.device_id}</span>
                <span className="row-id">{device.device_id}</span>
              </span>
              <span className="row-state">{labelState(sessionState, t)}</span>
            </button>
          );
        })}
      </div>
    </aside>
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

function DevicesSection({ devices, featureGates, selectedId, onSelect, onDeviceAdded, onChanged }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState("");
  const [scanOpen, setScanOpen] = useState(false);

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
      <div className="workspace-grid">
        <DeviceList devices={devices} filter={filter} selectedId={selectedId} onSelect={onSelect} />
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

function EventsSection({ events }) {
  const { t } = useI18n();
  const recent = useMemo(() => [...events].reverse().slice(0, 24), [events]);

  return (
    <section id="events" className="events-section" aria-labelledby="events-title">
      <div className="workspace-heading">
        <div>
          <p className="eyebrow">{t("events.eyebrow")}</p>
          <h2 id="events-title">{t("events.title")}</h2>
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

export default function App() {
  const { t } = useI18n();
  const { devices, health, events, transport, refreshSnapshot } = useBridgeData();
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    setSelectedId((current) =>
      current && devices.some((device) => device.device_id === current)
        ? current
        : devices[0]?.device_id || null,
    );
  }, [devices]);

  return (
    <>
      <a className="skip-link" href="#main-content">{t("skip.link")}</a>
      <Topbar transport={transport} />
      <main id="main-content" className="app-shell">
        <OverviewSection devices={devices} health={health} onRefresh={refreshSnapshot} />
        <DevicesSection
          devices={devices}
          featureGates={health?.feature_gates}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onDeviceAdded={refreshSnapshot}
          onChanged={refreshSnapshot}
        />
        <EventsSection events={events} />
      </main>
      <footer className="footer-bar">
        <span>LifeOS / Web Bridge</span>
        <span>{t("footer.local")}</span>
      </footer>
    </>
  );
}
