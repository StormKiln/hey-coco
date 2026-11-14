# Spike #7: Qwen via Ollama on the Mac

**Recommendation:** default model **`qwen3:8b`** (GGUF Q4_K_M, llama.cpp runner) with the v2
system prompt and JSON schema below, sent via `/api/chat` with `format: <schema>`,
`think: false`, `temperature: 0`, `num_ctx: 4096` and a model kept resident.
Warm latency is about 0.5 s for an intent and 0.7 s for a short answer (p90 about 0.6 s and 1.1 s).
On the 60-utterance set it gets 58/60 fully right (intent and slots) and 13/15 on a
held-out set, with 100% schema-valid JSON because decoding is grammar-constrained. Resident memory is about 5 GB.

`qwen3.5:9b` was slightly more accurate on the tuned set (60/60) but **cannot use structured
output** on this Ollama build. It also misspelled words, uses 3x the memory and has a 4.4 s cold start (details below).
Keep it as the first model to re-test if Ollama adds `format` support to the MLX runner.

## Method

- Machine: M2 Max MacBook Pro, 64 GB. Ollama 0.40.16 (already running; not restarted).
- Models (as installed, nothing pulled):
  | tag | weights | runner | params |
  |---|---|---|---|
  | `qwen3.5:4b` | safetensors nvfp4 | **mlx** | 4.5B |
  | `qwen3.5:9b` | safetensors nvfp4 | **mlx** | 9.7B |
  | `qwen3:8b` | GGUF Q4_K_M | llama.cpp | 8.2B |
- Script: `docs/spikes/qwen/spike.py` (`uv run --with httpx python docs/spikes/qwen/spike.py <model> --cold`).
  It calls streaming `/api/chat` with system prompt + utterance, `think: false`, `keep_alive: 30m`,
  `options: {temperature: 0, num_ctx: 4096, num_predict: 200}`, and `format: SCHEMA`.
  If the runner rejects `format`, it falls back to prompt-only JSON.
  Outputs are validated strictly against the schema by a hand-written validator. A lenient **repair** pass,
  which the Hub should also run, drops `null` slots, turns `"+10"` into `10`, and then validates again.
