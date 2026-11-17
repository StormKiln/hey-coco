import pytest
from pydantic import SecretStr, ValidationError

from coco_hub.settings import Settings

from conftest import TEST_TOKEN

URL = "https://calendar.example.invalid/ical/private-0123456789abcdef/basic.ics"


def settings(calendars, tmp_path) -> Settings:
    return Settings(token=TEST_TOKEN, data_dir=tmp_path, calendars=calendars)


def test_no_calendars_by_default(tmp_path):
    assert Settings(token=TEST_TOKEN, data_dir=tmp_path).calendars == []


def test_ics_calendar_with_defaults(tmp_path):
    (c,) = settings([{"source": "ics", "url": URL}], tmp_path).calendars
    assert c.source == "ics"
    assert isinstance(c.options.url, SecretStr)
    assert c.options.url.get_secret_value() == URL
    assert (c.name, c.color, c.show_on_idle, c.busy_only) == (None, None, True, False)


def test_all_options(tmp_path):
    (c,) = settings(
        [
            {
                "source": "ics",
                "url": URL,
                "name": "Family",
                "color": "#1a73e8",
                "show_on_idle": False,
                "busy_only": True,
            }
        ],
        tmp_path,
    ).calendars
    assert (c.name, c.color, c.show_on_idle, c.busy_only) == ("Family", "#1A73E8", False, True)


def test_from_toml_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        f'token = "{TEST_TOKEN}"\n'
        "[[calendars]]\n"
        'source = "ics"\n'
        f'url = "{URL}"\n'
        "[[calendars]]\n"
        'source = "ics"\n'
        'url = "webcal://calendar.example.invalid/other.ics"\n'
        'name = "School"\n'
    )
    monkeypatch.setenv("COCO_CONFIG", str(path))
    first, second = Settings().calendars  # type: ignore[call-arg]
    assert second.name == "School"


@pytest.mark.parametrize(
    "entry",
    [
        {"url": URL},  # no source
        {"source": "carrier-pigeon", "url": URL},
        {"source": "ics"},  # no url
        {"source": "ics", "url": "ftp://calendar.example.invalid/x.ics"},
        {"source": "ics", "url": URL, "colour": "#ffffff"},  # typo
        {"source": "ics", "url": URL, "color": "blue"},
    ],
)
def test_invalid_entries_are_refused(entry, tmp_path):
    with pytest.raises(ValidationError):
        settings([entry], tmp_path)


def test_url_never_appears_in_repr_or_validation_errors(tmp_path):
    s = settings([{"source": "ics", "url": URL}], tmp_path)
    assert "private-0123456789abcdef" not in repr(s)
    assert "private-0123456789abcdef" not in repr(s.calendars)
    with pytest.raises(ValidationError) as exc_info:
        settings([{"source": "ics", "url": URL, "colour": "red"}], tmp_path)
    assert "private-0123456789abcdef" not in str(exc_info.value)
