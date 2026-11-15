"""``hub/deploy/setup.py`` (``make hub-setup``): config writing and the install prompts.

No real brew / ollama / uv calls: the script takes a command runner and a prompt function.
"""

import importlib.util
import stat
import sys
import tomllib
from pathlib import Path

import pytest

from coco_hub.settings import Settings

HUB = Path(__file__).resolve().parents[1]
EXAMPLE = HUB / "config.example.toml"


def _load_setup():
    spec = importlib.util.spec_from_file_location("coco_hub_setup", HUB / "deploy" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


setup = _load_setup()


class FakeRunner:
    """Records commands; ``which`` answers from ``installed``."""

    def __init__(self, installed=("brew", "uv", "ollama"), outputs=None) -> None:
        self.installed = set(installed)
        self.outputs = outputs or {}
        self.ran: list[list[str]] = []

    def which(self, name: str) -> str | None:
        return f"/opt/homebrew/bin/{name}" if name in self.installed else None

    def run(self, cmd: list[str], cwd: Path | None = None) -> setup.Result:
        self.ran.append(cmd)
        return setup.Result(0, self.outputs.get(cmd[0], ""))


def answers(*replies: str):
    """A prompt function that returns ``replies`` in order and records the questions."""
    asked: list[str] = []
    queue = list(replies)

    def ask(question: str) -> str:
        asked.append(question)
        return queue.pop(0) if queue else ""

    ask.asked = asked
    return ask


# --- config rendering ----------------------------------------------------------------------


def test_render_config_fills_token_location_timezone_and_qwen():
    text = setup.render_config(
        EXAMPLE.read_text(),
        token="t" * 43,
        lat=51.5,
        lon=-0.12,
        timezone="Europe/London",
    )
    data = tomllib.loads(text)
    assert data["token"] == "t" * 43
    assert data["timezone"] == "Europe/London"
    assert (data["location"]["lat"], data["location"]["lon"]) == (51.5, -0.12)
    assert data["qwen"]["enabled"] is True
    # Everything else is the example, comments included.
    assert data["night"] == tomllib.loads(EXAMPLE.read_text())["night"]
    assert "# Shared LAN token" in text


def test_rendered_config_is_valid_hub_settings():
    text = setup.render_config(
        EXAMPLE.read_text(), token=setup.new_token(), lat=45.0, lon=-75.0, timezone="UTC"
    )
    data = tomllib.loads(text)
    settings = Settings(**data)
    assert settings.location.lat == 45.0
    assert settings.timezone == "UTC"


def test_new_token_is_long_and_fresh():
    a, b = setup.new_token(), setup.new_token()
    assert a != b
    assert len(a) >= 24


def test_qwen_model_is_read_from_the_config():
    assert setup.qwen_model(EXAMPLE.read_text()) == "qwen3:8b"
    assert setup.qwen_model('[qwen]\nmodel = "qwen3:14b"\n') == "qwen3:14b"
    assert setup.qwen_model("token = 'x'\n") == setup.DEFAULT_MODEL


@pytest.mark.parametrize(
    ("lat", "lon", "tz"),
    [("91", "0", "UTC"), ("0", "-181", "UTC"), ("abc", "0", "UTC"), ("0", "0", "Not/AZone")],
)
def test_bad_location_or_timezone_is_rejected(lat, lon, tz):
    with pytest.raises(setup.SetupError):
        setup.parse_location(lat, lon, tz)


def test_parse_location():
    assert setup.parse_location("45.42", "-75.69", "America/Toronto") == (
        45.42,
        -75.69,
        "America/Toronto",
    )


# --- write_config ---------------------------------------------------------------------------


def test_write_config_from_flags_without_prompting(tmp_path):
    config = tmp_path / "config.local.toml"
    ask = answers()
    out: list[str] = []
    written = setup.write_config(
        config,
        EXAMPLE,
        lat="45.42",
        lon="-75.69",
        timezone="America/Toronto",
        ask=ask,
        interactive=False,
        say=out.append,
    )
    assert written is True
    assert ask.asked == []
    data = tomllib.loads(config.read_text())
    assert len(data["token"]) >= 24
    assert data["location"]["lat"] == 45.42
    assert stat.S_IMODE(config.stat().st_mode) == 0o600  # the token is a secret


def test_write_config_asks_for_missing_values(tmp_path):
    config = tmp_path / "config.local.toml"
    ask = answers("51.5", "-0.12", "Europe/London")
    setup.write_config(
        config, EXAMPLE, lat=None, lon=None, timezone=None, ask=ask, interactive=True, say=print
    )
    assert len(ask.asked) == 3
    data = tomllib.loads(config.read_text())
    assert (data["location"]["lat"], data["timezone"]) == (51.5, "Europe/London")


def test_write_config_reasks_after_a_bad_answer(tmp_path):
    config = tmp_path / "config.local.toml"
    ask = answers("north", "51.5", "-0.12", "Europe/London")
    setup.write_config(
        config, EXAMPLE, lat=None, lon=None, timezone=None, ask=ask, interactive=True, say=print
    )
    assert tomllib.loads(config.read_text())["location"]["lat"] == 51.5


def test_timezone_prompt_defaults_to_the_example(tmp_path):
    config = tmp_path / "config.local.toml"
    ask = answers("51.5", "-0.12", "")
    setup.write_config(
        config, EXAMPLE, lat=None, lon=None, timezone=None, ask=ask, interactive=True, say=print
    )
    assert tomllib.loads(config.read_text())["timezone"] == "America/Toronto"


def test_write_config_non_interactive_needs_every_value(tmp_path):
    config = tmp_path / "config.local.toml"
    with pytest.raises(setup.SetupError, match="--lat"):
        setup.write_config(
            config,
            EXAMPLE,
            lat=None,
            lon="0",
            timezone="UTC",
            ask=answers(),
            interactive=False,
            say=print,
        )
    assert not config.exists()


def test_existing_config_is_never_touched(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text('token = "mine"\n')
    out: list[str] = []
    written = setup.write_config(
        config,
        EXAMPLE,
        lat="1",
        lon="2",
        timezone="UTC",
        ask=answers(),
        interactive=False,
        say=out.append,
    )
    assert written is False
    assert config.read_text() == 'token = "mine"\n'
    assert any("already exists" in line for line in out)


def test_values_come_from_env(monkeypatch):
    monkeypatch.setenv("COCO_SETUP_LAT", "10")
    monkeypatch.setenv("COCO_SETUP_LON", "20")
    monkeypatch.setenv("COCO_SETUP_TIMEZONE", "UTC")
    args = setup.parse_args([])
    assert (args.lat, args.lon, args.timezone) == ("10", "20", "UTC")
    assert setup.parse_args(["--lat", "5"]).lat == "5"


# --- tools: print by default, run only on an explicit yes ----------------------------------


def test_missing_tool_prints_the_install_command_by_default():
    runner = FakeRunner(installed={"brew", "ollama"})
    out: list[str] = []
    ask = answers("")  # Enter = the default, which is no
    setup.ensure_tool("uv", runner=runner, ask=ask, interactive=True, say=out.append)
    assert runner.ran == []
    assert any("brew install uv" in line for line in out)
    assert "[y/N]" in ask.asked[0]


def test_missing_tool_is_installed_on_yes():
    runner = FakeRunner(installed={"brew"})
    setup.ensure_tool("ollama", runner=runner, ask=answers("y"), interactive=True, say=print)
    assert runner.ran == [["brew", "install", "ollama"]]


def test_non_interactive_never_runs_brew():
    runner = FakeRunner(installed={"brew"})
    out: list[str] = []
    ask = answers("y")
    setup.ensure_tool("uv", runner=runner, ask=ask, interactive=False, say=out.append)
    assert runner.ran == [] and ask.asked == []
    assert any("brew install uv" in line for line in out)


def test_installed_tool_is_left_alone():
    runner = FakeRunner()
    ask = answers()
    assert setup.ensure_tool("uv", runner=runner, ask=ask, interactive=True, say=print) is True
    assert runner.ran == [] and ask.asked == []


def test_without_brew_it_points_at_homebrew():
    runner = FakeRunner(installed=set())
    out: list[str] = []
    setup.ensure_tool("uv", runner=runner, ask=answers("y"), interactive=True, say=out.append)
    assert runner.ran == []
    assert any("brew.sh" in line for line in out)


def test_ollama_service_and_model_are_offered_not_forced():
    runner = FakeRunner()
    out: list[str] = []
    setup.ensure_ollama(
        "qwen3:8b", runner=runner, ask=answers("", ""), interactive=True, say=out.append
    )
    assert runner.ran == [["ollama", "list"]]  # only the read-only check
    assert any("brew services start ollama" in line for line in out)
    assert any("ollama pull qwen3:8b" in line for line in out)


def test_ollama_model_already_pulled_is_not_offered():
    runner = FakeRunner(outputs={"ollama": "NAME ID SIZE\nqwen3:8b abc 5.2 GB\n"})
    ask = answers("y")
    setup.ensure_ollama("qwen3:8b", runner=runner, ask=ask, interactive=True, say=print)
    assert ["ollama", "pull", "qwen3:8b"] not in runner.ran
    assert runner.ran == [["ollama", "list"], ["brew", "services", "start", "ollama"]]


def test_ollama_steps_run_on_yes():
    runner = FakeRunner()
    setup.ensure_ollama(
        "qwen3:8b", runner=runner, ask=answers("y", "y"), interactive=True, say=print
    )
    assert runner.ran == [
        ["ollama", "list"],
        ["brew", "services", "start", "ollama"],
        ["ollama", "pull", "qwen3:8b"],
    ]


# --- the whole run ----------------------------------------------------------------------------


def test_main_non_interactive_end_to_end(tmp_path):
    config = tmp_path / "config.local.toml"
    runner = FakeRunner(outputs={"uv": "Coco Hub fingerprint: 3F2A-91C0\nHub ID: 5d0c"})
    out: list[str] = []
    code = setup.main(
        ["--lat", "45", "--lon", "-75", "--timezone", "UTC", "--non-interactive"],
        runner=runner,
        ask=answers(),
        say=out.append,
        config=config,
        example=EXAMPLE,
    )
    assert code == 0
    assert config.exists()
    assert [c[0] for c in runner.ran] == ["ollama", "uv"]  # check models, print fingerprint
    text = "\n".join(out)
    assert "3F2A-91C0" in text
    assert "make hub-install-service" in text
    # Idempotent: a second run keeps the config and its token.
    before = config.read_text()
    assert (
        setup.main(
            ["--non-interactive"],
            runner=runner,
            ask=answers(),
            say=out.append,
            config=config,
            example=EXAMPLE,
        )
        == 0
    )
    assert config.read_text() == before


def test_main_reports_bad_flags(tmp_path):
    out: list[str] = []
    code = setup.main(
        ["--lat", "200", "--lon", "0", "--timezone", "UTC", "--non-interactive"],
        runner=FakeRunner(),
        ask=answers(),
        say=out.append,
        config=tmp_path / "c.toml",
        example=EXAMPLE,
    )
    assert code == 2
    assert not (tmp_path / "c.toml").exists()
