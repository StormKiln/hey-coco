# hey-coco Roadmap

**Spec:** [docs/superpowers/specs/2026-11-14-hey-coco-design.md](../specs/2026-11-14-hey-coco-design.md)

This roadmap splits the approved design into phases. Each phase is a GitHub epic (label `epic`) with one sub-issue per task. A detailed, test-first implementation plan for a phase is written when that phase starts, in this directory.

Phase 0 spikes are throwaway: their output is a written finding, not code to keep.

## Phase 0: Foundations & de-risking spikes

Repo layout, CI, Apple distribution, and throwaway spikes that settle the three risky unknowns (Spotify on iPad, on-device speech + wake word on the A10, Qwen latency/reliability) before building on them.

**Done when:** CI green on an empty Hub and Display; written findings for each spike committed under docs/spikes/; Spotify-vs-Apple-Music decision recorded.

### Repo scaffolding, Makefile, and CI

Lay out the monorepo and the commands everyone uses.

- [ ] Layout: `hub/` (Python/FastAPI, uv-managed), `display/` (Xcode project, SwiftUI), `docs/`, `assets/`.
- [ ] Top-level `Makefile` with `hub-run`, `hub-test`, `hub-lint`, `display-build`, `display-test`, `help`.
- [ ] GitHub Actions: hub job (ruff + pytest) on ubuntu; display job (`xcodebuild test`, iPad simulator) on macos.
- [ ] CI green on a trivial test in each half.
- [ ] `CLAUDE.md` / README describe layout and Make targets.

### Apple Developer setup and TestFlight pipeline

Get a signed build of an empty Display app onto the kitchen iPad through TestFlight.

- [ ] Bundle id + App Store Connect record for Coco Display.
- [ ] `make display-testflight` archives and uploads (fastlane or xcodebuild + altool/notarytool equivalent).
- [ ] Empty app installed on the iPad 7th gen (iPadOS 18.7) via TestFlight.
- [ ] Secrets (API key / cert) documented, not committed.

Depends on: Repo scaffolding, Makefile, and CI

### Spike: Spotify playback on iPad while Coco holds the screen

Throwaway test of whether the Hub can reliably start Spotify on the iPad's own speaker while Coco is the foreground app under Guided Access.

- [ ] Hub-side script: Web API OAuth (Premium account), list devices, start playback on the iPad device id.
- [ ] Measured: does the iPad stay in the device list after 1h / overnight idle with Coco foreground? Can playback be started cold?
- [ ] Tested with Spotify iOS SDK App Remote as an alternative wake path.
- [ ] Findings in `docs/spikes/spotify.md` with a go / fallback-to-Apple-Music decision.

### Spike: wake word + on-device speech on the A10 iPad

Throwaway app proving "hey coco" + speech recognition work on the actual kitchen iPad, including while audio plays.

- [ ] "hey coco" openWakeWord model trained (`make wakeword-train`) and committed.
- [ ] openWakeWord detects "hey coco" from across a kitchen (~3–4 m); false-wake rate noted over a few hours of normal noise.
- [ ] `SFSpeechRecognizer.supportsOnDeviceRecognition` result recorded for the A10; latency for a short command measured.
- [ ] Wake word tested while music plays through the iPad speaker with `AVAudioSession` voice-processing (echo cancellation) on.
- [ ] CPU / battery / thermal over a 1h continuous-listen run recorded.
- [ ] Findings in `docs/spikes/voice.md`.

### Spike: Qwen via Ollama on the Mac

Pick the default Qwen model and confirm it is fast and reliable enough for intent fallback and short answers.

- [ ] 2–3 Qwen instruct sizes compared on the MacBook for latency (first token, full answer) and memory.
- [ ] Closed-set JSON intent extraction tested on ~40 sample phrasings; valid-JSON and correct-intent rates recorded.
- [ ] Short general answers sampled for length and obvious errors.
- [ ] Default model tag + prompt approach in `docs/spikes/qwen.md`.

