# SMS-Man Windows UI Design Plan

## Goal

Refit the Tkinter client in `sms_man_app.py` as a compact Windows SMS activation console that follows the supplied CSS and reference screen: deep blue header, pale blue-gray surface, restrained white panels, explicit operational state, and a strong primary action.

## Visual Direction

- **Product type:** Windows APP UI, not a marketing page or dashboard card mosaic.
- **Color system:** deep royal-blue header and primary action; CSS reference primary `#0187ff`; pale blue-gray application surface; white work panels; green only for healthy/active status; red only for stop/destructive action.
- **Typography:** Segoe UI for native Windows fit; 14px minimum body-equivalent text, 16px+ for interactive controls, 20px title, 22–24px received code.
- **Geometry:** 8px spacing scale; 8px control radius; thin cool-gray borders; no decorative gradients, icon circles, or non-functional cards.

**Decision D6: use a rounded card dashboard.**

- Use one card per actual interaction: setup, active activation, and history. Do not split number and code into separate decorative cards.
- Keep cards compact with 8px radius, thin borders, and one full-width primary control; avoid a dashboard mosaic.
- Preserve native focus treatment and readable Windows control density even where the web reference uses custom styling.

## Information Architecture

**Decision D3: single three-zone work console.**

```
┌ Header: SMS-Man 자동 발급 | connection / balance status ┐
├ Setup zone: token, application ID, country ID, privacy note ┤
├ Active-work zone: lifecycle status, primary action, phone/code results ┤
└ History zone: timestamped activity log and clear action ┘
```

1. **Setup zone** is read first only before a run. API token remains masked by default and is never stored.
2. **Active-work zone** is the visual anchor during a run. It shows the current lifecycle, elapsed time, retry countdown, primary action, phone number, SMS code, and copy feedback.
3. **History zone** provides compact evidence without competing with the current lifecycle. It uses a fixed-height scrollable log.

## Existing Implementation to Reuse

- One active `Poller` and its queued UI events in `sms_man_app.py:163-216`.
- Existing token masking and no-persistence behavior in `sms_man_app.py:230-245`.
- Existing `number`, `code`, `status`, `start`, `stop`, and `reject` events in `sms_man_app.py:259-324`.
- Standard-library Tkinter/`ttk`; no new GUI dependency.

## Interaction State Contract

**Decision D4: persistent status strip and result feedback.**

| Feature | Loading | Empty / idle | Error | Success | Partial / retry |
|---|---|---|---|---|---|
| Number request | Blue activity dot; “번호 요청 중” | Neutral dot; “발급 준비됨” | Red dot; actionable API reason | Green dot; acquired number | Amber dot; attempt count, elapsed time, “3초 뒤 재시도” |
| SMS polling | Blue activity dot; “SMS 대기 중” | Number shown, code placeholder | Red dot; request remains inspectable | Green dot; code emphasized | Neutral dot when stopped; no further polling |
| Code copy | Copy button enabled | Disabled until a code exists | Clipboard failure shown in strip | Button reads “복사됨” briefly | N/A |
| Reject | Enabled only for an active number | Disabled | Red strip with failure reason | Neutral strip: “번호를 거절했습니다” | N/A |

- Keep `messagebox` only for blocking validation before a request starts. API lifecycle outcomes stay in the persistent strip and the history log.
- The primary action changes by lifecycle: `번호 발급 시작` (idle), `중지` (requesting or polling), and returns to idle only after a terminal outcome.
- The status strip always communicates the next system action or the user’s available recovery action.

## User Journey

**Decision D5: retain a successful result until the user manually starts the next activation.**

| Step | User does | User feels | Screen response |
|---|---|---|---|
| 1 | Opens the app | Oriented | Header names the tool; setup is visible and token is masked. |
| 2 | Starts an activation | In control | Primary action becomes `중지`; status strip explains the request. |
| 3 | Inventory is unavailable | Reassured, not confused | Amber retry state gives attempt count, elapsed time, and next retry. |
| 4 | SMS arrives | Relieved | Code becomes the largest value, is copied, and copy feedback is visible. |
| 5 | Uses the code elsewhere | Confident | Number and code remain until the user explicitly starts the next activation. |

No automatic re-purchase and no time-based result clearing are in scope.

## Design System Status

No `DESIGN.md` exists. This plan uses the supplied CSS tokens and the Windows-native `ttk.Style` theme as the design contract.

