import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "./i18n.jsx";
import { currentSession } from "./lib.js";

const FAILURE_STATES = new Set([
  "rejected",
  "safety_blocked",
  "offline",
  "timeout",
  "expired",
  "preempted",
  "cancelled",
]);

function commandResultText(command, t) {
  if (!command) return null;
  if (command.state) {
    const stateKey = `command.state.${command.state}`;
    const state = t(stateKey);
    const reason = command.error?.reason || command.error?.code;
    return reason ? `${state} · ${reason}` : state;
  }
  return command.detail?.error?.reason || command.detail || t("command.failed");
}

async function parseResponse(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function settleCommand(data) {
  if (!data?.command_id || !data.state || FAILURE_STATES.has(data.state) || data.state === "completed") {
    return data;
  }
  let latest = data;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 100));
    try {
      const response = await fetch(`/api/v1/commands/${encodeURIComponent(data.command_id)}`, {
        cache: "no-store",
      });
      const next = await parseResponse(response);
      if (next?.command_id) latest = next;
      if (FAILURE_STATES.has(latest.state) || latest.state === "completed" || latest.state === "accepted") {
        return latest;
      }
    } catch {
      return latest;
    }
  }
  return latest;
}

function ManualControl({ device, enabled, onCameraPreviewStopped }) {
  const { t } = useI18n();
  const socketRef = useRef(null);
  const leaseRef = useRef(null);
  const acquireRef = useRef(null);
  const sequenceRef = useRef(0);
  const timerRef = useRef(null);
  const holdingRef = useRef(null);
  const inputInFlightRef = useRef(false);
  const pendingActionRef = useRef(null);
  const releasePendingRef = useRef(false);
  const [state, setState] = useState("idle");
  const [message, setMessage] = useState(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const sendInput = useCallback((action, direction) => {
    const socket = socketRef.current;
    const lease = leaseRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN || !lease || inputInFlightRef.current) return false;
    sequenceRef.current += 1;
    inputInFlightRef.current = true;
    pendingActionRef.current = action;
    socket.send(JSON.stringify({
      type: "input",
      lease_id: lease.lease_id,
      input_seq: sequenceRef.current,
      action,
      ...(direction ? { direction } : {}),
      ttl_ms: 500,
    }));
    return true;
  }, []);

  const sendRelease = useCallback(() => {
    if (!leaseRef.current || inputInFlightRef.current) return false;
    releasePendingRef.current = false;
    setState("releasing");
    return sendInput("release");
  }, [sendInput]);

  const release = useCallback(() => {
    holdingRef.current = null;
    clearTimer();
    if (!leaseRef.current) return;
    releasePendingRef.current = true;
    if (!inputInFlightRef.current) sendRelease();
  }, [clearTimer, sendRelease]);

  const acquire = useCallback(() => {
    if (leaseRef.current) return Promise.resolve(leaseRef.current);
    if (acquireRef.current) return acquireRef.current;

    const promise = new Promise((resolve, reject) => {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/control`);
      socketRef.current = socket;
      let settled = false;

      socket.addEventListener("open", () => {
        socket.send(JSON.stringify({
          type: "lease.acquire",
          device_id: device.device_id,
          ttl_ms: 500,
          max_duration_ms: 30_000,
        }));
      });
      socket.addEventListener("message", (event) => {
        let value;
        try {
          value = JSON.parse(event.data);
        } catch {
          return;
        }
        if (value.type === "lease.acquired") {
          settled = true;
          leaseRef.current = value.lease;
          sequenceRef.current = 0;
          if (value.camera_preview_stopped === true) {
            onCameraPreviewStopped?.();
            setMessage(t("manual.cameraPaused"));
          }
          setState("ready");
          resolve(value.lease);
        } else if (value.type === "control.error") {
          inputInFlightRef.current = false;
          pendingActionRef.current = null;
          releasePendingRef.current = false;
          setMessage(value.reason || value.code || t("manual.failed"));
          if (!settled) {
            settled = true;
            reject(new Error(value.reason || value.code || "control rejected"));
          }
        } else if (value.type === "command.state.changed" && value.command?.state) {
          if (value.command.type !== "manual_control") return;
          const action = pendingActionRef.current;
          inputInFlightRef.current = false;
          pendingActionRef.current = null;
          if (value.command.state === "completed") {
            if (action === "release") {
              leaseRef.current = null;
              releasePendingRef.current = false;
              setState("ready");
            } else if (releasePendingRef.current) {
              sendRelease();
            }
          } else if (FAILURE_STATES.has(value.command.state)) {
            holdingRef.current = null;
            clearTimer();
            leaseRef.current = null;
            releasePendingRef.current = false;
            setState("error");
            setMessage(commandResultText(value.command, t));
          }
        }
      });
      socket.addEventListener("error", () => {
        if (!settled) {
          settled = true;
          reject(new Error(t("manual.failed")));
        }
        setState("error");
      });
      socket.addEventListener("close", () => {
        clearTimer();
        leaseRef.current = null;
        socketRef.current = null;
        inputInFlightRef.current = false;
        pendingActionRef.current = null;
        releasePendingRef.current = false;
        setState("idle");
        if (!settled) {
          settled = true;
          reject(new Error(t("manual.disconnected")));
        }
      });
    });
    acquireRef.current = promise.finally(() => {
      acquireRef.current = null;
    });
    return acquireRef.current;
  }, [clearTimer, device.device_id, onCameraPreviewStopped, sendRelease, t]);

  const hold = useCallback(async (direction) => {
    holdingRef.current = direction;
    setMessage(null);
    setState("connecting");
    try {
      await acquire();
      if (holdingRef.current !== direction) {
        release();
        return;
      }
      sendInput("input", direction);
      clearTimer();
      timerRef.current = window.setInterval(() => {
        if (holdingRef.current === direction) sendInput("input", direction);
      }, 120);
      setState("holding");
    } catch (error) {
      holdingRef.current = null;
      clearTimer();
      setState("error");
      setMessage(error.message || t("manual.failed"));
    }
  }, [acquire, clearTimer, release, sendInput, t]);

  useEffect(() => {
    const stop = () => release();
    window.addEventListener("blur", stop);
    document.addEventListener("visibilitychange", stop);
    return () => {
      window.removeEventListener("blur", stop);
      document.removeEventListener("visibilitychange", stop);
      release();
      if (socketRef.current) socketRef.current.close();
    };
  }, [release]);

  const button = (label, direction) => (
    <button
      className="manual-pad-button"
      type="button"
      disabled={!enabled}
      aria-label={label}
      onPointerDown={(event) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        hold(direction);
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
      onKeyDown={(event) => {
        if ((event.key === "Enter" || event.key === " ") && !event.repeat) {
          event.preventDefault();
          hold(direction);
        }
      }}
      onKeyUp={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          release();
        }
      }}
      onBlur={release}
    >
      <span className="manual-pad-arrow" aria-hidden="true">
        {direction.yaw < 0 ? "←" : direction.yaw > 0 ? "→" : direction.pitch < 0 ? "↑" : "↓"}
      </span>
      <span className="sr-only">{label}</span>
    </button>
  );

  return (
    <section className="control-section manual-section" aria-labelledby="manual-heading" aria-disabled={!enabled}>
      <div className="control-heading">
        <div>
          <h4 id="manual-heading">{t("manual.heading")}</h4>
          <p>{t("manual.copy")}</p>
        </div>
        <span className="control-state" data-state={enabled ? state : "disabled"}>{t(enabled ? `manual.state.${state}` : "manual.state.disabled")}</span>
      </div>
      <div className="manual-pad" aria-label={t("manual.pad")}>
        <span></span>
        {button(t("manual.up"), { yaw: 0, pitch: -1 })}
        <span></span>
        {button(t("manual.left"), { yaw: -1, pitch: 0 })}
        <span className="manual-pad-center" aria-hidden="true">·</span>
        {button(t("manual.right"), { yaw: 1, pitch: 0 })}
        <span></span>
        {button(t("manual.down"), { yaw: 0, pitch: 1 })}
        <span></span>
      </div>
      {message && <p className="control-message" role="status">{message}</p>}
    </section>
  );
}

function CameraPreview({ device, mediaEnabled = false, manualPreviewStopVersion = 0 }) {
  const { t } = useI18n();
  const [active, setActive] = useState(false);
  const [state, setState] = useState("idle");
  const [message, setMessage] = useState(null);
  const [streamKey, setStreamKey] = useState(0);
  const activeRef = useRef(false);
  const manualPreviewStopVersionRef = useRef(manualPreviewStopVersion);
  const session = currentSession(device);
  const capabilities = new Set(session?.capabilities || device?.capabilities || []);
  const online = session?.state === "online" || session?.state === "degraded";
  const canPreview = mediaEnabled && capabilities.has("camera") && online;

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => {
    if (active && (!canPreview || !session?.session_id)) {
      setActive(false);
      setState("idle");
      setMessage(null);
    }
  }, [active, canPreview, session?.session_id]);

  useEffect(() => {
    setActive(false);
    setState("idle");
    setMessage(null);
    setStreamKey(0);
  }, [device?.device_id]);

  useEffect(() => {
    if (manualPreviewStopVersionRef.current === manualPreviewStopVersion) return;
    manualPreviewStopVersionRef.current = manualPreviewStopVersion;
    setActive(false);
    setState("idle");
    setMessage(null);
    setStreamKey((value) => value + 1);
  }, [manualPreviewStopVersion]);

  useEffect(() => () => {
    if (activeRef.current) {
      fetch(`/api/v1/devices/${encodeURIComponent(device.device_id)}/camera-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        body: JSON.stringify({ action: "stop" }),
      }).catch(() => {});
    }
  }, [device.device_id]);

  const toggle = async () => {
    const action = active ? "stop" : "start";
    setState("requesting");
    setMessage(null);
    try {
      const response = await fetch(`/api/v1/devices/${encodeURIComponent(device.device_id)}/camera-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ action }),
      });
      const data = await settleCommand(await parseResponse(response));
      if (!response.ok || FAILURE_STATES.has(data.state)) {
        throw new Error(commandResultText(data, t) || t("camera.failed"));
      }
      if (action === "start") {
        setActive(true);
        setStreamKey((value) => value + 1);
        setState("waiting");
      } else {
        setActive(false);
        setState("idle");
      }
    } catch (error) {
      setState("error");
      setMessage(error.message || t("camera.failed"));
    }
  };

  const streamUrl = active
    ? `/api/v1/devices/${encodeURIComponent(device.device_id)}/camera/stream?session=${encodeURIComponent(session?.session_id || "")}&v=${streamKey}`
    : null;

  return (
    <section className="control-section camera-section" aria-labelledby="camera-heading">
      <div className="control-heading">
        <div>
          <h4 id="camera-heading">{t("camera.heading")}</h4>
          <p>{t("camera.copy")}</p>
        </div>
        <span className="control-state" data-state={state}>{t(`camera.state.${state}`)}</span>
      </div>
      <div className="camera-frame" data-state={state}>
        {active && streamUrl ? (
          <img
            src={streamUrl}
            alt={t("camera.alt", device.display_name || device.device_id)}
            onLoad={() => setState("live")}
            onError={() => setState("stale")}
          />
        ) : (
          <div className="camera-placeholder">
            <span aria-hidden="true">◌</span>
            <p>{canPreview ? t("camera.placeholder") : mediaEnabled ? t("camera.unavailable") : t("camera.gate")}</p>
          </div>
        )}
      </div>
      <div className="camera-actions">
        <button className="quiet-button" type="button" disabled={!canPreview || state === "requesting"} onClick={toggle}>
          {state === "requesting" ? t("camera.requesting") : active ? t("camera.stop") : t("camera.start")}
        </button>
        <span className="camera-spec">{t("camera.spec")}</span>
      </div>
      {message && <p className="control-message" role="status">{message}</p>}
    </section>
  );
}

export function DeviceControls({ device, featureGates, onChanged }) {
  const { t } = useI18n();
  const [pending, setPending] = useState(null);
  const [message, setMessage] = useState(null);
  const [manualPreviewStopVersion, setManualPreviewStopVersion] = useState(0);
  const session = currentSession(device);
  const capabilities = new Set(session?.capabilities || device?.capabilities || []);
  const health = device?.health || {};
  const online = session?.state === "online" || session?.state === "degraded";
  const controlEnabled = featureGates?.control === true;
  const manualEnabled = featureGates?.manual_control_v1 === true;
  const mediaEnabled = featureGates?.media === true;
  const motionSafe = health.fault !== true && health.feedback_frozen !== true &&
    health.link_lost !== true &&
    (!Number.isFinite(health.feedback_age_ms) || health.feedback_age_ms <= 400);
  const canPreflight = online && controlEnabled && manualEnabled &&
    capabilities.has("manual_control_v1") && capabilities.has("manual_preflight_v1") &&
    health.fault !== true && health.feedback_frozen !== true &&
    health.link_lost !== true && health.paused !== true && !motionSafe;

  const submit = async (type, emergency = false) => {
    setPending(type);
    setMessage(null);
    const path = emergency
      ? `/api/v1/devices/${encodeURIComponent(device.device_id)}/emergency-stop`
      : `/api/v1/devices/${encodeURIComponent(device.device_id)}/commands`;
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify(emergency ? {} : { type, params: {} }),
      });
      const data = await settleCommand(await parseResponse(response));
      setMessage(commandResultText(data, t));
      if (response.ok || data.state) onChanged?.();
    } catch {
      setMessage(t("command.failed"));
    } finally {
      setPending(null);
    }
  };

  const actions = [
    ["control.status", "control.status", "status", true],
    ["control.pause", "control.pause", "motion", true],
    ["control.preflight", "control.preflight", "manual_preflight_v1", canPreflight],
    ["control.resume", "control.resume", "motion", motionSafe],
    ["control.home", "control.home", "motion", motionSafe],
  ];

  return (
    <>
      <section className="control-section" aria-labelledby="control-heading">
        <div className="control-heading">
          <div>
            <h4 id="control-heading">{t("control.heading")}</h4>
            <p>{t("control.copy")}</p>
          </div>
          <span className="control-state" data-state={online && controlEnabled ? "ready" : "disabled"}>{online && controlEnabled ? t("control.ready") : t("control.disabled")}</span>
        </div>
        <div className="control-actions">
          {actions.map(([type, labelKey, capability, safetyReady]) => (
            <button
              key={type}
              className="quiet-button"
              type="button"
              disabled={!online || !controlEnabled || !capabilities.has(capability) || !safetyReady || pending !== null}
              onClick={() => submit(type)}
            >
              {pending === type ? t("control.sending") : t(labelKey)}
            </button>
          ))}
          <button
            className="danger-button"
            type="button"
            disabled={!online || !controlEnabled || !(capabilities.has("safety") || capabilities.has("emergency_stop")) || pending !== null}
            onClick={() => submit("emergency_stop", true)}
          >
            {pending === "emergency_stop" ? t("control.sending") : t("control.emergency")}
          </button>
        </div>
        {message && <p className="control-message" role="status">{message}</p>}
      </section>
      <ManualControl
        device={device}
        enabled={online && controlEnabled && manualEnabled && capabilities.has("manual_control_v1") && motionSafe}
        onCameraPreviewStopped={() => setManualPreviewStopVersion((value) => value + 1)}
      />
      <CameraPreview
        device={device}
        mediaEnabled={mediaEnabled}
        manualPreviewStopVersion={manualPreviewStopVersion}
      />
    </>
  );
}
