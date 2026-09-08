# Web Console

React (Vite) single-page console served by the FastAPI Web Bridge. It reads
`/api/v1/health` and `/api/v1/devices`, then subscribes to `/api/v1/events`
using `visible_devices` plus a resumable cursor.

## Layout

- `src/` — React source (ES modules; entry `src/main.jsx`).
- `dist/` — committed Vite build output; FastAPI mounts this directory
  (`bridge/api.py` prefers `dist/` and falls back to the repo-root `web/`
  files when no build exists). This keeps the bridge runnable without Node.
- `vite.config.js` — dev server proxies `/api/v1/*` to a locally running
  bridge (default `http://127.0.0.1:8000`, override with
  `LIFEOS_BRIDGE_ORIGIN`).

## Commands

- `npm install` / `npm ci` — install toolchain (Node 18+).
- `npm run dev` — Vite dev server with API/WS proxy for live development.
- `npm run build` — rebuild `dist/` (`make web-check` does this; skipped
  gracefully when Node/npm are unavailable).

## i18n

The console ships Simplified Chinese (`zh`) by default with an English
(`en`) toggle in the top bar. UI strings live in `src/i18n.jsx`
(`messages`); the choice persists in `localStorage` under
`lifeos.web.lang` and updates `<html lang>`.

The console exposes typed `status`, `pause`, `resume`, `home`, and separate
remote-stop actions. Results are shown from the Bridge command lifecycle, so an
HTTP `202` or device ACK is not silently presented as completed motion.

The device detail view also exposes an explicit, feature-gated camera preview.
It uses a fixed QVGA JPEG MJPEG stream, keeps only the latest in-memory frame,
and never records or persists camera bytes. The four-way manual control pad
remains visible as a disabled affordance until the device declares
`manual_control_v1` and the server gate is enabled; raw serial, raw `lifeos.v1`
envelopes, and hardware parameters are never accepted by the browser API.
