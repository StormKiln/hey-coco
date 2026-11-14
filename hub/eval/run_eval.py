"""Offline accuracy/latency check of the intent pipeline against a live local Ollama.

    make hub-eval                      # qwen.model from settings (default qwen3:8b)
    make hub-eval ARGS="--model qwen3:8b --show-answers"

Runs every line of ``eval/utterances.jsonl`` through the rules engine and then, on NoMatch,
through ``QwenFallback.interpret`` (no handlers are run). Lines whose expected intent is not
registered yet are skipped, so the set can carry phrasings for features still being built.
Reports rules-only vs rules+Qwen accuracy and latency. Not part of CI (needs Ollama).

Scoring follows the spike (docs/spikes/qwen.md): intent must be the expected one or an
allowed ``alt``; for Qwen results, slots are checked per expected key (``name``/``query``/
``word`` loosely, ``delta`` by sign, the rest exactly), and an unrequested timer ``name``
counts as a slot error. An invalid reply counts as ``none`` (the Hub says sorry).
"""

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

from coco_hub.intents import build_registry
from coco_hub.intents.engine import NoMatch, RulesEngine
from coco_hub.intents.normalize import normalize
from coco_hub.protocol import UtteranceContext, UtteranceRequest
from coco_hub.qwen.catalog import GENERAL, NONE
from coco_hub.qwen.client import OllamaClient, OllamaError
from coco_hub.qwen.fallback import QwenFallback, tidy_answer
from coco_hub.settings import Settings

HERE = Path(__file__).resolve().parent
DEFAULT_SET = HERE / "utterances.jsonl"
LOOSE = {"name", "query", "word"}


def _loose(s: Any) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(s).lower()).strip()


def slots_ok(intent: str, expected: dict[str, Any], got: dict[str, Any]) -> bool:
    for key, want in expected.items():
        have = got.get(key)
        if have is None:
            return False
        if key in LOOSE:
            a, b = _loose(want), _loose(have)
            if not (a and b and (a in b or b in a)):
                return False
        elif key == "delta":
            sign = 1 if want == "+" else -1 if want == "-" else (want > 0) - (want < 0)
            if not isinstance(have, int) or (have > 0) - (have < 0) != sign:
                return False
        elif have != want:
            return False
    return not (intent.startswith("timer.") and "name" in got and "name" not in expected)


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "0/0"


def quantiles(xs: list[float]) -> str:
    if not xs:
        return "n/a"
    xs = sorted(xs)
    p = lambda q: xs[min(len(xs) - 1, round(q * (len(xs) - 1)))]  # noqa: E731
    return f"p50 {p(0.5):.2f}s / p90 {p(0.9):.2f}s / max {xs[-1]:.2f}s (n={len(xs)})"


