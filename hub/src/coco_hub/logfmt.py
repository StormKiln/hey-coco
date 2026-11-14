"""Pretty-print the Hub's JSON-lines log: ``tail -F hub.jsonl | python -m coco_hub.logfmt``.

One line per record (``ts LEVEL logger: msg``), extras and exceptions indented below it.
Lines that aren't JSON (e.g. ``tail`` headers) pass through unchanged. Stdlib only.
"""

import json
import sys
from typing import TextIO

_CORE = ("ts", "level", "logger", "msg", "exc", "stack")


def format_line(line: str) -> str:
    try:
        record = json.loads(line)
    except ValueError:
        return line
    if not isinstance(record, dict):
        return line
    head = (
        f"{record.get('ts', '')} {record.get('level', ''):<7} "
        f"{record.get('logger', '')}: {record.get('msg', '')}"
    )
    parts = [head]
    for key, value in record.items():
        if key not in _CORE:
            parts.append(f"    {key}: {json.dumps(value, ensure_ascii=False)}")
    for key in ("exc", "stack"):
        if record.get(key):
            parts.extend(f"    {text}" for text in str(record[key]).splitlines())
    return "\n".join(parts)


def main(src: TextIO = sys.stdin, dst: TextIO = sys.stdout) -> None:
    for line in src:
        print(format_line(line.rstrip("\n")), file=dst, flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
