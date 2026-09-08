import { createContext, useContext, useMemo, useState, useEffect } from "react";

// Console-wide UI strings. Dictionary values are plain strings, or functions
// when the copy embeds a number (counts, ages). Chinese is the default
// language; the choice is remembered in localStorage.
export const DEFAULT_LANG = "zh";
const STORAGE_KEY = "lifeos.web.lang";
const LANG_ATTR = { zh: "zh-CN", en: "en" };

export const messages = {
  zh: {
    "skip.link": "跳到操作区",
    "nav.overview": "总览",
    "nav.devices": "设备",
    "nav.events": "审计与诊断",

    "transport.connecting": "连接中",
    "transport.online": "Bridge 在线",
    "transport.offline": "Bridge 离线",
    "transport.reconnecting": "事件流重连中",

    "lang.toggle": "EN",

    "intro.eyebrow": "本地运维 / 可信局域网",
    "overview.title": "当前连接状态",
    "overview.copy":
      "实时查看已注册设备、会话新鲜度与安全相关状态。已接受不等于已完成，过期绝不等于健康。",
    "refresh": "刷新快照",

    "safety.label": "安全状态",
    "safety.waiting": "等待 Bridge 快照。",
    "banner.unavailable.label": "安全状态",
    "banner.unavailable.detail": "Bridge 快照不可用，设备状态未知。",
    "banner.attention.label": "需要关注",
    "banner.attention.detail": (n) => `${n} 台设备处于降级、离线、故障或反馈过期状态。`,
    "banner.ok.label": "无活跃安全事件",
    "banner.ok.detail": (n) => `${n} 个已协商会话足够新鲜，可进行监控。`,
    "banner.idle.label": "无在线会话",
    "banner.idle.detail": "已存在注册身份，但当前没有设备会话在线。",

    "metric.registered": "已注册",
    "metric.registered.hint": "已知身份",
    "metric.online": "在线",
    "metric.online.hint": "已协商会话",
    "metric.attention": "需要关注",
    "metric.attention.hint": "降级、离线或故障",

    "devices.eyebrow": "设备注册表",
    "devices.title": "设备",
    "filter.label": "筛选设备",
    "filter.placeholder": "按名称或 ID 过滤",
    "list.snapshot": "快照",
    "devices.count": (n) => `${n} 台设备`,
    "devices.loading": "正在加载已注册设备…",
    "devices.emptyFiltered": "没有符合筛选条件的设备。",
    "devices.empty": "暂无已注册设备。",

    "usb.scan": "扫描 USB 设备",
    "usb.scanning": "扫描中…",
    "usb.title": "USB 扫描结果",
    "usb.none": "未发现 USB 串口候选设备。",
    "usb.failed": "扫描失败，请重试。",
    "usb.gate": "usb_add 能力未开启，无法扫描。",
    "usb.note": "扫描会短暂打开串口并复位 ESP32-S3 USB 外设；已被 Bridge 占用的端口自动跳过。",
    "usb.state.online": "已识别",
    "usb.state.in-use": "已连接 · 跳过",
    "usb.state.no-response": "无响应",
    "usb.state.busy": "端口被占用",
    "usb.add": "添加",
    "usb.adding": "连接中…",
    "usb.added": "已添加",
    "usb.addFailed": "添加失败，请重试。",

    "inspector.select.title": "选择设备",
    "inspector.select.copy": "设备身份、会话新鲜度、能力与健康信息将显示在这里。",
    "detail.eyebrow": "设备详情",
    "state.unknown": "未知",
    "state.online": "在线",
    "state.offline": "离线",
    "state.degraded": "降级",
    "state.rejected": "已拒绝",

    "session.heading": "会话",
    "session.id": "会话 ID",
    "session.transport": "传输",
    "session.heartbeat": "最近心跳",
    "session.sequence": "收发序号 (RX / TX)",
    "session.none": "无活动会话",
    "protocol.unknown": "协议未知",
    "freshness.fresh": "新鲜",
    "freshness.stale": "过期 / 需检查",

    "health.heading": "健康",
    "health.firmware": "固件",
    "health.motion": "运动使能",
    "health.faults": "故障",
    "health.uptime": "运行时长",
    "motion.on": "已启用",
    "motion.off": "已禁用",
    "motion.unknown": "未知",
    "faults.reported": "已报告故障",
    "faults.count": (n) => `${n} 项故障`,
    "faults.none": "无故障记录",
    "health.danger": "故障",
    "health.ok": "已采样",
    "health.none": "无样本",

    "capabilities.heading": "已协商能力",
    "monitoring.note": "控制仅通过有界高层命令；连续手动控制仍需能力与服务端门禁，原始传输写入始终不开放。",

    "control.heading": "设备控制",
    "control.copy": "发送有界的高层命令；结果以设备回执为准，不把请求当作完成。",
    "control.ready": "可操作",
    "control.disabled": "不可操作",
    "control.sending": "发送中…",
    "control.status": "读取状态",
    "control.pause": "暂停",
    "control.preflight": "准备方向控制",
    "control.resume": "恢复",
    "control.home": "回中",
    "control.emergency": "远程急停",
    "command.failed": "请求失败或 Bridge 不可用",
    "command.state.created": "已创建",
    "command.state.validated": "已校验",
    "command.state.routed": "已路由",
    "command.state.sent": "已发送",
    "command.state.accepted": "设备已接受",
    "command.state.executing": "执行中",
    "command.state.completed": "设备已完成",
    "command.state.rejected": "已拒绝",
    "command.state.safety_blocked": "被安全状态阻止",
    "command.state.offline": "设备离线，未送达",
    "command.state.timeout": "等待设备超时",
    "command.state.expired": "已过期",
    "command.state.preempted": "已被更高优先级操作抢占",
    "command.state.cancelled": "已取消",

    "manual.heading": "连续手动控制",
    "manual.copy": "设备声明 manual_control_v1 且 Bridge 手动门禁开启后可用。开始方向控制会先停止预览；松开、失焦或断线会释放租约。",
    "manual.pad": "手动方向控制",
    "manual.up": "向上",
    "manual.left": "向左",
    "manual.right": "向右",
    "manual.down": "向下",
    "manual.state.idle": "待机",
    "manual.state.connecting": "建立租约",
    "manual.state.ready": "租约就绪",
    "manual.state.holding": "保持中",
    "manual.state.releasing": "正在释放",
    "manual.state.disabled": "不可用",
    "manual.state.error": "不可用",
    "manual.failed": "手动控制未建立",
    "manual.disconnected": "控制连接已断开",
    "manual.cameraPaused": "为保持连接稳定，摄像头预览已停止",

    "camera.heading": "摄像头预览",
    "camera.copy": "显式开启后接收真实连续 MJPEG 视频流；不录制、不持久化。连续方向控制期间会暂停预览。",
    "camera.start": "开启预览",
    "camera.stop": "停止预览",
    "camera.requesting": "准备中…",
    "camera.spec": "QVGA · MJPEG · ≤10 fps · 持续",
    "camera.placeholder": "开启后显示 StackChan 画面",
    "camera.unavailable": "设备未声明摄像头或当前离线",
    "camera.gate": "Bridge 的 media 能力未开启",
    "camera.alt": (name) => `${name} 摄像头预览`,
    "camera.failed": "摄像头预览不可用",
    "camera.state.idle": "未开启",
    "camera.state.requesting": "请求中",
    "camera.state.waiting": "等待首帧",
    "camera.state.live": "实时帧",
    "camera.state.stale": "画面过期",
    "camera.state.error": "不可用",

    "events.eyebrow": "事件流",
    "events.title": "近期活动",
    "events.waiting": "等待事件",
    "events.empty": "尚未收到 Bridge 事件。",
    "events.retained": (n) => `${n} 条事件已保留`,
    "event.healthSample": "健康采样",
    "event.safetyBoundary": "协议或安全边界",
    "event.recorded": "已记录",

    "age.none": "无心跳",
    "age.now": "刚刚",
    "age.seconds": (n) => `${n} 秒前`,
    "age.minutes": (n) => `${n} 分钟前`,
    "uptime.seconds": (s) => `${s} 秒`,
    "uptime.minutes": (m, s) => `${m} 分 ${s} 秒`,
    "uptime.hours": (h, m) => `${h} 小时 ${m} 分`,

    "footer.local": "本地部署，无 Web 认证 · 默认仅回环地址",
  },

  en: {
    "skip.link": "Skip to operations",
    "nav.overview": "Overview",
    "nav.devices": "Devices",
    "nav.events": "Audit & diagnostics",

    "transport.connecting": "Connecting",
    "transport.online": "Bridge online",
    "transport.offline": "Bridge offline",
    "transport.reconnecting": "Event stream reconnecting",

    "lang.toggle": "中文",

    "intro.eyebrow": "LOCAL OPERATIONS / TRUSTED LAN",
    "overview.title": "What is connected now",
    "overview.copy":
      "A factual view of registered devices, session freshness, and safety-relevant state. Accepted is not completed; stale is never healthy.",
    "refresh": "Refresh snapshot",

    "safety.label": "Safety state",
    "safety.waiting": "Waiting for the Bridge snapshot.",
    "banner.unavailable.label": "Safety state",
    "banner.unavailable.detail": "Bridge snapshot unavailable; device state is unknown.",
    "banner.attention.label": "Attention required",
    "banner.attention.detail": (n) => `${n} device${n === 1 ? " is" : "s are"} degraded, offline, faulted, or reporting stale feedback.`,
    "banner.ok.label": "No active safety event",
    "banner.ok.detail": (n) => `${n} negotiated session${n === 1 ? " is" : "s are"} fresh enough for monitoring.`,
    "banner.idle.label": "No active sessions",
    "banner.idle.detail": "Registered identities exist, but no device session is currently online.",

    "metric.registered": "Registered",
    "metric.registered.hint": "Known identities",
    "metric.online": "Online",
    "metric.online.hint": "Negotiated sessions",
    "metric.attention": "Needs attention",
    "metric.attention.hint": "Degraded, offline, or fault",

    "devices.eyebrow": "DEVICE REGISTRY",
    "devices.title": "Devices",
    "filter.label": "Filter devices",
    "filter.placeholder": "Filter by name or ID",
    "list.snapshot": "snapshot",
    "devices.count": (n) => `${n} device${n === 1 ? "" : "s"}`,
    "devices.loading": "Loading registered devices…",
    "devices.emptyFiltered": "No devices match this filter.",
    "devices.empty": "No registered devices.",

    "usb.scan": "Scan USB devices",
    "usb.scanning": "Scanning…",
    "usb.title": "USB scan results",
    "usb.none": "No USB serial candidate ports found.",
    "usb.failed": "Scan failed; try again.",
    "usb.gate": "The usb_add capability is disabled; scanning unavailable.",
    "usb.note": "Scanning briefly opens each port and resets the ESP32-S3 USB peripheral; ports held by the Bridge are skipped automatically.",
    "usb.state.online": "Identified",
    "usb.state.in-use": "Connected · skipped",
    "usb.state.no-response": "No response",
    "usb.state.busy": "Port busy",
    "usb.add": "Add",
    "usb.adding": "Connecting…",
    "usb.added": "Added",
    "usb.addFailed": "Add failed; try again.",

    "inspector.select.title": "Select a device",
    "inspector.select.copy": "Identity, session freshness, capabilities, and recent health appear here.",
    "detail.eyebrow": "DEVICE DETAIL",
    "state.unknown": "Unknown",
    "state.online": "Online",
    "state.offline": "Offline",
    "state.degraded": "Degraded",
    "state.rejected": "Rejected",

    "session.heading": "Session",
    "session.id": "Session ID",
    "session.transport": "Transport",
    "session.heartbeat": "Last heartbeat",
    "session.sequence": "RX / TX sequence",
    "session.none": "No active session",
    "protocol.unknown": "protocol unknown",
    "freshness.fresh": "Fresh",
    "freshness.stale": "Stale / inspect",

    "health.heading": "Health",
    "health.firmware": "Firmware",
    "health.motion": "Motion enabled",
    "health.faults": "Faults",
    "health.uptime": "Uptime",
    "motion.on": "Enabled",
    "motion.off": "Disabled",
    "motion.unknown": "Unknown",
    "faults.reported": "Fault reported",
    "faults.count": (n) => `${n} reported`,
    "faults.none": "None reported",
    "health.danger": "Fault",
    "health.ok": "Sampled",
    "health.none": "No sample",

    "capabilities.heading": "Negotiated capabilities",
    "monitoring.note": "Controls use bounded high-level commands; continuous manual control still needs capability and a server gate. Raw transport writes stay closed.",

    "control.heading": "Device controls",
    "control.copy": "Send bounded high-level commands; device evidence determines the result.",
    "control.ready": "Ready",
    "control.disabled": "Unavailable",
    "control.sending": "Sending…",
    "control.status": "Read status",
    "control.pause": "Pause",
    "control.preflight": "Prepare directions",
    "control.resume": "Resume",
    "control.home": "Home",
    "control.emergency": "Remote stop",
    "command.failed": "Request failed or the Bridge is unavailable",
    "command.state.created": "Created",
    "command.state.validated": "Validated",
    "command.state.routed": "Routed",
    "command.state.sent": "Sent",
    "command.state.accepted": "Accepted by device",
    "command.state.executing": "Executing",
    "command.state.completed": "Completed by device",
    "command.state.rejected": "Rejected",
    "command.state.safety_blocked": "Blocked by safety state",
    "command.state.offline": "Offline · not delivered",
    "command.state.timeout": "Device response timed out",
    "command.state.expired": "Expired",
    "command.state.preempted": "Preempted by a higher priority action",
    "command.state.cancelled": "Cancelled",

    "manual.heading": "Continuous manual control",
    "manual.copy": "Available when the device declares manual_control_v1 and the Bridge manual gate is enabled. Starting direction control stops preview first; release, blur, or disconnect ends the lease.",
    "manual.pad": "Manual direction control",
    "manual.up": "Up",
    "manual.left": "Left",
    "manual.right": "Right",
    "manual.down": "Down",
    "manual.state.idle": "Idle",
    "manual.state.connecting": "Acquiring lease",
    "manual.state.ready": "Lease ready",
    "manual.state.holding": "Holding",
    "manual.state.releasing": "Releasing",
    "manual.state.disabled": "Unavailable",
    "manual.state.error": "Unavailable",
    "manual.failed": "Manual control could not be established",
    "manual.disconnected": "Control connection closed",
    "manual.cameraPaused": "Camera preview stopped to keep the connection stable",

    "camera.heading": "Camera preview",
    "camera.copy": "Start explicitly to receive a real continuous MJPEG video stream; nothing is recorded or persisted. Preview pauses during continuous manual control.",
    "camera.start": "Start preview",
    "camera.stop": "Stop preview",
    "camera.requesting": "Preparing…",
    "camera.spec": "QVGA · MJPEG · ≤10 fps · continuous",
    "camera.placeholder": "Start to view the StackChan camera",
    "camera.unavailable": "Camera not declared or device is offline",
    "camera.gate": "The Bridge media gate is disabled",
    "camera.alt": (name) => `${name} camera preview`,
    "camera.failed": "Camera preview unavailable",
    "camera.state.idle": "Not started",
    "camera.state.requesting": "Requesting",
    "camera.state.waiting": "Waiting for first frame",
    "camera.state.live": "Live frames",
    "camera.state.stale": "Stale frame",
    "camera.state.error": "Unavailable",

    "events.eyebrow": "EVENT STREAM",
    "events.title": "Recent activity",
    "events.waiting": "Waiting for events",
    "events.empty": "No Bridge events received yet.",
    "events.retained": (n) => `${n} retained event${n === 1 ? "" : "s"}`,
    "event.healthSample": "health sample",
    "event.safetyBoundary": "protocol or safety boundary",
    "event.recorded": "recorded",

    "age.none": "No heartbeat",
    "age.now": "just now",
    "age.seconds": (n) => `${n}s ago`,
    "age.minutes": (n) => `${n}m ago`,
    "uptime.seconds": (s) => `${s}s`,
    "uptime.minutes": (m, s) => `${m}m ${s}s`,
    "uptime.hours": (h, m) => `${h}h ${m}m`,

    "footer.local": "Local, no Web authentication · loopback by default",
  },
};

function readStoredLang() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && messages[stored]) return stored;
  } catch {
    // localStorage may be unavailable (privacy mode); fall through to default.
  }
  return DEFAULT_LANG;
}

const LangContext = createContext(null);

export function LanguageProvider({ children }) {
  const [lang, setLang] = useState(readStoredLang);

  useEffect(() => {
    document.documentElement.lang = LANG_ATTR[lang] || DEFAULT_LANG;
    try {
      window.localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // Ignore persistence failures; the in-memory choice still applies.
    }
  }, [lang]);

  const value = useMemo(() => {
    const t = (key, ...args) => {
      const entry = messages[lang][key] ?? messages[DEFAULT_LANG][key] ?? key;
      return typeof entry === "function" ? entry(...args) : entry;
    };
    return {
      lang,
      t,
      toggleLang: () => setLang((current) => (current === "zh" ? "en" : "zh")),
    };
  }, [lang]);

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useI18n() {
  return useContext(LangContext);
}
