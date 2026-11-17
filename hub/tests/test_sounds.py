import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from coco_hub.app import create_app
from coco_hub.settings import DEFAULT_SOUNDS_DIR, Settings
from coco_hub.sounds import (
    ALLOWED_LICENSES,
    ManifestError,
    SoundLibrary,
    normalise,
)

from conftest import TEST_TOKEN

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}
REPO_SOUNDS = Path(__file__).resolve().parents[2] / "assets" / "sounds"


# ---------------------------------------------------------------- the shipped library


@pytest.fixture(scope="module")
def library() -> SoundLibrary:
    return SoundLibrary.load(REPO_SOUNDS)


def test_default_sounds_dir_is_repo_assets():
    assert DEFAULT_SOUNDS_DIR.resolve() == REPO_SOUNDS.resolve()


def test_shipped_manifest_loads_with_about_eighty_sounds(library):
    sounds = library.all()
    assert 75 <= len(sounds) <= 100
    categories = {s.category for s in sounds}
    assert categories == {"animal", "vehicle"}
    assert sum(s.category == "animal" for s in sounds) >= 45
    assert sum(s.category == "vehicle" for s in sounds) >= 30


def test_shipped_files_exist_and_are_reasonable(library):
    total = 0
    for sound in library.all():
        clip = library.clip_path(sound)
        image = library.image_path(sound)
        assert clip.is_file() and clip.suffix == ".m4a", sound.id
        assert image.is_file() and image.suffix == ".jpg", sound.id
        assert 0 < sound.duration_s <= 6.5, sound.id
        total += clip.stat().st_size + image.stat().st_size
    assert total < 20_000_000


def test_shipped_licences_are_all_allowed(library):
    for sound in library.all():
        for attribution in (sound.audio_attribution, sound.image_attribution):
            assert attribution.license in ALLOWED_LICENSES, sound.id
            assert "NC" not in attribution.license and "ND" not in attribution.license
            assert attribution.author.strip(), sound.id
            assert attribution.source_url.startswith("https://commons.wikimedia.org/wiki/File:")
            assert attribution.license_url.startswith("http")


def test_shipped_names_and_aliases_are_unique_across_sounds(library):
    owner: dict[str, str] = {}
    for sound in library.all():
        for key in {normalise(k) for k in (sound.name, *sound.aliases)}:
            assert owner.setdefault(key, sound.id) == sound.id, (
                f"{key!r} used by {owner[key]} and {sound.id}"
            )


def test_attribution_md_lists_every_sound():
    text = (REPO_SOUNDS / "ATTRIBUTION.md").read_text()
    manifest = json.loads((REPO_SOUNDS / "manifest.json").read_text())
    for sound in manifest["sounds"]:
        assert f"(`{sound['id']}`)" in text
        assert sound["audio_attribution"]["source_url"] in text
        assert sound["image_attribution"]["source_url"] in text


# ---------------------------------------------------------------- lookup


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("cow", "cow"),
        ("Cow", "cow"),
        ("  COW  ", "cow"),
        ("cows", "cow"),
        ("a cow", "cow"),
        ("the cow?", "cow"),
        ("moo", "cow"),
        ("kitty", "cat"),
        ("kitties", "cat"),
        ("kittens", "cat"),
        ("puppies", "dog"),
        ("doggy", "dog"),
        ("choo choo", "steam_train"),
        ("choo-choo", "steam_train"),
        ("choo choo train", "steam_train"),
        ("trains", "train"),
        ("nee naw", "fire_truck"),
        ("fire engine", "fire_truck"),
        ("fire engines", "fire_truck"),
        ("firetruck", "fire_truck"),
        ("fire trucks", "fire_truck"),
        ("guinea pigs", "guinea_pig"),
        ("cock-a-doodle-doo", "rooster"),
        ("buses", "bus"),
        ("wolves", "wolf"),
        ("ponies", "horse"),
        ("aeroplane", "airplane"),
        ("police car", "police_car"),
        ("helicopters", "helicopter"),
        ("ambulances", "ambulance"),
        ("zebras", "zebra"),
        ("geese", "goose"),
        ("honk honk", "car_horn"),
        ("peacocks", "peacock"),
        ("chimp", "chimpanzee"),
        ("monkeys", "monkey"),
        ("sea lion", "seal"),
        ("dolphins", "dolphin"),
        ("gator", "alligator"),
        ("crocodile", "alligator"),
        ("rattlesnake", "rattlesnake"),
        ("coyotes", "coyote"),
        ("loon", "loon"),
        ("ice cream truck", "ice_cream_truck"),
        ("ice cream van", "ice_cream_truck"),
        ("fighter jet", "fighter_jet"),
        ("jet", "airplane"),
        ("trams", "tram"),
        ("streetcar", "tram"),
        ("go kart", "go_kart"),
        ("go-karts", "go_kart"),
        ("lawnmower", "lawn_mower"),
        ("forklift", "forklift"),
        ("speedboat", "motorboat"),
        ("tank", "tank"),
        ("santa's sleigh", "sleigh"),
    ],
)
def test_find_matches_names_aliases_and_plurals(library, query, expected):
    sound = library.find(query)
    assert sound is not None, query
    assert sound.id == expected