async def run(args: argparse.Namespace) -> int:
    settings = Settings(token="offline-evaluation-token-000000", discovery={"enabled": False})
    model = args.model or settings.qwen.model
    registry = build_registry()
    engine = RulesEngine(registry)
    client = OllamaClient(args.base_url or settings.qwen.base_url, model)
    fallback = QwenFallback(client, engine, timeout_s=args.timeout)
    names = set(fallback.catalog.intent_names)

    health = await client.health()
    if not health.reachable or not health.model_present:
        print(f"Ollama not ready for {model}: {health}", file=sys.stderr)
        return 2
    await fallback.warm_up()

    cases = [json.loads(line) for line in args.file.read_text().splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for case in cases:
        ok_intents = {case["intent"], *case.get("alt", [])}
        if not ok_intents & names:
            skipped[case["intent"]] = skipped.get(case["intent"], 0) + 1
            continue
        context = {"view": "idle", "alarm_ringing": False, "media_playing": None}
        context |= case.get("context", {})
        request = UtteranceRequest(
            text=case["text"], source="voice", context=UtteranceContext(**context)
        )
        started = time.monotonic()
        text = normalize(case["text"])
        match = registry.match(text, request.context)
        rules_intent = match.intent if match else NONE
        row: dict[str, Any] = {**case, "rules": rules_intent, "by": "rules"}
        if match:
            row |= {"got": rules_intent, "slots_got": {}, "slots_ok": True}
        else:
            row["by"] = "qwen"
            try:
                async with asyncio.timeout(args.timeout):
                    result = await fallback.interpret(NoMatch(text, case["text"]), request)
            except TimeoutError:
                result, row["got"] = None, "timeout"
            except OllamaError as exc:
                print(f"Ollama error: {exc}", file=sys.stderr)
                return 2
            if result is None:
                row.setdefault("got", NONE)
                row["invalid"] = row["got"] != "timeout"
                row |= {"slots_got": {}, "slots_ok": not case.get("slots")}
            else:
                row["got"] = result.intent
                row["slots_got"] = dict(result.slots)
                row["slots_ok"] = slots_ok(result.intent, case.get("slots", {}), row["slots_got"])
                if result.intent == GENERAL:
                    row["answer"] = tidy_answer(result.answer or "")
        row["latency"] = time.monotonic() - started
        row["intent_ok"] = row["got"] in ok_intents
        row["rules_ok"] = rules_intent in ok_intents
        row["full_ok"] = row["intent_ok"] and row["slots_ok"]
        rows.append(row)
        if args.verbose:
            mark = "ok " if row["full_ok"] else "BAD"
            print(
                f"{mark} {row['latency']:.2f}s {case['text']!r} -> {row['got']} "
                f"{row['slots_got'] or ''}"
            )

    await fallback.aclose()
    n = len(rows)
    qwen_rows = [r for r in rows if r["by"] == "qwen"]
    print(
        f"\nModel {model}, {n} scored utterances ({len(cases) - n} skipped: intent not "
        f"registered yet: {', '.join(f'{k} x{v}' for k, v in sorted(skipped.items()))})"
    )
    print(f"  rules only:     intent {pct(sum(r['rules_ok'] for r in rows), n)}")
    print(
        f"  rules + qwen:   intent {pct(sum(r['intent_ok'] for r in rows), n)}, "
        f"fully right {pct(sum(r['full_ok'] for r in rows), n)}"
    )
    print(
        f"  handled by rules {len(rows) - len(qwen_rows)}, by qwen {len(qwen_rows)} "
        f"(invalid after repair {sum(bool(r.get('invalid')) for r in rows)}, "
        f"timeouts {sum(r['got'] == 'timeout' for r in rows)})"
    )
    by_intent: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_intent.setdefault(r["intent"], []).append(r)
    for intent, rs in sorted(by_intent.items()):
        print(
            f"    {intent:16} rules {pct(sum(r['rules_ok'] for r in rs), len(rs)):>12}   "
            f"rules+qwen {pct(sum(r['full_ok'] for r in rs), len(rs)):>12}"
        )
    print(f"  latency, full pipeline: {quantiles([r['latency'] for r in rows])}")
    print(
        f"  latency, qwen intents:  "
        f"{quantiles([r['latency'] for r in qwen_rows if r['got'] != GENERAL])}"
    )
    print(
        f"  latency, qwen answers:  "
        f"{quantiles([r['latency'] for r in qwen_rows if r['got'] == GENERAL])}"
    )
    misses = [r for r in rows if not r["full_ok"]]
    if misses:
        print("\nMisses:")
        for r in misses:
            print(
                f"  {r['text']!r}: expected {r['intent']} {r.get('slots') or ''}, "
                f"got {r['got']} {r['slots_got'] or ''}"
            )
    if args.show_answers:
        print("\nAnswers:")
        for r in rows:
            if "answer" in r:
                print(f"  {r['text']!r} -> {r['answer']}  [expected: {r.get('fact', '-')}]")
    if args.out:
        args.out.write_text(json.dumps({"model": model, "rows": rows}, indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", help="Ollama model tag (default: settings qwen.model)")
    parser.add_argument("--base-url", help="Ollama URL (default: settings qwen.base_url)")
    parser.add_argument("--file", type=Path, default=DEFAULT_SET)
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--show-answers", action="store_true")
    parser.add_argument("--out", type=Path, help="write per-utterance results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
