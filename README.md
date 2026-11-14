# hey-coco

A home-built kitchen assistant that replaces our Google Home. Say **"hey coco"** to set
timers, check the weather, see pictures, hear animal and vehicle sounds, play YouTube and
Spotify, or ask a quick question. When idle it shows the time and the next few hours of
forecast; at night it goes dark and waits for the wake phrase.

- **Coco Hub** (`hub/`) runs on an always-on Mac: rules engine, optional local Qwen (Ollama),
  timers, weather, media lookups.
- **Coco Display** (`display/`) is a SwiftUI app for iPad (also runs on Apple silicon Macs):
  wake word, speech, screens, alarms, night mode.

See the [design](docs/superpowers/specs/2026-11-14-hey-coco-design.md) and the
[roadmap](docs/superpowers/plans/2026-11-14-hey-coco-roadmap.md). Run `make help` for commands.

New to the setup? Follow the [kitchen kiosk setup guide](docs/kitchen-setup.md) (Mac Hub, then iPad).
