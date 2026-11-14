"""Throwaway spike for #7: Qwen via Ollama as Coco's intent fallback + short answers.

Usage (from repo root):
    uv run --with httpx python docs/spikes/qwen/spike.py qwen3:8b [--cold]
    uv run --with httpx python docs/spikes/qwen/spike.py --report   # summary table

Writes docs/spikes/qwen/results-<model>.json. Not production code; Phase 4 reuses
SYSTEM_PROMPT / SCHEMA (copied into docs/spikes/qwen.md).
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

OLLAMA = "http://localhost:11434"
HERE = Path(__file__).parent
KEEP_ALIVE = "30m"

INTENTS = [
    "timer.create",
    "timer.query",
    "timer.cancel",
    "timer.add_time",
    "timer.list",
    "time.now",
    "date.today",
    "weather.current",
    "weather.forecast",
    "weather.rain",
    "picture.show",
    "sound.play",
    "youtube.play",
    "music.play",
    "media.pause",
    "media.resume",
    "media.next",
    "media.stop",
    "volume.set",
    "volume.change",
    "alarm.stop",
    "general.answer",
    "none",
]
WHEN = ["now", "today", "tonight", "tomorrow", "this_afternoon", "this_weekend"]
KINDS = ["artist", "album", "playlist", "song", "any"]

SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "slots": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "duration_s": {"type": "integer"},
                "all": {"type": "boolean"},
                "when": {"type": "string", "enum": WHEN},
                "query": {"type": "string"},
                "kind": {"type": "string", "enum": KINDS},
                "level": {"type": "integer"},
                "delta": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "answer": {"type": ["string", "null"]},
    },
    "required": ["intent", "slots", "answer"],
    "additionalProperties": False,
}

SYSTEM_PROMPT_V1 = """You are Coco, a voice assistant on a family kitchen iPad. Kids and adults talk to you. \
The request below was transcribed from speech and did not match any built-in command rule. \
Map it to exactly ONE intent from this closed list, or answer it as a general question.

Intents (slots in parentheses; include only slots that the request actually gives):
- timer.create (name?, duration_s) - start a countdown. name = the food/thing, e.g. "eggs". duration_s in seconds.
- timer.query (name?) - how long is left on a timer.
- timer.cancel (name? | all) - stop/delete a running timer; all=true for every timer.
- timer.add_time (name?, duration_s) - add time to an existing timer.
- timer.list - which timers are running / how many.
- time.now - the current time.
- date.today - today's date or day of the week.
- weather.current - weather / temperature outside right now.
- weather.forecast (when) - forecast; when is one of today, tonight, tomorrow, this_afternoon, this_weekend.
- weather.rain (when) - will it rain / need an umbrella.
- picture.show (query) - show a photo of something; query is the thing, e.g. "narwhal".
- sound.play (name) - play the noise an animal or vehicle makes; name is the plain animal/vehicle, e.g. "dog".
- youtube.play (query) - play a video from YouTube.
- music.play (query, kind) - play music on Spotify; kind is artist, album, playlist, song or any.
- media.pause, media.resume, media.next, media.stop - control what is playing.
- volume.set (level) - set volume to level 0-100.
- volume.change (delta) - louder (+10) or quieter (-10) unless an amount is given.
- alarm.stop - silence a ringing timer alarm.
- general.answer - a general knowledge, cooking, maths, spelling or kids' question you can answer.
- none - anything else: actions you cannot do (shopping, ordering, calls, messages, lights, lists), \
or requests you cannot answer safely.

Rules:
- Reply with ONLY a JSON object: {"intent": "<intent>", "slots": {...}, "answer": <string or null>}.
- For every intent except general.answer, "answer" is null.
- For general.answer, "slots" is {} and "answer" is spoken aloud by a speaker: at most 2 short sentences, \
plain words, no lists, no markdown, no emoji, numbers written as a person would say them. \
Be accurate; if you are not sure, say so briefly.
- Never invent an intent that is not in the list. If unsure which command was meant, use none."""

# v2: v1 + fixes for every failure class seen in the v1 runs (see qwen.md).
SYSTEM_PROMPT_V2 = """You are Coco, a voice assistant on a family kitchen iPad. Kids and adults talk to you. \
The request below was transcribed from speech and did not match any built-in command rule. \
Map it to exactly ONE intent from this closed list, or answer it as a general question.

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
- general.answer - any question about the world you can answer: science, nature, space, animals, \
cooking, conversions, maths, spelling, history, geography.
- none - actions you cannot do: shopping, ordering, lists, calls, messages, lights, bookings, \
or anything unsafe or unclear.

