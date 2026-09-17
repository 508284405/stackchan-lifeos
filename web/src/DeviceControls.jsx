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

function useControlChannel(deviceId, enabled) {
  const socketRef = useRef(null);
  const listenersRef = useRef(new Set());
  const [connectionId, setConnectionId] = useState(null);

  const subscribe = useCallback((listener) => {
    listenersRef.current.add(listener);
    return () => listenersRef.current.delete(listener);
  }, []);

  const send = useCallback((message) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  }, []);

  useEffect(() => {
    if (!enabled || !deviceId) return undefined;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/control`);
    socketRef.current = socket;
    const publish = (value) => listenersRef.current.forEach((listener) => listener(value));
    socket.addEventListener("message", (event) => {
      try {
        const value = JSON.parse(event.data);
        if (value.type === "control.connected") setConnectionId(value.connection_id);
        publish(value);
      } catch {
        // Ignore malformed server data; the socket lifecycle still fails closed.
      }
    });
    socket.addEventListener("close", () => {
      if (socketRef.current === socket) socketRef.current = null;
      setConnectionId(null);
      publish({ type: "control.disconnected" });
    });
    socket.addEventListener("error", () => publish({ type: "control.disconnected" }));
    return () => {
      setConnectionId(null);
      if (socketRef.current === socket) socketRef.current = null;
      socket.close();
    };
  }, [deviceId, enabled]);

  return { connectionId, send, subscribe, connected: connectionId !== null };
}

function ManualControl({ device, enabled, channel }) {
  const { t } = useI18n();
  const leaseRef = useRef(null);
  const acquireRef = useRef(null);
  const acquireWaiterRef = useRef(null);
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
    const lease = leaseRef.current;
    if (!channel.connected || !lease || inputInFlightRef.current) return false;
    sequenceRef.current += 1;
    inputInFlightRef.current = true;
    pendingActionRef.current = action;
    channel.send({
      type: "input",
      lease_id: lease.lease_id,
      input_seq: sequenceRef.current,
      action,
      ...(direction ? { direction } : {}),
      ttl_ms: 500,
    });
    return true;
  }, [channel]);

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
    if (!channel.connected) return Promise.reject(new Error(t("manual.disconnected")));
    const promise = new Promise((resolve, reject) => {
      acquireWaiterRef.current = { resolve, reject };
      if (!channel.send({
        type: "lease.acquire",
        device_id: device.device_id,
        ttl_ms: 500,
        max_duration_ms: 30_000,
      })) {
        acquireWaiterRef.current = null;
        reject(new Error(t("manual.disconnected")));
      }
    });
    acquireRef.current = promise.finally(() => {
      acquireRef.current = null;
    });
    return acquireRef.current;
  }, [channel, device.device_id, t]);

  useEffect(() => channel.subscribe((value) => {
    if (value.type === "lease.acquired") {
      leaseRef.current = value.lease;
      sequenceRef.current = 0;
      setState("ready");
      acquireWaiterRef.current?.resolve(value.lease);
      acquireWaiterRef.current = null;
    } else if (value.type === "control.error") {
      inputInFlightRef.current = false;
      pendingActionRef.current = null;
      releasePendingRef.current = false;
      setMessage(value.reason || value.code || t("manual.failed"));
      acquireWaiterRef.current?.reject(new Error(value.reason || value.code || "control rejected"));
      acquireWaiterRef.current = null;
    } else if (value.type === "command.state.changed" && value.command?.type === "manual_control") {
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
    } else if (value.type === "control.disconnected") {
      clearTimer();
      leaseRef.current = null;
      inputInFlightRef.current = false;
      pendingActionRef.current = null;
      releasePendingRef.current = false;
      setState("idle");
      acquireWaiterRef.current?.reject(new Error(t("manual.disconnected")));
      acquireWaiterRef.current = null;
    }
  }), [channel, clearTimer, sendRelease, t]);

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

function CameraPreview({ device, mediaEnabled = false, channel, onVideoReady }) {
  const { t } = useI18n();
  const [active, setActive] = useState(false);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [state, setState] = useState("idle");
  const [message, setMessage] = useState(null);
  const [frame, setFrame] = useState(null);
  const activeRef = useRef(false);
  const frameRef = useRef(null);
  const session = currentSession(device);
  const capabilities = new Set(session?.capabilities || device?.capabilities || []);
  const online = session?.state === "online" || session?.state === "degraded";
  const canPreview = mediaEnabled && channel.connected && capabilities.has("camera") &&
    capabilities.has("camera_capture_ts_v1") && online;

  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  useEffect(() => channel.subscribe((value) => {
    if (value.type === "viewer.opened" && value.device_id === device.device_id) {
      setViewerOpen(true);
      setState("waiting");
    } else if (value.type === "viewer.closed" || value.type === "control.disconnected") {
      setViewerOpen(false);
      setActive(false);
      setState("idle");
      onVideoReady(false);
    } else if (value.type === "video.display.acknowledged") {
      setState("live");
      onVideoReady(true);
    } else if (value.type === "control.error" && activeRef.current) {
      setMessage(value.reason || value.code || t("camera.failed"));
      setState("stale");
      onVideoReady(false);
    }
  }), [channel, device.device_id, onVideoReady, t]);

  useEffect(() => {
    if (active && (!canPreview || !session?.session_id)) {
      setActive(false);
      setViewerOpen(false);
      setState("idle");
      onVideoReady(false);
    }
  }, [active, canPreview, onVideoReady, session?.session_id]);

  useEffect(() => {
    setActive(false);
    setViewerOpen(false);
    setState("idle");
    setMessage(null);
    setFrame(null);
    onVideoReady(false);
  }, [device?.device_id, onVideoReady]);

  useEffect(() => {
    if (!active || !viewerOpen || !channel.connectionId) return undefined;
    let cancelled = false;
    let timeout = null;
    const poll = async () => {
      try {
        const response = await fetch(
          `/api/v1/devices/${encodeURIComponent(device.device_id)}/camera/frame?viewer_id=${encodeURIComponent(channel.connectionId)}`,
          { cache: "no-store" },
        );
        if (response.status === 404) return;
        if (!response.ok) throw new Error("camera frame unavailable");
        const frameId = response.headers.get("X-LifeOS-Frame-Id");
        const token = response.headers.get("X-LifeOS-Frame-Token");
        if (!frameId || !token || frameRef.current?.id === frameId) return;
        const url = URL.createObjectURL(await response.blob());
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        setFrame((previous) => {
          if (previous?.url) URL.revokeObjectURL(previous.url);
          return { id: frameId, token, url };
        });
      } catch {
        if (!cancelled) {
          setState("stale");
          onVideoReady(false);
        }
      } finally {
        if (!cancelled) timeout = window.setTimeout(poll, 100);
      }
    };
    poll();
    return () => {
      cancelled = true;
      if (timeout !== null) window.clearTimeout(timeout);
    };
  }, [active, channel.connectionId, device.device_id, onVideoReady, viewerOpen]);

  useEffect(() => {
    frameRef.current = frame;
    return () => {
      if (frame?.url) URL.revokeObjectURL(frame.url);
    };
  }, [frame]);

  useEffect(() => {
    const hide = () => {
      if (document.visibilityState !== "visible" && activeRef.current) {
        channel.send({ type: "viewer.close" });
        setActive(false);
        setViewerOpen(false);
        setState("idle");
        onVideoReady(false);
      }
    };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  }, [channel, onVideoReady]);

  useEffect(() => () => {
    if (activeRef.current) {
      channel.send({ type: "viewer.close" });
      fetch(`/api/v1/devices/${encodeURIComponent(device.device_id)}/camera-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        body: JSON.stringify({ action: "stop" }),
      }).catch(() => {});
    }
  }, [channel, device.device_id]);

  const toggle = async () => {
    const action = active ? "stop" : "start";
    setState("requesting");
    setMessage(null);
    onVideoReady(false);
    try {
      if (action === "stop") channel.send({ type: "viewer.close" });
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
        setState("waiting");
        if (!channel.send({ type: "viewer.open", device_id: device.device_id })) {
          throw new Error(t("manual.disconnected"));
        }
      } else {
        setActive(false);
        setViewerOpen(false);
        setFrame(null);
        setState("idle");
      }
    } catch (error) {
      setActive(false);
      setViewerOpen(false);
      setState("error");
      setMessage(error.message || t("camera.failed"));
    }
  };

  const acknowledgeDisplay = () => {
    if (!frame || document.visibilityState !== "visible") return;
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      if (document.visibilityState === "visible" && frameRef.current?.id === frame.id) {
        channel.send({
          type: "video.displayed",
          device_id: device.device_id,
          frame_id: frame.id,
          token: frame.token,
          visible: true,
        });
      }
    }));
  };

  return (
    <section className="control-section camera-section" aria-labelledby="camera-heading">
      <div className="control-heading">
        <div><h4 id="camera-heading">{t("camera.heading")}</h4><p>{t("camera.copy")}</p></div>
        <span className="control-state" data-state={state}>{t(`camera.state.${state}`)}</span>
      </div>
      <div className="camera-frame" data-state={state}>
        {active && frame?.url ? (
          <img src={frame.url} alt={t("camera.alt", device.display_name || device.device_id)} onLoad={acknowledgeDisplay} onError={() => { setState("stale"); onVideoReady(false); }} />
        ) : (
          <div className="camera-placeholder"><span aria-hidden="true">◌</span><p>{canPreview ? t("camera.placeholder") : mediaEnabled ? t("camera.unavailable") : t("camera.gate")}</p></div>
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
  const safetyClear = health.fault !== true && health.feedback_frozen !== true &&
    health.link_lost !== true;
  const feedbackFresh = !Number.isFinite(health.feedback_age_ms) || health.feedback_age_ms <= 400;
  // Torque-off is a safe idle state. It may have deliberately old feedback
  // until the zero-motion preflight refreshes it, so it may resume/preflight
  // but must not unlock direction input by itself.
  const safeIdle = safetyClear && health.torque_enabled === false;
  const canResume = safetyClear && (feedbackFresh || safeIdle);
  const canPreflight = online && controlEnabled && manualEnabled &&
    capabilities.has("manual_control_v1") && capabilities.has("manual_preflight_v1") &&
    health.fault !== true && health.feedback_frozen !== true &&
    health.link_lost !== true && health.paused !== true;
  const manualReady = online && controlEnabled && manualEnabled &&
    capabilities.has("manual_control_v1") && safetyClear && feedbackFresh &&
    health.paused !== true;

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
    ["control.resume", "control.resume", "motion", canResume],
    ["control.home", "control.home", "motion", safetyClear && feedbackFresh],
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
        enabled={manualReady}
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