- Test sets:
  - `utterances.jsonl`: 60 items. 40 odd phrasings of commands ("can you give me ten minutes for
    the eggs", "what's the doggo noise", "chuck on some dinner party jazz"), 15 general questions and 5 refusals.
  - `utterances-heldout.jsonl`: 15 new phrasings written **after** tuning the prompt, to check for
    overfitting (v2 only).
- Scoring: an intent is correct when it is in the expected (or allowed alternative) intent.
  Slots are checked per expected key:
  - `duration_s`, `when`, `level` and `all` must match exactly;
  - `name` and `query` match loosely (normalised substring);
  - `delta` is checked by its sign;
  - a timer `name` the person never said counts as a slot error.
  "Fully right" means repaired-valid, intent correct and all slots correct.
- Two prompt versions: **v1** was the first design. **v2** fixed each failure class seen in v1 (see
  below). Both runs are kept: `results-<model>-v1.json` and `results-<model>.json` (v2).
- Warm runs: one warm-up request, then every utterance once, sequentially, at temperature 0.
  Cold: the script unloads only that model (`keep_alive: 0`), waits 2 s, then times one request.
  All three models were resident at once by the end (≈35 GB of 64 GB, no swapping).
- Latency is wall-clock time of the HTTP request, as the Hub would see it.

## Results

### v2 prompt (recommended), main set n=60

| | qwen3.5:4b | qwen3.5:9b | **qwen3:8b** |
|---|---|---|---|
| Structured output (`format`) | **rejected** ("structured output is unavailable") | **rejected** | yes |
| Valid JSON, strict | 59/60 | 59/60 | **60/60** |
| Valid JSON after repair | 60/60 | 60/60 | 60/60 |
| Correct intent, all | 58/60 | **60/60** | 58/60 |
| — commands (40) | 38 | 40 | 38 |
| — general (15) | 15 | 15 | 15 |
| — refuse → none (5) | 5 | 5 | 5 |
| Slot `duration_s` | 7/7 | 7/7 | 7/7 |
| Slot `name` (incl. invented names) | 8/10 | 10/10 | 10/10 |
| Other slots (when/query/kind/level/delta/all) | 12/16 | 17/17 | 17/17 |
| **Fully right** | 53/60 | **60/60** | 58/60 |
| **Held-out fully right** (n=15) | 8/15 | 13/15 | 13/15 |
| Intent latency p50 / p90 | 0.35 / 0.46 s | 0.43 / 0.57 s | 0.47 / 0.61 s |
| Answer latency p50 / p90 | 0.41 / 0.53 s | 0.64 / 1.03 s | 0.68 / 1.08 s |
| Max latency, all 75 requests | 0.71 s | 1.13 s | 1.39 s |
| Decode speed (median) | 80 tok/s | 66 tok/s | 58 tok/s |
| Prompt eval, warm (≈620 tok, prefix-cached) | 0.05 s | 0.09 s | 0.08 s |
| Cold first request (load + full prompt) | 2.4 s | 4.4 s | 2.5 s |
| `ollama ps` SIZE | 11–12.6 GB | 14.8–16.3 GB | **5.1 GB** |
| Runner process RSS | 3.6 GB | 6.9 GB | 5.8 GB |
| General answers > 2 sentences / > 3 | 0 / 0 | 0 / 0 | 1 / 0 |
| Obvious factual or spelling errors in answers (18) | **4** | 2 | **0** |

`ollama ps` for the MLX models reports far more than the process RSS, and the number grew between
runs. It looks like the MLX runner's own reservation, not real use; RSS is the better measure of
real memory. Either way, `qwen3:8b` is the smallest per unit of quality.

### v1 prompt (first design), main set n=60, same scoring

| | qwen3.5:4b | qwen3.5:9b | qwen3:8b |
|---|---|---|---|
| Valid JSON strict / after repair | 56 / 60 | 58 / 60 | 60 / 60 |
| Correct intent | 57/60 | 58/60 | 54/60 |
| Fully right | 54/60 | 55/60 | 49/60 |
| Intent latency p50 / p90 | 0.36 / 0.49 s | 0.41 / 0.55 s | 0.46 / 0.65 s |
| Answer latency p50 / p90 | 0.42 / 0.63 s | 0.57 / 0.87 s | 0.60 / 1.00 s |

The v1→v2 change took `qwen3:8b` from 49 to 58 fully right. The prompt matters as much as the model.

## Notable failures

**v1 failure classes, each fixed by a v2 rule:**
- The qwen3.5 models wrote `"name": null` / `"kind": null` (schema violation). → "Never output null".
- qwen3.5:4b wrote `"delta": "+10"` as a string. → "delta is an integer".
- qwen3:8b made up timer names: "countdown", "one", "it". → explicit ban.
- qwen3:8b: "pasta needs eleven minutes" → `duration_s: 11`. → "duration_s is in SECONDS" with examples.
- qwen3:8b: "remind me in an hour and a half to take the bread out" → `none` ("I can't set reminders").
  → timer.create now covers "remind me in X".
- qwen3:8b: "how far away is the moon" → `none`. → broader general.answer description.
- All three: "what does a komodo dragon look like" → general.answer instead of picture.show;
  qwen3.5:4b: "what does a cow say" → general.answer. → "what does an X look like / say" mapped explicitly.
- "skip this one" → `none` (all three). → "media.next (skip / next song)".
- qwen3:8b: "will it be nice this weekend" → weather.rain. → "weather.rain only for rain/umbrellas/storms".
- music `kind` was missing or set to "any". → artist/album/playlist rules.

**Still failing with v2:**
- qwen3:8b: "ok keep going with the music" → media.next (should be media.resume); "skip this one" → none.
- qwen3:8b and qwen3.5:4b: "a quarter of an hour on the lasagna" → `duration_s: 1500` (should be 900).
  qwen3.5:9b got 900 but chose timer.add_time.
- "I can't hear it" (meaning louder) failed on every model: general.answer or none.
  That is a fair miss; the rules engine should own it.
- qwen3.5:4b had many held-out misses ("how are the muffins doing" was answered as chat with an
  invented "they're in the oven"; "scrap the broccoli one" → general.answer). **4B is too weak.**

**General answers (read by hand):**
- Length is fine on every model. Nothing went over 3 sentences, and qwen3:8b went over 2 once (cats purr).
- **Spelling is the weak spot of the qwen3.5 models.**
  - qwen3.5:9b: "rhythm" → "R-I-T-H-M" (and "necessary" → "N-E-S-S-A-R-Y" in v1).
  - qwen3.5:4b: "N-E-S-S-E-S-S-A-R-Y" and "R-I-T-M-Y-C".
  - qwen3:8b spelled both correctly in both runs.
- qwen3.5:4b factual errors:
  - "about 42 cups in a liter";
  - "No, penguins don't have knees" (they do).
- qwen3.5:9b gave a muddled "why is the sky blue" ("blue light travels smaller distances…
  reaches our eyes first"). It also read "176.7" out as "one seventy-six point seven".
- qwen3:8b answers were all correct. Two borderline points:
  - "4 to 5 minutes" for a runny yolk (short, but defensible);
  - extra unrequested detail (Apollo 11 date, whale weight).
- For `none`, models sometimes put a refusal sentence in `answer` ("I can't make phone calls").
  The Hub should ignore `answer` unless intent is general.answer and speak its own "Sorry, I can't do that yet."

## Recommended prompt approach

1. **Single call, single closed schema.** One `/api/chat` request returns
   `{"intent", "slots", "answer"}`. There is no separate classify step: one call handles both intents and answers,
   and the prefix-cached system prompt makes it cheap (≈0.08 s prompt eval warm).
2. Send `format: SCHEMA` (grammar-constrained on the llama.cpp runner), `think: false`,
   `temperature: 0`, `num_ctx: 4096`, `num_predict: 200`, `stream: false`. Streaming is not worth it:
   answers finish in about 1 s, and TTS needs the whole sentence anyway.
3. **Keep the model resident:** `keep_alive: -1` on every request. Send one warm-up request when the Hub starts,
   so a cold load (≈2.5 s) happens at boot and not at "hey coco".
4. **Validate, then repair, then decide** on the Hub, even with `format` on:
   - parse and check the intent enum and slot types;
   - drop null slots and coerce numeric strings;
   - on any remaining failure, treat the result as `none`.
   Re-check slot meaning in code: `duration_s` must be > 0 and ≤ 24 h, `level` must be 0–100.
   Ignore `answer` for non-general intents.
5. **Make the rules engine own the known weak spots** instead of trusting Qwen with them:
   - "how do you spell X": spell X from the transcript deterministically;
   - fractional durations ("quarter of an hour", "half an hour");
   - "keep going" / "carry on" → resume; "skip" → next;
   - "I can't hear it" → louder.
6. Phase 4 should keep both test files as an offline regression set. Add each misheard real-world phrasing
   to `utterances.jsonl`, and keep the held-out set unseen when tuning.

### Final system prompt (v2, verbatim)

```text
You are Coco, a voice assistant on a family kitchen iPad. Kids and adults talk to you. The request below was transcribed from speech and did not match any built-in command rule. Map it to exactly ONE intent from this closed list, or answer it as a general question.

Intents (slots in parentheses, ? = optional):
- timer.create (name?, duration_s) - start a countdown, including "remind me in X to Y".
- timer.query (name?) - how long is left on a timer / is it done.
- timer.cancel (name? | all) - stop or delete a running timer; all=true only when they say all/every timer.
- timer.add_time (name?, duration_s) - add more time to an existing timer.
- timer.list - which timers are running, or how many.
- time.now - the current time.
- date.today - today's date or day of the week.
- weather.current - weather or temperature outside right now.
- weather.forecast (when) - what the weather will be like later.
- weather.rain (when) - only questions about rain, umbrellas or storms.
- picture.show (query) - show a photo; also "what does an X look like".
- sound.play (name) - play the noise an animal or vehicle makes; also "what does an X say / sound like".
- youtube.play (query) - play a video from YouTube.
- music.play (query, kind) - play music on Spotify.
- media.pause, media.resume, media.next (skip / next song), media.stop - control what is playing.
- volume.set (level) - set volume to level 0-100 ("half" = 50).
- volume.change (delta) - louder or quieter; delta is an integer, +10 or -10 unless an amount is given.
- alarm.stop - silence a ringing timer alarm or beeping.
- general.answer - any question about the world you can answer: science, nature, space, animals, cooking, conversions, maths, spelling, history, geography.
- none - actions you cannot do: shopping, ordering, lists, calls, messages, lights, bookings, or anything unsafe or unclear.

Slot rules:
- Include only slots the person actually said. Never output null; leave the slot out instead.
- duration_s is in SECONDS: "45 seconds" = 45, "eleven minutes" = 660, "an hour and a half" = 5400.
- timer name is the food or thing being timed ("eggs", "bread"). Never use words like "timer", "countdown", "one" or "it" as a name; leave name out.
- when is one of: today, tonight, tomorrow, this_afternoon, this_weekend.
- sound name is the plain animal or vehicle: "doggo noise" -> "dog".
- kind is one of artist, album, playlist, song, any: a performer -> artist; a soundtrack or record -> album; a genre or mood like "jazz for dinner" -> playlist; a song title -> song.

Output rules:
- Reply with ONLY one JSON object, on one line: {"intent": "<intent>", "slots": {...}, "answer": <string or null>}.
- "answer" is null for every intent except general.answer.
- For general.answer, "slots" is {} and "answer" is spoken aloud: one or two short sentences, plain words a child understands, no lists, no markdown, no emoji. When spelling a word, spell it carefully letter by letter with dashes. If you are not sure of a fact, say so briefly.
```

The user message is just the transcript, e.g. `can you give me ten minutes for the eggs`.

### JSON schema (`format`, verbatim)

```json
{
  "type": "object",
  "properties": {
    "intent": {
      "type": "string",
      "enum": [
        "timer.create", "timer.query", "timer.cancel", "timer.add_time", "timer.list",
        "time.now", "date.today",
        "weather.current", "weather.forecast", "weather.rain",
        "picture.show", "sound.play", "youtube.play", "music.play",
        "media.pause", "media.resume", "media.next", "media.stop",
        "volume.set", "volume.change", "alarm.stop",
        "general.answer", "none"
      ]
    },
    "slots": {
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "duration_s": {"type": "integer"},
        "all": {"type": "boolean"},
        "when": {"type": "string", "enum": ["now", "today", "tonight", "tomorrow", "this_afternoon", "this_weekend"]},
        "query": {"type": "string"},
        "kind": {"type": "string", "enum": ["artist", "album", "playlist", "song", "any"]},
        "level": {"type": "integer"},
        "delta": {"type": "integer"}
      },
      "additionalProperties": false
    },
    "answer": {"type": ["string", "null"]}
  },
  "required": ["intent", "slots", "answer"],
  "additionalProperties": false
}
```

Slots are one flat object shared by every intent; the prompt says which slots apply to which intent,
and the Hub checks this. `name` is the timer name for timer.* and the sound name for sound.play.
`when` allows `now` in the schema although the prompt does not mention it; the Hub can treat
`now` as weather.current.

## Latency budget

These are measured warm on the M2 Max, and the timing starts when the Hub sends the request to Ollama:

- Rules-engine hit: ~0 ms (no Qwen).
- Qwen intent: p50 ≈ 0.5 s, p90 ≈ 0.6 s.
- Qwen general answer: p50 ≈ 0.7 s, p90 ≈ 1.1 s, max seen 1.4 s.
- Cold model load: ≈ 2.5 s (should only happen at Hub start or after an Ollama restart).

Recommendation for the Display/Hub:
- Show the "thinking" state (visual only) as soon as the transcript is sent. Don't wait for a timer.
- **Speak "one moment"** only if the Hub has not answered within **1.5 s**. With a warm model, that only
  happens for a cold load or a contended Mac.
- **Hard timeout 6 s** on the Qwen call. On timeout, say "Sorry, I can't do that yet." and log it.
- Hub health: if `/api/ps` shows the model is not loaded, send a background warm-up request.

## Risks

- **No structured output on the MLX runner.** Ollama 0.40.16 rejects `format` for the
  safetensors/MLX `qwen3.5:*` tags. If someone switches the default to a qwen3.5 tag, the Hub must
  rely on prompt-only JSON plus repair. That gave 59/60 strict-valid here, but it is not guaranteed.
  Keep the validator and repair step in any case.
- **Tuned on the test set.** The v2 prompt was written after reading the v1 failures on these same 60
  utterances, and some of its examples come from them. The held-out set (13/15 on the recommended model) is the
  more honest number: expect about 85–90% on new odd phrasings, not 97%.
- **Confidently wrong facts.** The 4B made up facts outright; the 8B was clean on 18 questions, but that is a
  small sample. Kids will ask spelling and maths questions, so spelling should go through the rules
  engine.
- **Memory and co-tenancy.** The model needs ~5–6 GB resident with `keep_alive: -1`. That is fine on the 64 GB
  M2 Max, but the design says the Hub's Mac is "newer Apple silicon". If the production Mac is smaller,
  re-check that the model stays resident and that an unload does not cause cold starts.
- **Ollama upgrades** can change runners, default context or `think` handling. Pin the tag, and keep
  `spike.py` and the two test sets as a quick re-check after upgrades.
- **Single-pass numbers.** Latencies come from one pass over 75 requests on an otherwise idle machine. They are
  not a load test. Two requests at once (e.g. Mac and iPad displays) will queue.
