import { useCallback, useEffect, useRef, useState } from "react";

// Bridge data plane: snapshot fetching plus the resumable /api/v1/events
// WebSocket, mirroring the W2 monitoring contract (read-only, cursor-based).

const EVENT_BUFFER = 80;
const SNAPSHOT_TRIGGERS = ["device.summary.changed", "device.session.changed", "telemetry.sampled", "device.safety.changed"];

export function useBridgeData() {
  const [devices, setDevices] = useState([]);
  const [health, setHealth] = useState(null);
  const [events, setEvents] = useState([]);
  const [transport, setTransport] = useState({ status: "unknown", labelKey: "transport.connecting" });

  const cursorRef = useRef("0");
  const reconnectDelayRef = useRef(500);
  const socketRef = useRef(null);

  const refreshSnapshot = useCallback(async () => {
    try {
      const [healthResponse, devicesResponse] = await Promise.all([
        fetch("/api/v1/health", { cache: "no-store" }),
        fetch("/api/v1/devices", { cache: "no-store" }),
      ]);
      if (!healthResponse.ok || !devicesResponse.ok) throw new Error("Bridge snapshot failed");
      setHealth(await healthResponse.json());
      setDevices((await devicesResponse.json()).items || []);
      setTransport({ status: "online", labelKey: "transport.online" });
    } catch {
      setHealth(null);
      setTransport({ status: "offline", labelKey: "transport.offline" });
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    let reconnectTimer = null;

    const connect = () => {
      if (disposed) return;
      if (socketRef.current) socketRef.current.close();
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/events`);
      socketRef.current = socket;

      socket.addEventListener("open", () => {
        reconnectDelayRef.current = 500;
        setTransport({ status: "online", labelKey: "transport.online" });
        socket.send(JSON.stringify({ visible_devices: true, cursor: cursorRef.current, include_telemetry: true }));
      });

      socket.addEventListener("message", (message) => {
        if (disposed) return;
        const event = JSON.parse(message.data);
        if (event.type === "resync_required") {
          setEvents([]);
          cursorRef.current = "0";
          refreshSnapshot();
          socket.send(JSON.stringify({ visible_devices: true, cursor: "0", include_telemetry: true }));
          return;
        }
        if (event.type === "subscription.error") return;
        if (event.cursor) cursorRef.current = String(event.cursor);
        setEvents((current) => [...current, event].slice(-EVENT_BUFFER));
        if (SNAPSHOT_TRIGGERS.includes(event.type)) refreshSnapshot();
      });

      socket.addEventListener("close", () => {
        if (disposed || socketRef.current !== socket) return;
        setTransport({ status: "offline", labelKey: "transport.reconnecting" });
        reconnectTimer = window.setTimeout(connect, reconnectDelayRef.current);
        reconnectDelayRef.current = Math.min(reconnectDelayRef.current * 2, 8000);
      });

      socket.addEventListener("error", () => socket.close());
    };

    refreshSnapshot();
    connect();

    return () => {
      disposed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (socketRef.current) socketRef.current.close();
    };
  }, [refreshSnapshot]);

  return { devices, health, events, transport, refreshSnapshot };
}