@pytest.mark.parametrize("query", ["", "   ", "unicorn", "dragon", "toaster", "?"])
def test_find_unknown_returns_none(library, query):
    assert library.find(query) is None


def test_get_by_id(library):
    assert library.get("cow").name == "cow"
    assert library.get("nope") is None


def test_attribution_text_names_both_authors(library):
    cow = library.get("cow")
    text = cow.attribution_text()
    assert cow.audio_attribution.author in text
    assert cow.audio_attribution.license in text
    assert cow.image_attribution.author in text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Cock-a-Doodle-Doo!", "cock a doodle doo"),
        ("  the   FIRE truck ", "fire truck"),
        ("an owl", "owl"),
        ("Nee, naw", "nee naw"),
    ],
)
def test_normalise(raw, expected):
    assert normalise(raw) == expected


# ---------------------------------------------------------------- manifest validation


def attribution(license: str = "CC0") -> dict:
    return {
        "author": "Someone",
        "license": license,
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "source_url": "https://commons.wikimedia.org/wiki/File:X.ogg",
    }


def entry(sid: str, **overrides) -> dict:
    data = {
        "id": sid,
        "name": sid,
        "aliases": [],
        "category": "animal",
        "clip": f"clips/{sid}.m4a",
        "image": f"images/{sid}.jpg",
        "duration_s": 1.0,
        "audio_attribution": attribution(),
        "image_attribution": attribution(),
    }
    data.update(overrides)
    return data


def write_library(root: Path, entries: list[dict], create_files: bool = True) -> Path:
    (root / "clips").mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(parents=True, exist_ok=True)
    if create_files:
        for e in entries:
            (root / e["clip"]).write_bytes(b"clip-" + e["id"].encode())
            (root / e["image"]).write_bytes(b"image-" + e["id"].encode())
    (root / "manifest.json").write_text(json.dumps({"version": 1, "sounds": entries}))
    return root


def test_valid_synthetic_manifest_loads(tmp_path):
    lib = SoundLibrary.load(write_library(tmp_path, [entry("cow"), entry("dog")]))
    assert [s.id for s in lib.all()] == ["cow", "dog"]


def test_missing_manifest_is_an_error(tmp_path):
    with pytest.raises(ManifestError, match="manifest.json"):
        SoundLibrary.load(tmp_path)


def test_missing_clip_file_is_an_error(tmp_path):
    root = write_library(tmp_path, [entry("cow")])
    (root / "clips" / "cow.m4a").unlink()
    with pytest.raises(ManifestError, match=r"cow.*clips/cow\.m4a"):
        SoundLibrary.load(root)


def test_missing_image_file_is_an_error(tmp_path):
    root = write_library(tmp_path, [entry("cow")])
    (root / "images" / "cow.jpg").unlink()
    with pytest.raises(ManifestError, match=r"images/cow\.jpg"):
        SoundLibrary.load(root)


@pytest.mark.parametrize("bad", ["CC BY-NC 4.0", "CC BY-ND 4.0", "GFDL", "All rights reserved"])
def test_disallowed_licence_is_an_error(tmp_path, bad):
    root = write_library(tmp_path, [entry("cow", image_attribution=attribution(bad))])
    with pytest.raises(ManifestError, match=re.escape(bad)):
        SoundLibrary.load(root)