## Phase 1: Hub core

A running Coco Hub on the Mac that understands time, timers, and weather from text, with the Display protocol defined.

**Done when:** `curl` a transcript like "set a pasta timer for 10 minutes" and get the right Action back; timers persist across Hub restarts; Hub starts at login via launchd.

### Hub service skeleton, settings, and launchd install

FastAPI app the Mac runs all the time.

- [ ] FastAPI app with `/healthz`; settings (home lat/lon, timezone, night schedule, API keys) from a config file + env, never committed.
- [ ] SQLite storage with migrations.
- [ ] Shared LAN token auth on all non-health endpoints.
- [ ] Bonjour advertisement as `_coco._tcp`.
- [ ] `make hub-install-service` / `hub-uninstall-service` / `hub-logs` manage a launchd agent; Hub restarts after reboot.

### Display protocol: utterance → Action, and event stream

The contract between Hub and Display, versioned and shared.

- [ ] `POST /v1/utterance {text, context}` → `Action {say, view?, effects?}`.
- [ ] `GET /v1/idle` returns clock-relevant data + current temp + next ~6h forecast.
- [ ] `WS /v1/events` pushes timer created/updated/fired/cancelled, now-playing, and settings changes.
- [ ] JSON Schema (or OpenAPI) committed and used to generate or check the Swift models.
- [ ] Contract tests for every view type.

Depends on: Hub service skeleton, settings, and launchd install

### Rules engine framework

Deterministic intent matching that everything else plugs into.

- [ ] Intent registry: each intent declares patterns + a handler returning an Action.
- [ ] Normalisation (numbers as words, "a minute and a half", filler words).
- [ ] Table-driven test file per intent (phrase → intent + slots).
- [ ] Unmatched text returns a typed `NoMatch` so the Qwen fallback (Phase 4) can slot in.
- [ ] Time and date intents ("what time is it", "what's the date") as the first intents.

Depends on: Display protocol: utterance → Action, and event stream

### Timers service and intents

Multiple named timers, Hub-authoritative.

- [ ] Create (`set a pasta timer for 10 minutes`, unnamed timers get default names), query (`how long on the pasta`), list, cancel (by name / all), add time.
- [ ] Absolute end times stored in SQLite; survive Hub restart without drift.
- [ ] Fired events on the WS stream; Hub marks timers rung/dismissed.
- [ ] Ambiguity handled (two timers, "cancel the timer" → asks which).
- [ ] Table-driven phrase tests + service tests with a fake clock.

Depends on: Rules engine framework

### Weather service and intents

Open-Meteo weather for home, for both the idle screen and questions.

- [ ] Open-Meteo client, home lat/lon from settings, ~10 min cache, graceful failure when offline.
- [ ] Intents: current weather, "tomorrow", "will it rain this afternoon", "what's the high today".
- [ ] `/v1/idle` forecast filled from this service.
- [ ] Recorded-response tests (no live network in CI).

Depends on: Rules engine framework

## Phase 2: Display core (iPad app)

The iPad app shows the idle clock and forecast, takes tap-to-talk commands, speaks answers, and runs timers with alarms that ring even if the Hub is down.

**Done when:** On the kitchen iPad via TestFlight: tap, say "set an egg timer for 3 minutes", see the countdown, hear the alarm, tap to dismiss — and the alarm still rings if the Hub is stopped mid-timer.

### Display app shell and Hub client

SwiftUI app structure and connection to the Hub.

- [ ] App shell with a screen router driven by Action `view`.
- [ ] Hub discovery via Bonjour with manual host override + token in settings.
- [ ] REST client for `/v1/utterance` and `/v1/idle`; WebSocket client with reconnect/backoff.
- [ ] Clear "can't reach Coco Hub" state; app never stuck on a blank screen.
- [ ] Idle timer disabled (screen stays on).

Depends on: Display protocol: utterance → Action, and event stream

