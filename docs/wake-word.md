# Wake word, listening loop and bare "stop"

Issues #21, #22, #23. Code: `display/CocoKit/Sources/CocoKit/Voice/` (logic, tested with
`make kit-test`) and `display/App/Voice/PorcupineWakeWordEngine.swift` (the Porcupine adapter).

## What the owner supplies (never committed)

The app builds and runs without any of this: tap-to-talk works and a faint "Wake word off"
shows bottom-left. To turn "hey coco" on:

1. **Picovoice AccessKey.** Sign in at <https://console.picovoice.ai>, copy the AccessKey.
2. **Keyword files.** In the console, Porcupine → train the phrase "hey coco":
   - platform **iOS** → download, rename to `hey-coco_ios.ppn`;
   - platform **macOS** (optional, for running the Display on a Mac) → `hey-coco_mac.ppn`.
   Train it for the Porcupine version pinned in `display/project.yml` (**v4.0**): keyword files
   from a different major version are rejected at start-up.
3. Put them in `display/WakeWord/` (gitignored except its README):

   ```
   display/WakeWord/PicovoiceAccessKey.txt   # the key on one line
   display/WakeWord/hey-coco_ios.ppn
   display/WakeWord/hey-coco_mac.ppn         # optional
   ```
4. `make display-build` (it regenerates the Xcode project, which picks the files up as bundle
   resources). Archive for TestFlight from the same checkout, so the files are in the build.

Why a bundled file rather than a Settings field: the keyword file has to be bundled anyway, so
both secrets travel the same way, and nobody has to type a 50-character key on the iPad. The key
ends up inside the app bundle; that is fine for a personal TestFlight build (Picovoice keys are
per-account and revocable) but would not be for a public App Store build.

On a Mac ("Designed for iPad") the macOS keyword is preferred if present, else the iOS one. Whether
Picovoice accepts either on an iOS-app-on-Mac has not been verified yet.

## Design

- **`WakeWordEngine`** protocol (`start(onDetected:)`, `stop()`, `isRunning`);
  `PorcupineWakeWordEngine` (app target, because Picovoice's Swift package is iOS-only and CocoKit
  must build for `swift test` on macOS) and `FakeWakeWordEngine` (tests).
  The adapter uses the low-level `Porcupine` class fed by `PCMFrameMicrophone` (16 kHz mono
  Int16, 512-sample frames, voice processing on), not `PorcupineManager`, so nothing but
  `AudioSessionCoordinator` touches `AVAudioSession`.
- **`AudioSessionCoordinator`** (`.shared`): the only owner of `AVAudioSession` while the loop
  runs. Standby: `.playAndRecord` + `.defaultToSpeaker` + `.mixWithOthers` (music and video keep
  playing at full volume). Conversation: adds `.duckOthers`. After a turn the session is
  deactivated with `.notifyOthersOnDeactivation` (that is what un-ducks other apps) and standby
  re-activated. Echo cancellation is voice processing on each mic engine
  (`setVoiceProcessingEnabled(true)`, with its own "other audio" ducking set to minimum), not the
  `.voiceChat` session mode, which also makes media quieter. Reports interruptions, hardware route
  changes and media-server resets. The alarm player defers to it, so a ringing alarm no longer
  switches the session to `.playback` (which would cut the mic the bare "stop" needs).
- **`VoiceLoop`** (`@Observable`): owns the wake-word engine, the bare-stop listener and the
  `ConversationController`.

  ```
  idle(armed) ─wake/tap─▶ woken (chime) ─▶ listening ─▶ thinking ─▶ speaking ─▶ idle(armed)
  ```

  Mic handoff: the standby listener (wake word, or stop word while an alarm rings) is stopped
  *before* the recognizer opens the mic and restarted as soon as the recognizer's stream ends,
  so "hey coco" while Coco is thinking or speaking barges in. `ducking` is true from wake until
  the reply has been spoken; media screens observe it via `@Environment(VoiceLoop.self)`.
  Interruptions stop the engine (and cancel a conversation) and re-arm when they end; route
  changes restart it; a failed start retries with backoff (2 s doubling to 60 s); returning to
  the foreground re-arms. `wakeWord` (`armed`, `paused`, `retrying`, `unavailable`) drives the
  "Wake word off" hint.
- **Bare "stop" (#23)**: `StopWordListener`. Porcupine's built-in keywords have no "stop" (they
  are names like "computer", "jarvis", "bumblebee"), and a custom one would be another console
  training per platform, so the listener is `RecognizerStopWordListener`: back-to-back ~4 s
  `SFSpeechRecognizer` windows (on-device when available) that run **only while an alarm
  rings**, replacing the wake word for that time. The whole utterance must be one of: stop,
  stop it, ok, okay, enough, dismiss, shut up, off, optionally after "hey coco" / "coco" (so
  "hey coco, stop" works); "don't stop" or "stop the music" do nothing. On a match: `alarm.stop`
  locally (instant silence), then `POST /v1/utterance` "stop" with `alarm_ringing: true` so the
  Hub dismisses its timers; nothing is spoken.
  While the alarm rings, "hey coco <anything else>" is not heard (the stop listener has the
  mic); tap-to-talk still works.

## On-device checks (not testable in CI)

Diagnostics → **Voice test** measures most of these (detections, probable false wakes, latency,
listening time, thermal/battery/CPU): see `docs/spikes/voice-test-procedure.md`.

- "hey coco" detected while a YouTube video / Spotify / a sound plays from the iPad speaker
  at typical volume (echo cancellation); and while Coco itself is speaking (barge-in).
- Bare "stop" heard over the alarm at full volume.
- Media ducks on wake and comes back after the reply; the alarm is not ducked to inaudible.
- False wakes over an evening of kitchen noise / TV; tune `sensitivity` (0.6) if needed.
- CPU and thermals on the iPad 7th gen (A10) with the wake word always running.
- Recovery: Siri, a FaceTime call, plugging in headphones, backgrounding the app.
- Mac ("Designed for iPad"): whether Porcupine accepts the key and keyword there.
