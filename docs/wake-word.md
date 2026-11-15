# Wake word, listening loop and bare "stop"

Issues #21, #22, #23. Code: `display/CocoKit/Sources/CocoKit/Voice/` (logic, tested with
`make kit-test`), `display/App/Voice/OpenWakeWordEngine.swift` and
`OnnxWakeWordModelRunner.swift` (the ONNX Runtime adapter), `display/WakeWord/` (the models) and
`display/tools/wakeword/` (training).

The engine is [openWakeWord](https://github.com/dscripka/openWakeWord) (Apache-2.0) on ONNX
Runtime. It replaced Picovoice Porcupine, which became a 7-day trial that needs a company
registration. Nothing about it is secret: no key, no account, every model is committed.

## The models (`display/WakeWord/`)

| File | What | Size | Source |
|---|---|---|---|
| `melspectrogram.onnx` | audio → mel spectrogram | 1.1 MB | openWakeWord v0.5.1 release, Apache-2.0 |
| `embedding_model.onnx` | mel → speech embeddings (Google's `speech_embedding`) | 1.3 MB | openWakeWord v0.5.1 release, Apache-2.0 |
| `hey_coco.onnx` | embeddings → "hey coco" score | ~200 KB | **trained here**: `make wakeword-train` |

Checksums and licence notes are in `display/WakeWord/README.md`. XcodeGen bundles the folder.
Until `hey_coco.onnx` exists the app builds and runs exactly as before: tap-to-talk works and a
faint "Wake word off" shows bottom-left. The trained model is committed (it holds no secret), so
CI and TestFlight build with the wake word from any checkout.

## Training "hey coco" (`make wakeword-train`)

The owner runs this once on an Apple silicon Mac, then commits `display/WakeWord/hey_coco.onnx`:

```sh
make wakeword-train SMOKE=1   # optional: a few minutes, checks the toolchain, writes nothing
make wakeword-train           # the real run: downloads ~19 GB, runs for hours
git add display/WakeWord/hey_coco.onnx
```

It follows openWakeWord's `automatic_model_training` notebook, pinned to openWakeWord commit
`368c037` and piper-sample-generator v2.0.0 (`display/tools/wakeword/train.py`, its own uv
environment in `display/tools/wakeword/`, Python 3.11, never the Hub's):

1. **Generate clips:** 10,000 synthetic "hey coco" (and 2,000 for validation) from the Piper
   `en_US-libritts_r-medium` voice with many speakers, plus as many adversarial near-misses
   ("hey cocoa", "coco", "hey coach", "okay coco", ... in `NEGATIVE_PHRASES`).
2. **Augment:** room impulse responses (MIT) and AudioSet background noise, then openWakeWord
   features for every clip.
3. **Train** 50,000 steps against ~2,000 hours of precomputed negative features (ACAV100M), with
   early stopping on ~11 hours of validation audio's false-positive rate. Exports
   `hey_coco.onnx`, checks it takes the [1, 16, 96] input the app feeds it, and copies it into
   `display/WakeWord/`.

Every step resumes: downloads continue, and existing clips and features are reused, so after an
interruption just run it again. Data goes to `WAKEWORD_WORK` (default
`~/Library/Caches/hey-coco/wakeword`; delete it afterwards to get the disk back).

| Download | Size |
|---|---|
| ACAV100M negative features | 17.3 GB |
| AudioSet background (2 of 38 shards) | ~1.4 GB |
| Piper voice | 204 MB |
| Validation features | 185 MB |
| Room impulse responses | 8 MB |
| uv environment (torch, Piper, ...) | ~1.1 GB on disk |

Disk: about 25 GB free (downloads, the decoded background audio and ~20,000 clips with their
features). Time, an estimate for an M-series Mac on CPU (the smoke run measured the per-clip
costs, not the full run): clip generation 1-2 h, augmentation and features ~1 h, training 1-3 h,
so **3-6 hours**, plus the 19 GB download.

**If it fails on macOS** (openWakeWord only supports training on Linux; this works around its two
known issues, macOS's `spawn` multiprocessing and a missing `pkg_resources`): use openWakeWord's
official Colab notebook,
<https://colab.research.google.com/drive/1q1oe2zOyZp7UsB3jJiQ1IFn8z5YfjwEb?usp=sharing>, with the phrase
"hey coco", download the `.onnx` it produces, save it as `display/WakeWord/hey_coco.onnx` and
commit it. It must take a [1, 16, 96] input (16 embeddings), which the notebook's default
settings produce for a short phrase.

**Licence of the trained model.** The training code and the two feature models are Apache-2.0.
openWakeWord publishes its own trained wake-word models as CC BY-NC-SA 4.0 because their negative
data (ACAV100M, AudioSet) has unknown or restrictive licensing; `hey_coco.onnx` is trained on the
same data, so treat it the same way: fine for this personal, non-commercial app, not for anything
sold.

## Tuning

`WakeWordDetector` (CocoKit) turns the per-80 ms scores into detections: the mean of the last 2
scores must reach **0.5** (openWakeWord's recommended threshold), and after a detection nothing
fires for **2 s**. The engine also needs 1.28 s of audio after every (re)start before it can fire,
so it never re-detects the "hey coco" that started a conversation. Too many false wakes: raise
the threshold (0.6-0.7) or the smoothing; missed wakes from across the kitchen: lower the
threshold, or retrain with more clips (`ARGS="--samples 20000"`).

## Design

- **`WakeWordEngine`** protocol (`start(onDetected:)`, `stop()`, `isRunning`);
  `OpenWakeWordEngine` (app target, because ONNX Runtime is a ~50 MB binary package CocoKit stays
  free of, so `swift test` on macOS never needs it) and `FakeWakeWordEngine` (tests).
  The pipeline is CocoKit's `OpenWakeWordRecognizer` behind a `WakeWordModelRunner` protocol
  (tests use a fake): 1280-sample (80 ms) chunks; the mel model sees each chunk plus the
  previous 480 samples, its output transformed `x / 10 + 2`; a 76-frame mel window gives one
  96-value embedding per chunk; the last 16 embeddings go to the classifier. This mirrors
  openWakeWord's `AudioFeatures._streaming_features` and `Model.predict`, and was checked
  against them: the same audio through the Python reference and this pipeline (with
  openWakeWord's `hey_jarvis` model) gives scores equal to within 1e-6.
  One deliberate difference: openWakeWord pre-fills its embedding buffer with noise; this one
  waits for 16 real embeddings instead.
  The engine is fed by `PCMFrameMicrophone` (16 kHz mono Int16, 1280-sample frames, voice
  processing on), so nothing but `AudioSessionCoordinator` touches `AVAudioSession`. The models
  load on the first start; a load failure is a failed start (retried with backoff).
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
- **Bare "stop" (#23)**: `StopWordListener`. A wake-word model for "stop" would be another
  training run, and a one-syllable word false-triggers easily, so the listener is
  `RecognizerStopWordListener`: back-to-back ~4 s
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
- "hey coco" from across the kitchen (3-4 m), by everyone in the family: the model only ever
  heard synthetic voices.
- False wakes over an evening of kitchen noise / TV (and "coco", "cocoa", "hey Google"); tune
  the threshold (see Tuning) if needed.
- CPU and thermals on the iPad 7th gen (A10) with the wake word always running. On an M-series
  Mac the three models take ~2 ms per 80 ms chunk; the A10 is perhaps 5-10x slower, i.e. some
  10-25% of one core, not yet measured.
- Recovery: Siri, a FaceTime call, plugging in headphones, backgrounding the app.
- Mac ("Designed for iPad"): the same models; ONNX Runtime's iOS binary is used there too.
