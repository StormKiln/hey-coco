# Voice test procedure (spike #6)

How to take the #6 measurements on the kitchen iPad (7th gen, A10) with the Display's
**Voice test** mode, and where to put the results (`docs/spikes/voice.md`, template at the end).

The voice test counts, while it is switched on: wake-word detections, probable false wakes,
per-turn latency, how long "hey coco" was actually listening, on-device recognition support, and
thermal state, battery and the app's CPU every minute. It is off by default. **No audio is
recorded or stored, and no transcript text is kept** (only a word count per turn).

What it cannot count is a *missed* wake word (nothing happens, so there is nothing to record).
Count tries yourself with a tally on paper and compare with the detections counter.

## Before you start

1. Wake word set up: the trained `display/WakeWord/hey_coco.onnx` in the build, see
   `docs/wake-word.md`. The bottom-left "Wake word off" hint must **not** be showing.
2. Coco Hub running on the Mac and the iPad connected (no banner at the top).
3. Install the build on the iPad (TestFlight, or Xcode → Run on the device).
4. iPad on its usual spot in the kitchen, volume at your normal level, Coco in front (the test
   only runs while the app is in the foreground; leave the screen on).
5. A tape measure or known distances (floor tiles, worktop lengths), and paper for tallies.

## Using Voice test

- Open Diagnostics: tap the idle clock 5 times (or Settings → Diagnostics…).
- **Voice test → Start voice test.** The counters update live. You can close Diagnostics (Done):
  the test keeps running in the background of the app until you stop it.
- **Stop voice test**, then **Copy report** (to the clipboard) or **Share report…** (AirDrop it to
  the Mac, Notes, Mail). Starting again clears the previous run, so copy the report first.
- One run per block below keeps the numbers apart. Name each report in `voice.md` by its block.

Reading the report:

- *Probable false wake*: a wake-word detection whose turn ended with nothing heard, a recognizer
  error, or a cancel before any words. During the distance blocks (where you only ever say
  "hey coco, …") a probable false wake is more likely a detection where the request was not
  heard; during the quiet/false-wake block any detection at all is a false wake.
- *Speech end → final transcript*: from the last partial transcript (about when you stopped
  talking) to the final transcript. It includes the recognizer's end-of-speech wait (1.2 s of
  silence), so the "felt" latency is roughly this number.
- *Final transcript → Hub reply*: network + Hub time. "What time is it" is handled by rules and
  should be well under a second.
