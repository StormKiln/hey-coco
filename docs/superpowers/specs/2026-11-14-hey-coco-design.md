# hey-coco — Design

Date: 2026-11-14
Repo: `StormKiln/hey-coco` (to be created)

## Intent

The family's Google Home is dying. hey-coco replaces it with a home-built kitchen
assistant the family owns end to end. It lives on the kitchen counter, is used by
everyone in the family, and is spoken to with the wake phrase **"hey coco"**.

Success: the family stops missing the Google Home. Timers, weather, music, YouTube,
"what does a cow say", and quick questions all work hands-free from across the
kitchen, and the device shows the time and near-term forecast when idle and goes
dark at night.

## Goals (v1)

- Wake phrase "hey coco", always listening; tap-to-talk as fallback.
- Multiple named timers (create, query, cancel, add time); loud alarm that rings
  until dismissed by voice ("stop") or tap.
- Weather: current conditions, next hours, "tomorrow", "will it rain this afternoon".
- "Show me a …" pictures from Wikimedia Commons.
- Animal & vehicle sounds from a curated local library (~50 clips to start), each
  paired with a picture.
- YouTube: voice search, play top result full screen, pause / next / stop.
- Spotify (Premium): play artist / album / playlist / song through the iPad speaker,
  pause / resume / skip / volume.
- General questions answered by a local Qwen model, kept short.
- Spoken replies (iPad built-in TTS) plus on-screen text.
- Idle screen: large clock + current temp + forecast for the next ~6 hours.
- Night mode on a configurable schedule: dim at bedtime, fully dark after a few idle
  minutes, wake on "hey coco" or tap.
- The same Display app runs on the MacBook (Apple silicon runs iPad apps) for
  development and as an alternate display.

## Non-goals (v1) — deferred, design must leave room

- Recipes (deferred by user; will return).
- Family photos from iCloud (later phase; idle screen should be able to host a
  slideshow later).
- Content lockdown / parental controls for YouTube & Spotify (deferred by user;
  keep a single choke point in the Hub so it can be added later).

## Non-goals (my calls, not discussed)

- Wake-up alarms at a clock time, reminders, shopping lists, calendars, smart-home
  control, multi-room / multiple Displays, voice identification of who is speaking.
  These are reasonable future features, not v1.

## Anti-goals (would be a failure even if it "works")

- A timer that fails silently. If the Hub is unreachable, a running timer must
  still ring on the iPad.
- Coco becoming unresponsive with no way back short of a reboot (e.g. stuck black
  screen). A tap must always wake it.
- The clock/timers depending on the internet. Internet-backed features may fail;
  the core must not.
- Confidently wrong behaviour on commands: if the rules + Qwen can't map a request
  to an action, say so rather than doing something random.

## Constraints

- **Hardware:** iPad 7th gen (A10, 3 GB RAM), iPadOS 18.7, on the counter, always
  plugged in, using its own speaker. MacBook Pro (newer, Apple silicon), always on,
  same LAN.
- **iPadOS cannot truly turn off the screen** from an app while keeping the mic
  live. "Off" = black UI + `UIScreen.brightness` at minimum, idle timer disabled,
  app held in foreground with Guided Access.
- **Spotify** playback goes through the Spotify iOS app on the iPad (Spotify
  Connect). Spotify can drop an idle iPad from its device list — must be de-risked
  with an early spike.
- **YouTube Data API** free quota ≈ 100 searches/day — plenty for a family; cache
  recent searches.
- **Google Images** has no usable public API for new users → Wikimedia Commons.
- **Porcupine** (Picovoice) needs a free personal AccessKey and a custom "hey coco"
  keyword model trained in the Picovoice console for iOS (and macOS for the Mac
  build).
- Owner's preferences: Makefiles for consistent commands; Python/FastAPI on the
  backend.

## Approaches considered

| | Approach | Trade-offs | Verdict |
|---|---|---|---|
| **A** | Native SwiftUI iPad app ("Coco Display") + FastAPI Hub on the Mac ("Coco Hub") | Full control of brightness, screen-awake, continuous mic + wake word; same app runs on Mac. Two codebases; install via Xcode/TestFlight. | **Chosen** |
| B | Safari web app + Hub | One codebase, nothing to install. Safari can't set brightness or keep the mic reliably alive; wake word dies when the page sleeps; night mode not achievable. | Rejected |
| C | Everything on the iPad | Simplest deploy. A10 can't run Qwen; API keys on the device; no general Q&A. | Rejected |

## Architecture

```
┌──────────── iPad (Coco Display, SwiftUI) ────────────┐        ┌──────── Mac (Coco Hub, FastAPI) ────────┐
│ Porcupine "hey coco"  →  Apple Speech (STT)          │  text  │ Intent pipeline:                         │
│ Screens: Idle/Clock, Timers, Answer, Picture,        │ ─────► │   1. rules engine (deterministic)        │
│   Sound, YouTube player, Now Playing, Night          │        │   2. Qwen via Ollama (intent fallback +  │
│ TTS (AVSpeechSynthesizer)                            │ ◄───── │      general Q&A)                        │
│ Local alarm scheduling (backup for Hub timers)       │ action │ Services: timers (authoritative store),  │
│ Brightness / night mode / audio ducking              │  + WS  │   weather (Open-Meteo), Wikimedia,       │
│ Spotify iOS app (plays audio)                        │ events │   sound library, YouTube search,         │
└──────────────────────────────────────────────────────┘        │   Spotify Web API (Connect control)      │
                                                                │ Settings (location, night schedule, keys)│
                                                                └──────────────────────────────────────────┘
```