Slot rules:
- Include only slots the person actually said. Never output null; leave the slot out instead.
- duration_s is in SECONDS: "45 seconds" = 45, "eleven minutes" = 660, "an hour and a half" = 5400.
- timer name is the food or thing being timed ("eggs", "bread"). Never use words like \
"timer", "countdown", "one" or "it" as a name; leave name out.
- when is one of: today, tonight, tomorrow, this_afternoon, this_weekend.
- sound name is the plain animal or vehicle: "doggo noise" -> "dog".
- kind is one of artist, album, playlist, song, any: a performer -> artist; a soundtrack or record -> album; \
a genre or mood like "jazz for dinner" -> playlist; a song title -> song.

Output rules:
- Reply with ONLY one JSON object, on one line: {"intent": "<intent>", "slots": {...}, "answer": <string or null>}.
- "answer" is null for every intent except general.answer.
- For general.answer, "slots" is {} and "answer" is spoken aloud: one or two short sentences, \
plain words a child understands, no lists, no markdown, no emoji. When spelling a word, \
spell it carefully letter by letter with dashes. If you are not sure of a fact, say so briefly."""

PROMPTS = {"v1": SYSTEM_PROMPT_V1, "v2": SYSTEM_PROMPT_V2}
SYSTEM_PROMPT = SYSTEM_PROMPT_V2  # the recommended one


def chat_body(model: str, text: str, use_format: bool) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "stream": True,
        "think": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 200},
    }
    if use_format:
        body["format"] = SCHEMA
    return body


def call(client: httpx.Client, model: str, text: str, use_format: bool) -> dict:
    t0 = time.perf_counter()
    ttft = None
    content = ""
    final: dict = {}
    with client.stream("POST", f"{OLLAMA}/api/chat", json=chat_body(model, text, use_format)) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(r.text)
        for line in r.iter_lines():
            if not line:
                continue
            msg = json.loads(line)
            if "error" in msg:
                raise RuntimeError(msg["error"])
            piece = msg.get("message", {}).get("content", "")
            if piece and ttft is None:
                ttft = time.perf_counter() - t0
            content += piece
            if msg.get("done"):
                final = msg
    total = time.perf_counter() - t0
    ev_n = final.get("eval_count") or 0
    ev_d = (final.get("eval_duration") or 0) / 1e9
    return {
        "raw": content,
        "ttft_s": round(ttft or total, 3),
        "total_s": round(total, 3),
        "load_s": round((final.get("load_duration") or 0) / 1e9, 3),
        "prompt_tokens": final.get("prompt_eval_count"),
        "prompt_eval_s": round((final.get("prompt_eval_duration") or 0) / 1e9, 3),
        "eval_tokens": ev_n,
        "tok_per_s": round(ev_n / ev_d, 1) if ev_d else None,
    }


def parse(raw: str) -> tuple[dict | None, str | None]:
    """Parse + validate against SCHEMA (hand-rolled, stdlib only). Returns (obj, error)."""
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        return None, f"json: {e}"
    if not isinstance(obj, dict) or set(obj) != {"intent", "slots", "answer"}:
        return obj, f"keys: {list(obj) if isinstance(obj, dict) else type(obj).__name__}"
    if obj["intent"] not in INTENTS:
        return obj, f"intent not in set: {obj['intent']}"
    slots = obj["slots"]
    if not isinstance(slots, dict):
        return obj, "slots not object"
    props = SCHEMA["properties"]["slots"]["properties"]
    for k, v in slots.items():
        if k not in props:
            return obj, f"unknown slot {k}"
        t = props[k]["type"]
        ok = {
            "string": isinstance(v, str),
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "boolean": isinstance(v, bool),
        }[t]
        if not ok or ("enum" in props[k] and v not in props[k]["enum"]):
            return obj, f"bad slot {k}={v!r}"
    if obj["answer"] is not None and not isinstance(obj["answer"], str):
        return obj, "answer type"
    return obj, None


def repair(raw: str) -> dict | None:
    """Lenient pass the Hub would run when strict validation fails: drop null slots,
    coerce numeric strings ("+10") to int. Returns a schema-valid object or None."""
    try:
        obj = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    obj.setdefault("answer", None)
    slots = obj.get("slots") if isinstance(obj.get("slots"), dict) else {}
    props = SCHEMA["properties"]["slots"]["properties"]
    fixed = {}
    for k, v in slots.items():
        if v is None or k not in props:
            continue
        if props[k]["type"] == "integer" and isinstance(v, str) and re.fullmatch(r"[+-]?\d+", v.strip()):
            v = int(v)
        fixed[k] = v
    obj["slots"] = fixed
    obj = {k: obj.get(k) for k in ("intent", "slots", "answer")}
    good, err = parse(json.dumps(obj))
    return good if err is None else None


def norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\b(the|a|an|timer|sound|noise)\b", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:-1] if s.endswith("s") and len(s) > 3 else s


def slot_checks(expected: dict, got: dict) -> dict:
    """Per expected slot: True/False. Names/queries match loosely (substring after normalising)."""
    out = {}
    for k, ev in expected.items():
        gv = got.get(k)
        if k in ("name", "query"):
            out[k] = isinstance(gv, str) and bool(norm(gv)) and (norm(ev) in norm(gv) or norm(gv) in norm(ev))
        elif k == "delta":
            out[k] = isinstance(gv, int) and gv != 0 and (gv > 0) == (ev == "+")
        else:
            out[k] = gv == ev
    # a named timer slot invented when none was spoken is also a slot error
    if "name" not in expected and got.get("name"):
        out["name_spurious"] = False
    return out


def sentences(text: str) -> int:
    return len([p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p])


def ps_size(model: str) -> dict:
    data = httpx.get(f"{OLLAMA}/api/ps").json()
    for m in data.get("models", []):
        if m["name"] == model:
            return {"size": m.get("size"), "size_vram": m.get("size_vram"), "context_length": m.get("context_length")}
    return {}


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (HERE / name).read_text().splitlines() if line.strip()]


def score(c: dict, r: dict) -> dict:
    obj, err = parse(r["raw"])
    fixed = obj if err is None else repair(r["raw"])
    got_intent = fixed.get("intent") if fixed else None
    ok_intents = {c["intent"], *c.get("alt", [])}
    row = {**c, **r, "valid": err is None, "error": err, "repaired_ok": fixed is not None, "got": fixed}
    row["intent_ok"] = got_intent in ok_intents
    if fixed and row["intent_ok"] and got_intent == c["intent"] and c["intent"] != "general.answer":
        row["slots_ok"] = slot_checks(c["slots"], fixed["slots"])
    if fixed and got_intent == "general.answer" and fixed.get("answer"):
        row["answer_sentences"] = sentences(fixed["answer"])
        row["answer_words"] = len(fixed["answer"].split())
    return row


def run(model: str, cold: bool, prompt: str) -> None:
    global SYSTEM_PROMPT
    SYSTEM_PROMPT = PROMPTS[prompt]
    sets = {"main": load("utterances.jsonl")}
    if prompt != "v1":
        sets["heldout"] = load("utterances-heldout.jsonl")
    client = httpx.Client(timeout=300)

    # Does this runner accept JSON-schema `format`? (MLX runner in 0.40.16 does not.)
    use_format = True
    try:
        call(client, model, "what time is it", True)
    except RuntimeError as e:
        print(f"format rejected: {e} -> prompt-only JSON")
        use_format = False

    cold_res = None
    if cold:
        # unload this model only (does not stop Ollama), then time the first request
        httpx.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
        time.sleep(2)
        cold_res = call(client, model, "how many legs does a spider have", use_format)
        print(f"cold: {cold_res['total_s']}s (load {cold_res['load_s']}s)")

    call(client, model, "what time is it", use_format)  # warm-up
    rows = []
    for set_name, cases in sets.items():
        for c in cases:
            row = {"set": set_name, **score(c, call(client, model, c["text"], use_format))}
            mark = "ok " if row["intent_ok"] else "BAD"
            raw = row["raw"].replace("\n", " ")
            print(f"{set_name[:4]} {mark} {row['total_s']:5.2f}s {c['text'][:45]:45} -> {raw[:110]}")
            rows.append(row)

    ps_text = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
    out = {
        "model": model,
        "prompt": prompt,
        "structured_output": use_format,
        "cold": cold_res,
        "ollama_ps": ps_text,
        "ps": ps_size(model),
        "summary": {s: summarise([r for r in rows if r["set"] == s]) for s in sets},
        "rows": rows,
    }
    suffix = "" if prompt == "v2" else f"-{prompt}"
    (HERE / f"results-{model.replace(':', '_')}{suffix}.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out["summary"], indent=2))
    print(ps_text)


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))], 2)


def summarise(rows: list[dict]) -> dict:
    cmd = [r for r in rows if r["intent"] != "general.answer"]
    gen = [r for r in rows if r["intent"] == "general.answer"]
    slot_vals = {"duration_s": [], "name": [], "other": []}
    for r in rows:
        for k, v in (r.get("slots_ok") or {}).items():
            key = "name" if k == "name_spurious" else (k if k in slot_vals else "other")
            slot_vals[key].append(v)

    def rate(xs):
        return f"{sum(xs)}/{len(xs)}" if xs else "-"

    def fully_right(r):
        return r["repaired_ok"] and r["intent_ok"] and all((r.get("slots_ok") or {}).values())

    return {
        "n": len(rows),
        "valid_json_strict": rate([r["valid"] for r in rows]),
        "valid_after_repair": rate([r["repaired_ok"] for r in rows]),
        "intent_ok_all": rate([r["intent_ok"] for r in rows]),
        "intent_ok_commands": rate([r["intent_ok"] for r in cmd if r["intent"] != "none"]),
        "intent_ok_general": rate([r["intent_ok"] for r in gen]),
        "intent_ok_none": rate([r["intent_ok"] for r in rows if r["intent"] == "none"]),
        "slot_duration_ok": rate(slot_vals["duration_s"]),
        "slot_name_ok": rate(slot_vals["name"]),
        "slot_other_ok": rate(slot_vals["other"]),
        "fully_right": rate([fully_right(r) for r in rows]),
        "lat_cmd_p50": pct([r["total_s"] for r in cmd], 0.5),
        "lat_cmd_p90": pct([r["total_s"] for r in cmd], 0.9),
        "lat_gen_p50": pct([r["total_s"] for r in gen], 0.5),
        "lat_gen_p90": pct([r["total_s"] for r in gen], 0.9),
        "lat_max": pct([r["total_s"] for r in rows], 1.0),
        "ttft_p50": pct([r["ttft_s"] for r in rows], 0.5),
        "prompt_eval_p50": pct([r["prompt_eval_s"] for r in rows], 0.5),
        "tok_per_s_p50": round(statistics.median([r["tok_per_s"] for r in rows if r["tok_per_s"]]), 1),
        "answers_over_3_sentences": sum(1 for r in rows if r.get("answer_sentences", 0) > 3),
        "answers_over_2_sentences": sum(1 for r in rows if r.get("answer_sentences", 0) > 2),
    }


def report() -> None:
    for f in sorted(HERE.glob("results-*.json")):
        d = json.loads(f.read_text())
        print(f"{d['model']} prompt={d.get('prompt', 'v1')} structured_output={d['structured_output']} ps={d['ps']}")
        print("  cold:", d["cold"] and {k: d["cold"][k] for k in ("total_s", "load_s", "prompt_eval_s")})
        for s, summ in (d["summary"].items() if "n" not in d["summary"] else [("main", d["summary"])]):
            print(f"  {s}:", json.dumps(summ))


def rescore(path: Path) -> None:
    """Re-score a results file's recorded raw outputs with the current scoring (no model calls)."""
    d = json.loads(path.read_text())
    timing = ("raw", "ttft_s", "total_s", "load_s", "prompt_tokens", "prompt_eval_s", "eval_tokens", "tok_per_s")
    case = ("text", "intent", "slots", "fact", "alt")
    rows = []
    for r in d["rows"]:
        c = {k: r[k] for k in case if k in r}
        rows.append({"set": r.get("set", "main"), **score(c, {k: r[k] for k in timing})})
    d["rows"] = rows
    d.setdefault("prompt", "v1")
    d["summary"] = {s: summarise([r for r in rows if r["set"] == s]) for s in dict.fromkeys(r["set"] for r in rows)}
    path.write_text(json.dumps(d, indent=2) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "--report":
        report()
    elif sys.argv[1] == "--rescore":
        for f in sys.argv[2:]:
            rescore(Path(f))
    else:
        p = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--prompt=")), "v2")
        run(sys.argv[1], "--cold" in sys.argv, p)
