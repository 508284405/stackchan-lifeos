# Playwright Automation Test Results

Date: 2026-08-30  
Scope: Web Bridge fake-device browser monitoring and failure recovery.

## Summary

| Metric | Result |
| --- | --- |
| Specs added/updated | CLI black-box run using `playwright-cli`; feature inventory and case design added |
| Scenarios run | 7 |
| Passed | 7 |
| Failed | 0 unresolved; 2 product defects fixed during the run |
| Skipped | 0 |
| Build/typecheck | `node --check web/app.js` PASS |
| Final console | 0 errors, 0 warnings |

## Commands

```text
python3 tools/web_bridge_browser_server.py --host 127.0.0.1 --port 8765
playwright-cli open http://127.0.0.1:8765 --headed
playwright-cli snapshot
playwright-cli click Devices
playwright-cli fill "Filter devices" "fake-01"
playwright-cli fill "Filter devices" "no-such-device"
curl -X POST http://127.0.0.1:8765/__test__/disconnect
curl -X POST http://127.0.0.1:8765/__test__/reconnect
playwright-cli open http://127.0.0.1:8765 --mobile
```

The browser-visible snapshot showed `Bridge online`, one registered/online fake
device, selected device detail, `Fresh`, `Sampled`, negotiated capabilities,
and retained event entries. After disconnect it showed `Attention required`,
online `0`, needs attention `1`, and device state `offline`; after reconnect it
showed a new session/transport and `online` again. The mobile viewport reported
`innerWidth=360`, `scrollWidth=360`, and `bodyWidth=360`.

## Coverage

| Feature domain | Cases | Status | Notes |
| --- | --- | --- | --- |
| Smoke | app load, title, navigation, Bridge status | PASS | Real headed browser against local fake server |
| Main flow | device selection, search filter, empty filter, refresh, event stream | PASS | Used visible text, roles, and searchbox only |
| Error injection | server-side session disconnect and `fault=true` health sample | PASS | UI distinguishes offline/attention/fault from healthy |
| Recovery | fake session reconnect and snapshot refresh | PASS | New session/transport appears; prior identity remains |
| Accessibility/responsive | mobile viewport, semantic snapshot, keyboard-focusable controls | PASS | No horizontal overflow; status text is visible |

## Failures And Debug Results

| Case | User action | Expected | Actual | Classification | Root cause |
| --- | --- | --- | --- | --- | --- |
| Initial load | Open page | No console errors | Browser requested missing `/favicon.ico` and got 404 | product defect | No favicon declaration |
| Device detail | Select device | Empty inspector disappears | `hidden=true` but computed display remained `grid` | product defect | Author CSS overrode the user-agent hidden rule |

## Fixes

| File | Change | Reason | Verification |
| --- | --- | --- | --- |
| `web/index.html` | Added a no-request favicon declaration | Remove deterministic 404 noise | Reloaded browser; console 0 errors |
| `web/styles.css` | Added `[hidden] { display: none !important; }` | Enforce the DOM visibility contract | Computed style and screenshot show only selected-device detail |
| `web/app.js` | Treat boolean `health.fault` as a fault in overview/detail | Real firmware status uses `fault`, not only `faults[]` | Browser fault injection showed `Health=Fault` and `Fault reported` |

## Artifacts

- [Online desktop screenshot](/Users/wangyu/product/stackchan-lifeos/output/playwright/web-bridge-recovered.png)
- [Offline desktop screenshot](/Users/wangyu/product/stackchan-lifeos/output/playwright/web-bridge-offline.png)
- [Mobile screenshot](/Users/wangyu/product/stackchan-lifeos/output/playwright/web-bridge-mobile.png)
- [Feature inventory](/Users/wangyu/product/stackchan-lifeos/docs/system-feature-inventory.md)
- [Black-box test cases](/Users/wangyu/product/stackchan-lifeos/docs/playwright-automation-test-cases.md)

## Regression

| Command | Result |
| --- | --- |
| `node --check web/app.js` | PASS |
| `PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' make bridge-test` | PASS; 89 tests |

## Remaining Risks

- Browser tests use the deterministic local fake-device server; they do not prove
  browser-to-real-device API behavior.
- Long-lived real USB disconnect/reconnect and physical HIL remain separate gates.