### Coco Hub (Mac)

- Python + FastAPI, run as a launchd agent so it survives reboots; Makefile targets
  for run / test / install-service / logs.
- **Request flow:** Display POSTs a transcript → Hub returns an **Action**: `say`
  (text to speak), plus an optional `view` (which screen + payload) and optional
  side effects already performed (timer created, Spotify started).
- **Push channel:** WebSocket from Hub → Display for timer ticks/firing, Spotify
  now-playing changes, and settings changes.
- **Intent pipeline:** rules engine first (regex/grammar per intent: timers, weather,
  pictures, sounds, YouTube, Spotify, media control, night/wake, time/date). If no
  rule matches, Qwen is asked to either (a) emit a structured intent from the same
  closed set, or (b) produce a short general answer (≤ 2–3 sentences). If Qwen is
  off or unreachable, unmatched requests get "Sorry, I can't do that yet."
- **Qwen:** via Ollama; default model is a Qwen 8B-class instruct model
  (configurable). Optional: Hub runs fine with it disabled.
- **Timers:** stored in SQLite on the Hub (authoritative), keyed by name; end time
  is absolute so restarts don't drift.
- **Weather:** Open-Meteo, home lat/lon in settings, cached ~10 min.
- **Pictures:** Wikimedia Commons search API, filter to images with usable licences
  and reasonable resolution; return the top few.
- **Sounds:** curated clip library (CC0 / CC-BY from Freesound & Wikimedia), stored
  in the repo or a Hub data dir with a manifest (name, aliases, category
  animal/vehicle, image, attribution). Served to the Display over HTTP.
- **YouTube:** Data API v3 search (API key on Hub), returns video id; Display plays
  via embedded player.
- **Spotify:** Web API with OAuth (owner's Premium account) on the Hub; search, then
  start playback on the iPad's Spotify Connect device id. Exact handling of the
  "device not visible" case comes from the spike.
- **Lockdown hook:** all YouTube/Spotify requests pass through one Hub module so
  future content rules have a single place to live.

### Coco Display (iPad, also runs on Mac)

- SwiftUI app, iPadOS 18 target; also runs on Apple silicon Mac as "Designed for
  iPad".
- **Listening:** Porcupine runs continuously; on wake, chime + duck audio, start
  `SFSpeechRecognizer` (prefer on-device), end on silence, send transcript to Hub.
- **Screens:** Idle (clock + current temp + next ~6h forecast), Answer (spoken text
  shown large), Timers (big countdown cards; multiple), Picture, Sound (picture +
  playing clip), YouTube (full screen player), Now Playing (Spotify), Night (black).
  Large type and touch targets readable across a kitchen.
- **Timers:** mirrors Hub state over WebSocket and schedules a **local** alarm for
  each timer's end time, so alarms ring even if the Hub is unreachable. Alarm loops
  loud until "stop" or tap.
- **Night mode:** schedule from Hub settings. At bedtime: dim. After N idle minutes:
  black + minimum brightness. Wake on "hey coco" / tap / firing timer → restore.
- **Keep alive:** idle timer disabled; documented Guided Access setup.
- **Hub discovery:** Bonjour (`_coco._tcp`) with a manual host override in settings;
  clear on-screen state when the Hub is unreachable.
- **TTS:** `AVSpeechSynthesizer`, one consistent voice.

## Decided by the user

- Kitchen, whole family, no lockdown for now.
- Mac always on (Hub); iPad on the counter (Display); same network.
- Audio through the iPad's own speaker; Spotify Premium.
- Pictures = "show me a …" from Wikimedia Commons now; iCloud family photos later.
- Recipes skipped for now.
- Qwen answers general questions too (option 2).
- Approved: named multi-timers, Open-Meteo weather, YouTube via official search,
  spoken replies, scheduled night mode, FastAPI + Ollama on the Hub.
- Approach A.
- Spotify first; if the spike shows Spotify can't play reliably while Coco holds
  the screen, fall back to Apple Music (MusicKit, native playback in-app).
- A ringing alarm can be stopped with a bare "stop" — that word is only listened
  for while an alarm is ringing.
- A paid Apple Developer account is available → distribute via TestFlight.

## Builder-check calls (mine, flagged for review)

- **Hub down:** running timers still ring (local alarms on the iPad) and a timer can
  be set by touch on the Display; voice commands require the Hub. The Hub is on the
  LAN, so internet outages don't affect voice timers.
- **On-device speech on the A10:** verify in Phase 1; if on-device recognition is
  unavailable, Apple's server recognition is acceptable.
- **Mac Display is dev / substitute only**, never simultaneous with the iPad — one
  Display at a time; the Hub does not arbitrate multiple clients.
- **Wake-word engine behind a protocol**; Porcupine is the first implementation, so
  it can be swapped if its free tier or quality disappoints.
- **"Hey coco" must work while music/video plays** through the iPad speaker; use
  the iOS voice-processing audio mode (echo cancellation) and duck media on wake.

## Left to the builder

- Exact rules-engine grammar and phrase coverage (table-driven tests per intent).
- Hub ↔ Display wire format details, auth (a shared LAN token is sufficient).
- Specific Qwen model tag and prompt design.
- Clip selection for the sound library (licence-clean, with attribution file).
- Visual design of each screen within the "readable across a kitchen" bar.