### Idle screen: clock and next-hours forecast

What the kitchen sees most of the day.

- [ ] Large clock readable across the kitchen; date.
- [ ] Current temp + conditions + next ~6 hours from `/v1/idle`, refreshed periodically.
- [ ] Clock keeps working when the Hub or internet is down (forecast shows stale/unavailable state).
- [ ] Leaves a slot for a future photo slideshow (no implementation).

Depends on: Display app shell and Hub client

### Tap-to-talk, speech recognition, and spoken replies

The full request loop without the wake word.

- [ ] Tap mic → listening UI → `SFSpeechRecognizer` (on-device preferred, server fallback allowed) → end on silence.
- [ ] Transcript sent to Hub; Action rendered; `say` spoken with `AVSpeechSynthesizer` in one consistent voice.
- [ ] Answer screen shows the reply large, returns to idle after a timeout.
- [ ] Mic / speech permission flows handled.

Depends on: Display app shell and Hub client

### Timers screen, local alarms, and touch timers

Timers that can't fail silently.

- [ ] Timer cards (name + big countdown) mirrored from WS events; multiple timers.
- [ ] Each timer's end time scheduled locally on the iPad so it rings even if the Hub is unreachable.
- [ ] Alarm loops loudly until tap (voice "stop" lands in Phase 3); firing timer wakes any screen state.
- [ ] Touch UI to create a timer (preset durations) that works with the Hub down and syncs when it's back.
- [ ] Tests: Hub stopped mid-timer still rings; app relaunch restores timers.

Depends on: Display app shell and Hub client; Timers service and intents

### Kitchen kiosk setup guide

How to set the iPad up as an always-on kitchen device.

- [ ] `docs/kitchen-setup.md`: Guided Access, auto-lock, power, volume, notifications off, TestFlight install.
- [ ] Verified on the kitchen iPad.

Depends on: Apple Developer setup and TestFlight pipeline

## Phase 3: Voice: wake word and night mode

Hands-free "hey coco" all day, a bare "stop" for ringing alarms, and a dark screen at night.

**Done when:** From across the kitchen, "hey coco, set a 5 minute timer" works with music playing; "stop" silences the alarm; at night the screen dims then goes black and "hey coco" brings it back.

### Wake-word engine protocol with openWakeWord implementation

Swappable wake-word detection.

- [ ] `WakeWordEngine` protocol; `OpenWakeWordEngine` using the trained "hey coco" model (iOS + macOS).
- [ ] No key or account: the models are committed (replaced Porcupine, now a paid trial).
- [ ] Fake engine for tests.

Depends on: Spike: wake word + on-device speech on the A10 iPad; Tap-to-talk, speech recognition, and spoken replies

### Listening state machine, echo cancellation, and ducking

One owner of the microphone and audio session.

- [ ] States: idle → woken (chime) → listening → thinking → speaking → idle; unit-tested transitions.
- [ ] `AVAudioSession` voice-processing mode so "hey coco" works while Coco's own media plays.
- [ ] Media (YouTube/Spotify/sounds) ducked while listening and speaking, restored after.
- [ ] Recovers if the audio session is interrupted.

Depends on: Wake-word engine protocol with openWakeWord implementation

### Bare "stop" while an alarm rings

Messy-hands dismissal.

- [ ] While (and only while) an alarm rings, listen for "stop" without the wake word.
- [ ] "stop" is not acted on at any other time.
- [ ] "hey coco, stop" also works.

Depends on: Listening state machine, echo cancellation, and ducking; Timers screen, local alarms, and touch timers

### Night mode

Dim, then dark, on a schedule.

- [ ] Schedule from Hub settings (e.g. 21:30–06:30).
- [ ] At bedtime: dim. After N idle minutes: black UI + minimum `UIScreen.brightness`.
- [ ] Wake on "hey coco", tap, or firing timer → restore brightness; return to dark after idle.
- [ ] A tap always wakes it — never stuck black.
- [ ] Restores normal brightness at the end of the night window.