**Decision D7: keep the token contract in this plan.** Promote these rules to `DESIGN.md` only when the project gains a second UI surface or a reusable settings/history screen.

## Windows Accessibility and Sizing

**Decision D8: support Windows high-DPI, keyboard use, and resizable layouts.**

- Set a practical minimum size, allow horizontal expansion, and keep the history zone at a fixed useful height so active controls remain visible.
- Tab order: token → application ID → country ID → primary action → reject → phone copy → code copy → clear log.
- Enter starts only when idle and required values are present; Escape stops only while work is active.
- Every colored status includes a text label; focus is visible on all buttons and inputs; labels remain visible above populated fields.
- Mobile and a web layout are explicitly not in scope.

## Not in Scope

- **SMS-Man balance badge:** deferred. The reference’s green badge is not reproduced until `get-balance` has its own explicit refresh behavior, error state, and unit tests.
- **Automatic successive activations:** excluded. A successful code remains visible until manual restart.
- **Mobile or browser UI:** excluded. This is a Windows Tkinter client.

## Implementation Tasks

- [x] **T1 (P1, human: ~2h / CC: ~15min)** — `main()` layout — replace the sequential form with compact setup, active-work, and history cards.
  - Surfaced by: Pass 1 — Decision D3 information hierarchy.
  - Files: `sms_man_app.py`
  - Verify: launch at 100%, 125%, and 200% Windows scaling; active controls remain visible.
- [ ] **T2 (P1, human: ~2h / CC: ~15min)** — lifecycle presentation — add explicit active/retry/wait/success/stopped/error state data, retry countdown, elapsed time, and timestamped log.
  - Surfaced by: Pass 2 — Decision D4 state contract.
  - Files: `sms_man_app.py`, `tests/test_sms_man.py`
  - Verify: unit-test lifecycle/event mapping; manually run a retryable fake response and verify no duplicate start action.
- [x] **T3 (P1, human: ~1h / CC: ~10min)** — result actions — retain phone/code after success, expose separate copy controls, and show transient copy confirmation.
  - Surfaced by: Pass 3 — Decision D5 success journey.
  - Files: `sms_man_app.py`
  - Verify: receive a code, confirm it stays visible until the next explicit start, and verify keyboard focus/copy feedback.
- [x] **T4 (P2, human: ~1h / CC: ~10min)** — native styling — add narrow `ttk.Style` tokens for the blue header, compact rounded interaction cards, primary/stop buttons, and semantic status text.
  - Surfaced by: Passes 4–5 — Decisions D6 and D7 visual system.
  - Files: `sms_man_app.py`
  - Verify: inspect idle, retry, error, and code-received screens against the reference; do not add GUI dependencies.
- [ ] **T5 (P1, human: ~1h / CC: ~10min)** — Windows usability — remove fixed-size-only behavior; define minimum size, tab order, Enter/Escape rules, focus visibility, and text-based status labels.
  - Surfaced by: Pass 6 — Decision D8 Windows accessibility.
  - Files: `sms_man_app.py`
  - Verify: keyboard-only pass and manual 125%/200% scaling pass.

## Design Review Summary

| Review dimension | Initial | Final | Result |
|---|---:|---:|---|
| Information architecture | 4/10 | 10/10 | D3 three-zone console |
| Interaction state coverage | 3/10 | 10/10 | D4 persistent strip and copy feedback |
| User journey | 5/10 | 10/10 | D5 manual restart with retained result |
| AI slop risk | 4/10 | 9/10 | D6 compact, interaction-earned cards |
| Design system alignment | 3/10 | 8/10 | D7 plan-scoped tokens |
| Windows accessibility | 4/10 | 10/10 | D8 high-DPI and keyboard contract |
| Unresolved decisions | 0 | 0 | D9 balance deferred, D10 TODO added |

- **Overall:** 3/10 → 9/10.
- **Mockups:** not generated. The installed gstack designer requires an OpenAI API key that is not configured; the reference screenshot and CSS tokens remain the visual source.
- **Task artifact:** JSONL aggregation was skipped because `jq` is unavailable; an empty run marker was written under `~/.gstack/projects/JaeM-beginner-sms-man-auto/`.
- **Next verification:** after implementation, launch the native window at 100%, 125%, and 200% Windows scaling and run the existing unit-test suite.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | NOT RUN | required before shipping |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | CLEAN | score: 3/10 → 9/10, 7 decisions |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** DESIGN CLEARED — ready for implementation planning; eng review required before shipping.
NO UNRESOLVED DECISIONS