- *Armed*: share of the run the wake word was listening. It is paused during each conversation
  and while an alarm rings; *Restarting*/*start failures* above zero are worth noting.

## Block A: on-device recognition (1 minute)

1. Start a voice test, read **On-device recognition** (Supported / Not supported), stop.
2. Note it in the template. If "Not supported", every turn goes to Apple's server: note that the
   latency below includes that.

## Block B: distance, quiet kitchen (~10 minutes)

Quiet kitchen, nothing playing. Start a voice test. At each distance, facing the iPad, normal
speaking voice, say **"hey coco, what time is it"** 10 times, waiting for the reply each time.
Tally your tries. Distances: **1 m, 2 m, 3 m, 4 m** (and across the room if larger).

Write down per distance: tries, detections (watch the counter rise), requests understood.
Then do 4 m once more **facing away** from the iPad (5 tries). Stop and copy the report: its
latency medians are the "short command" latency for #6.

## Block C: with music playing (~15 minutes)

Same as block B at **2 m** and **3.5 m**, 10 tries each, while music plays **from the iPad's own
speaker** (that is what echo cancellation has to cancel):

1. **YouTube in Coco**: ask Coco for a music video on YouTube, at your normal listening volume.
   One run at ~50 % volume, one at ~75 %.
2. **Spotify on the iPad**: start a playlist in the Spotify app, switch back to Coco (Spotify keeps
   playing; Coco mixes with it). Same two volumes.

One voice test per source (start before, stop and copy after). Note how loud the music was and
whether it ducked on wake and came back after the reply.

## Block D: false wakes (1–3 hours, normal kitchen noise)

Start a voice test and **don't talk to Coco**. Cook, talk, run the tap, have the radio/TV on as
usual. Every detection in this block is a false wake: the report lists their times, so you can
match them to what was happening (note anything notable with the time). Ideally run it over an
evening. Stop and copy the report.

## Block E: 1-hour continuous listening (CPU, battery, thermal)

1. Charge the iPad, then **unplug it** (battery drain only shows unplugged).
2. Start a voice test, leave Coco on the idle screen for **60 minutes** with the screen on. Ask it
   something a few times during the hour (e.g. every 15 minutes) so the run includes turns.
3. Stop and copy the report. It has a sample per minute (thermal, battery, app CPU %) and the
   averages; feel the back of the iPad at the end and note if it was warm.
4. Optional, for a second CPU number: Xcode → Debug Navigator → CPU while running from Xcode.

Blocks D and E can be the same run if you are unplugged and the kitchen is in normal use.

## Results template for `docs/spikes/voice.md`

Copy this into `docs/spikes/voice.md`, fill it in, and paste each block's report under it.

```markdown
# Spike #6: wake word + on-device speech on the A10 iPad

Date: YYYY-MM-DD · iPad: <model (identifier), iPadOS x.y> · App build: <version (build)>
openWakeWord: hey_coco.onnx <sha256 prefix>, threshold 0.5, smoothing 2, cooldown 2 s

## Verdict

- Wake word across the kitchen (3–4 m): <works / marginal / doesn't>
- With music from the iPad speaker: <...>
- False wakes: <n per hour>, acceptable? <yes/no>
- On-device recognition: <supported / not supported>; short-command latency <x s>
- 1 h continuous listening: CPU <avg %>, battery <from → to %>, thermal <max state>
- Follow-ups: <issues to open, settings to change>

## A. On-device recognition

`supportsOnDeviceRecognition` (en-US or device locale): <yes/no>

## B. Distance (quiet)

| Distance | Tries | Detected | Understood | Notes |
|---|---|---|---|---|
| 1 m | 10 | | | |
| 2 m | 10 | | | |
| 3 m | 10 | | | |
| 4 m | 10 | | | |
| 4 m facing away | 5 | | | |

Latency (from the report): speech end → final transcript median <x>, max <x>;
final transcript → Hub reply median <x>, max <x>.

<details><summary>Report</summary>

(paste)

</details>

## C. With music from the iPad speaker

| Source | Volume | Distance | Tries | Detected | Understood | Ducked and came back? |
|---|---|---|---|---|---|---|
| YouTube (Coco) | ~50 % | 2 m | 10 | | | |
| YouTube (Coco) | ~50 % | 3.5 m | 10 | | | |
| YouTube (Coco) | ~75 % | 2 m | 10 | | | |
| YouTube (Coco) | ~75 % | 3.5 m | 10 | | | |
| Spotify app | ~50 % | 2 m | 10 | | | |
| Spotify app | ~50 % | 3.5 m | 10 | | | |
| Spotify app | ~75 % | 2 m | 10 | | | |
| Spotify app | ~75 % | 3.5 m | 10 | | | |

<details><summary>Reports</summary>

(paste)

</details>

## D. False wakes

Duration: <h m> · Conditions: <cooking, TV, radio, conversation...>
False wakes: <n> (<n per hour>) · Times and what was happening:

- HH:MM:SS — <...>

<details><summary>Report</summary>

(paste)

</details>

## E. 1-hour continuous listening

Unplugged: <yes/no> · Turns during the hour: <n>
Battery: <start %> → <end %> · App CPU: average <x %>, max <x %> · Thermal: max <state>
Armed: <x %> · Wake word restarts: <n> · Warm to the touch: <yes/no>

<details><summary>Report</summary>

(paste)

</details>
```