Depends on: Listening state machine, echo cancellation, and ducking; Idle screen: clock and next-hours forecast

### Run Coco Display on the MacBook

Same app as a dev / substitute display.

- [ ] "Designed for iPad" build runs on the Apple silicon Mac with the macOS wake-word model.
- [ ] Brightness / Guided Access behaviour degrades gracefully on Mac.
- [ ] `make display-run-mac` documented.

Depends on: Wake-word engine protocol with openWakeWord implementation

## Phase 4: Qwen fallback and general questions

When rules don't match, Qwen maps odd phrasings to known commands or answers general questions briefly.

**Done when:** "can you give me ten minutes for the eggs" sets a timer; "how far away is the moon" gets a short spoken answer; with Ollama stopped, both degrade to a polite "I can't do that yet".

### Ollama client and health in the Hub

Optional local LLM, off-switchable.

- [ ] Ollama client with model tag from settings (default from the Qwen spike).
- [ ] Health check; Hub runs normally when Ollama is off or `qwen.enabled=false`.
- [ ] Timeouts so a slow model never leaves the Display hanging (spoken "one moment" if > ~1.5s).

Depends on: Rules engine framework; Spike: Qwen via Ollama on the Mac

### Qwen intent fallback (closed set)

Odd phrasings → existing intents.

- [ ] On `NoMatch`, Qwen asked for JSON intent from the registry's closed set (with slot schemas).
- [ ] Output validated against the schema; invalid → treat as general question or "sorry".
- [ ] Never invents an intent the registry doesn't have.

Depends on: Ollama client and health in the Hub

### General Q&A with short answers

Google-Home-style quick answers.

- [ ] Non-command questions answered in ≤ 2–3 sentences, plain spoken style.
- [ ] Answer screen shows the text; spoken via TTS.
- [ ] Clear "I'm not sure" behaviour instead of rambling.

Depends on: Qwen intent fallback (closed set)

### Utterance evaluation set

Know when rules or prompts regress.

- [ ] ~150 real-family-style utterances with expected intent/slots or "general".
- [ ] `make hub-eval` reports rules-only and rules+Qwen accuracy and latency.
- [ ] Runs locally (not in CI, since it needs Ollama).

Depends on: General Q&A with short answers

## Phase 5: Pictures and sounds

"Show me a giraffe" and "what does a cow say" work.

**Done when:** Pictures from Wikimedia Commons display full screen; ~50 animal and vehicle sounds play with matching pictures; attribution recorded.

### Wikimedia Commons picture search

"show me a …" pictures.

- [ ] Commons search API client; filter to bitmap images with usable licences and adequate resolution.
- [ ] Intent: "show me a fire truck", "what does an axolotl look like".
- [ ] Recorded-response tests; graceful "couldn't find one".

Depends on: Rules engine framework

### Picture screen

Display side of pictures.

- [ ] Full-screen image with caption; swipe/"next one" for the next result.
- [ ] Returns to idle after a timeout.

Depends on: Wikimedia Commons picture search; Display app shell and Hub client

### Curated animal and vehicle sound library

Reliable, licence-clean clips.

- [ ] ~50 clips (animals + vehicles), CC0/CC-BY from Freesound/Wikimedia, normalised loudness, short.
- [ ] Manifest: name, aliases, category, image, source URL, licence, author.
- [ ] `ATTRIBUTION.md` generated from the manifest.
- [ ] Hub serves clips + images over HTTP.

Depends on: Hub service skeleton, settings, and launchd install

### Sound intents and Sound screen

"what does a cow say" / "play a fire truck sound".

- [ ] Intents match names + aliases ("moo", "choo choo", "what sound does a …").
- [ ] Display shows the picture and plays the clip; "again" replays.
- [ ] Unknown animal → short apology + suggestion.

Depends on: Curated animal and vehicle sound library; Display app shell and Hub client