def test_duplicate_alias_across_sounds_is_an_error(tmp_path):
    root = write_library(tmp_path, [entry("cow", aliases=["Moo"]), entry("dog", aliases=["moo!"])])
    with pytest.raises(ManifestError, match="moo"):
        SoundLibrary.load(root)


def test_alias_colliding_with_other_name_is_an_error(tmp_path):
    root = write_library(tmp_path, [entry("cow"), entry("dog", aliases=["cow"])])
    with pytest.raises(ManifestError, match="cow"):
        SoundLibrary.load(root)


def test_duplicate_id_is_an_error(tmp_path):
    root = write_library(tmp_path, [entry("cow"), entry("cow", name="other")])
    with pytest.raises(ManifestError, match="duplicate id"):
        SoundLibrary.load(root)


@pytest.mark.parametrize(
    "path", ["../secret.m4a", "/etc/passwd", "clips/../../x.m4a", "images/cow.jpg"]
)
def test_clip_path_must_stay_inside_its_folder(tmp_path, path):
    root = write_library(tmp_path, [entry("cow")])
    data = json.loads((root / "manifest.json").read_text())
    data["sounds"][0]["clip"] = path
    (root / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ManifestError):
        SoundLibrary.load(root)


def test_unknown_category_is_an_error(tmp_path):
    root = write_library(tmp_path, [entry("cow", category="mineral")])
    with pytest.raises(ManifestError):
        SoundLibrary.load(root)


# ---------------------------------------------------------------- serving


@pytest.fixture
def media_client(tmp_path):
    sounds_dir = write_library(tmp_path / "sounds", [entry("cow"), entry("dog")])
    settings = Settings(
        token=TEST_TOKEN,
        data_dir=tmp_path / "data",
        discovery={"enabled": False},
        sounds_dir=sounds_dir,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app


def test_settings_sounds_dir_defaults_to_repo(settings):
    assert settings.sounds_dir == DEFAULT_SOUNDS_DIR


def test_app_exposes_library(media_client):
    _, app = media_client
    assert app.state.sounds.get("cow") is not None


def test_serves_clip_with_token(media_client):
    client, _ = media_client
    response = client.get("/v1/media/sounds/clips/cow.m4a", headers=AUTH)
    assert response.status_code == 200
    assert response.content == b"clip-cow"
    assert response.headers["content-type"] == "audio/mp4"


def test_serves_image_with_token(media_client):
    client, _ = media_client
    response = client.get("/v1/media/sounds/images/dog.jpg", headers=AUTH)
    assert response.status_code == 200
    assert response.content == b"image-dog"
    assert response.headers["content-type"] == "image/jpeg"


def test_clip_supports_range_requests(media_client):
    client, _ = media_client
    response = client.get("/v1/media/sounds/clips/cow.m4a", headers={**AUTH, "Range": "bytes=0-3"})
    assert response.status_code == 206
    assert response.content == b"clip"


def test_media_url_matches_manifest_path(media_client):
    _, app = media_client
    cow = app.state.sounds.get("cow")
    assert cow.clip_url_path() == "/v1/media/sounds/clips/cow.m4a"
    assert cow.image_url_path() == "/v1/media/sounds/images/cow.jpg"


@pytest.mark.parametrize(
    "path",
    ["/v1/media/sounds/clips/cow.m4a", "/v1/media/sounds/images/cow.jpg"],
)
def test_media_requires_token(media_client, path):
    client, _ = media_client
    response = client.get(path)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/v1/media/sounds/clips/horse.m4a",
        "/v1/media/sounds/images/horse.jpg",
        "/v1/media/sounds/clips/cow.jpg",
        "/v1/media/sounds/images/cow.m4a",
        "/v1/media/sounds/clips/..%2Fmanifest.json",
        "/v1/media/sounds/manifest.json",
        "/v1/media/sounds/other/cow.m4a",
    ],
)
def test_unknown_media_is_404(media_client, path):
    client, _ = media_client
    response = client.get(path, headers=AUTH)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_files_outside_manifest_are_not_served(media_client, tmp_path):
    client, _ = media_client
    (tmp_path / "sounds" / "clips" / "stray.m4a").write_bytes(b"stray")
    response = client.get("/v1/media/sounds/clips/stray.m4a", headers=AUTH)
    assert response.status_code == 404
