import io
import json

from coco_hub.logfmt import format_line, main


def test_formats_a_json_line_with_extras():
    line = json.dumps(
        {
            "ts": "2026-11-14T20:05:00.123Z",
            "level": "INFO",
            "logger": "coco_hub.utterance",
            "msg": "utterance 'what time is it' -> time.now (rules) 3ms",
            "trace": {"intent": "time.now"},
        }
    )
    out = format_line(line)
    assert out.startswith("2026-11-14T20:05:00.123Z INFO    coco_hub.utterance: utterance")
    assert '\n    trace: {"intent": "time.now"}' in out


def test_includes_exception_text():
    line = json.dumps({"ts": "t", "level": "ERROR", "logger": "x", "msg": "m", "exc": "Trace\nX"})
    assert format_line(line).endswith("\n    Trace\n    X")


def test_passes_non_json_lines_through():
    assert format_line("==> hub.jsonl <==") == "==> hub.jsonl <=="


def test_main_streams_stdin_to_stdout():
    src = io.StringIO('{"ts": "t", "level": "INFO", "logger": "x", "msg": "hi"}\nplain\n')
    dst = io.StringIO()
    main(src, dst)
    assert dst.getvalue() == "t INFO    x: hi\nplain\n"