## Phase 6: Media: YouTube and Spotify

Voice-controlled YouTube and Spotify (or Apple Music fallback) through the iPad speaker, through a single media-policy choke point.

**Done when:** "play Bluey on YouTube", "play Taylor Swift", pause/next/stop/volume all work by voice; media ducks while Coco listens or an alarm rings.

### Media policy module (lockdown hook)

One place all YouTube/music requests pass through.

- [ ] Every media request goes through `media_policy.check(request) -> allow/deny`.
- [ ] v1 policy allows everything; tests prove there is no bypass path.
- [ ] Documented as where future parental controls go.

Depends on: Rules engine framework

### YouTube search and intents

"play Bluey on YouTube".

- [ ] YouTube Data API v3 search (API key on Hub), top embeddable result.
- [ ] Search cache + daily quota guard with a spoken message when exhausted.
- [ ] Intents: play X on YouTube, pause, resume, next, stop.

Depends on: Media policy module (lockdown hook)

### YouTube player screen

Full-screen playback on the iPad.

- [ ] Embedded player full screen; voice controls via Hub Actions/events.
- [ ] Ducks during listening; pauses for a firing alarm.
- [ ] Returns to idle when finished.

Depends on: YouTube search and intents; Listening state machine, echo cancellation, and ducking

### Spotify control from the Hub

Music via Spotify Connect on the iPad (per spike).

- [ ] Web API OAuth for the Premium account; refresh tokens stored on the Hub only.
- [ ] Intents: play artist/album/playlist/song, pause, resume, skip, volume, "what's playing".
- [ ] Targets the iPad's Spotify device, using the wake path found in the spike.
- [ ] Now-playing pushed on the WS stream.

Depends on: Media policy module (lockdown hook); Spike: Spotify playback on iPad while Coco holds the screen

### Now Playing screen

What's playing, from across the room.

- [ ] Artwork, title, artist; tap controls.
- [ ] Compact now-playing strip on the idle screen while music plays.

Depends on: Spotify control from the Hub; Idle screen: clock and next-hours forecast

### Apple Music fallback (only if the Spotify spike fails)

Native playback via MusicKit if Spotify can't work reliably.

- [ ] Only started if `docs/spikes/spotify.md` recommends the fallback; otherwise close as not needed.
- [ ] MusicKit playback in the Display app; same intents as Spotify behind a `MusicProvider` interface.

Depends on: Spike: Spotify playback on iPad while Coco holds the screen

## Phase 7: Hardening and kitchen rollout

Coco replaces the Google Home for real.

**Done when:** Kitchen iPad on TestFlight build, Hub on the Mac, one week of family use with the anti-goals checklist passing.

### Resilience: Hub down, internet down, restarts

Prove the anti-goals.

- [ ] Scripted checks: Hub killed mid-timer (alarm still rings), internet off (clock + timers + voice timers work), Mac reboot (Hub returns, Display reconnects), iPad relaunch (timers restored).
- [ ] Any failure fixed or filed.

Depends on: Timers screen, local alarms, and touch timers; Night mode

### Diagnostics screen and Hub logs

See what Coco heard and did.

- [ ] Hidden diagnostics screen: Hub connection, last transcripts + matched intent, wake-word stats, versions.
- [ ] Hub structured logs with rotation; `make hub-logs`.

Depends on: Display app shell and Hub client

### Kitchen install and one-week soak

Real-world shakedown.

- [ ] Installed per `docs/kitchen-setup.md`.
- [ ] One week of family use; issues found are filed with the `soak` label.
- [ ] Google Home retired.

Depends on: Resilience: Hub down, internet down, restarts; Kitchen kiosk setup guide

## Later (not scheduled)

- Recipes (source + step-by-step voice walkthrough with timers from steps)
- iCloud family photos on the idle screen
- Content lockdown / parental controls (via the media policy module)
- Clock-time wake-up alarms, reminders, shopping lists, calendars, smart home

