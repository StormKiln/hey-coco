# hey-coco

Kitchen assistant replacing a Google Home. Read the design first:
`docs/superpowers/specs/2026-11-14-hey-coco-design.md`, and the roadmap:
`docs/superpowers/plans/2026-11-14-hey-coco-roadmap.md`.

## Layout
- `hub/` — Coco Hub (Mac, always on). Python 3.13, FastAPI, uv. Package `coco_hub` in `hub/src/`.
- `display/` — Coco Display (iPad 7th gen, iPadOS 18). SwiftUI app in `display/App/`;
  all testable logic in the `CocoKit` Swift package (`display/CocoKit/`), which must stay
  UI-free enough to run under `swift test` on macOS.
  The Xcode project is generated from `display/project.yml` by XcodeGen and not committed.
- `docs/` — design, roadmap, per-phase plans, spike findings (`docs/spikes/`).
- `assets/` — sound library and other media.

## Commands
Always use the Makefile (`make help`). CI runs `make hub-lint`, `make hub-test`, `make hub-resilience`,
`make kit-test`, `make display-build`.

## Rules
- Never work on `main`; one branch + PR per issue, title prefixed with the issue (`#12: ...`), body `Closes #12`.
- TDD: write the failing test first. Hub tests never hit the live network (use recorded responses).
- No secrets in git: API keys, Picovoice AccessKey, Spotify tokens live in `hub/config.local.toml` or env.
- The Hub ↔ Display contract lives in `docs/protocol/`; change it in one PR together with both sides.
