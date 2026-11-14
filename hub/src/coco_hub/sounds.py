"""Curated animal and vehicle sound library.

The library lives in a directory (``settings.sounds_dir``, default ``assets/sounds`` in the
repo) holding ``manifest.json``, ``clips/<id>.m4a`` and ``images/<id>.jpg``. It is built by
``assets/sounds/tools/curate.py``; this module only reads and validates it.

Clips and pictures are served (behind the bearer token) at
``/v1/media/sounds/clips/<id>.m4a`` and ``/v1/media/sounds/images/<id>.jpg``.
"""

import json
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from coco_hub.errors import HubError

MEDIA_PREFIX = "/v1/media/sounds"

# Keep in sync with assets/sounds/tools/curate.py. No NC, ND, GFDL-only or unknown licences.
ALLOWED_LICENSES = frozenset(
    {
        "CC0",
        "Public domain",
        "CC BY 1.0",
        "CC BY 2.0",
        "CC BY 2.5",
        "CC BY 3.0",
        "CC BY 4.0",
        "CC BY-SA 1.0",
        "CC BY-SA 2.0",
        "CC BY-SA 2.5",
        "CC BY-SA 3.0",
        "CC BY-SA 4.0",
    }
)

_CLIP_PATH = re.compile(r"^clips/[a-z0-9_]+\.m4a$")
_IMAGE_PATH = re.compile(r"^images/[a-z0-9_]+\.jpg$")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_ARTICLES = ("a ", "an ", "the ", "some ")


class ManifestError(ValueError):
    """The sound manifest is missing, malformed, or points at files that are not there."""


class Attribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    author: str
    license: str
    license_url: str
    source_url: str


class Sound(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    name: str
    aliases: tuple[str, ...] = ()
    category: Literal["animal", "vehicle"]
    clip: str
    image: str
    duration_s: float
    audio_attribution: Attribution
    image_attribution: Attribution

    def clip_url_path(self) -> str:
        return f"{MEDIA_PREFIX}/{self.clip}"

    def image_url_path(self) -> str:
        return f"{MEDIA_PREFIX}/{self.image}"

    def attribution_text(self) -> str:
        """One-line credit for the Display, e.g. ``Sound: A (CC0) · Picture: B (CC BY 2.0)``."""
        a, i = self.audio_attribution, self.image_attribution
        return f"Sound: {a.author} ({a.license}) · Picture: {i.author} ({i.license})"


def normalise(text: str) -> str:
    """Lowercase, punctuation to spaces, collapse whitespace, drop a leading article."""
    words = _NON_WORD.sub(" ", text.lower()).strip()
    for article in _ARTICLES:
        if words.startswith(article):
            words = words[len(article) :]
            break
    return words


def _singulars(key: str) -> list[str]:
    """Naive English singulars of the last word: kitties→kitty, buses→bus, cows→cow."""
    out = []
    if key.endswith("ies"):
        out.append(key[:-3] + "y")
    if key.endswith("s") and not key.endswith("ss"):
        out.append(key[:-1])
    if key.endswith("es"):
        out.append(key[:-2])
    return out


class SoundLibrary:
    def __init__(self, root: Path, sounds: list[Sound]) -> None:
        self.root = root
        self._sounds = list(sounds)
        self._by_id = {s.id: s for s in self._sounds}
        self._by_key: dict[str, Sound] = {}
        self._by_compact_key: dict[str, Sound] = {}
        self._media = {s.clip: s for s in self._sounds} | {s.image: s for s in self._sounds}
        for sound in self._sounds:
            for key in {normalise(k) for k in (sound.name, *sound.aliases)} - {""}:
                for index, k in ((self._by_key, key), (self._by_compact_key, key.replace(" ", ""))):
                    other = index.setdefault(k, sound)
                    if other.id != sound.id:
                        raise ManifestError(
                            f"name/alias {k!r} is used by both {other.id!r} and {sound.id!r}"
                        )

    @classmethod
    def load(cls, root: Path) -> "SoundLibrary":
        """Load and validate ``root/manifest.json``. Raises ManifestError on any problem."""
        root = Path(root)
        manifest = root / "manifest.json"
        try:
            data = json.loads(manifest.read_text())
            entries = data["sounds"]
        except FileNotFoundError as exc:
            raise ManifestError(f"no sound library manifest at {manifest}") from exc
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ManifestError(f"{manifest} is not a valid sound manifest: {exc}") from exc

        sounds: list[Sound] = []
        seen: set[str] = set()
        for raw in entries:
            try:
                sound = Sound.model_validate(raw)
            except ValidationError as exc:
                raise ManifestError(f"invalid manifest entry {raw.get('id')!r}: {exc}") from exc
            if sound.id in seen:
                raise ManifestError(f"duplicate id {sound.id!r}")
            seen.add(sound.id)
            for attribution in (sound.audio_attribution, sound.image_attribution):
                if attribution.license not in ALLOWED_LICENSES:
                    raise ManifestError(
                        f"{sound.id}: licence {attribution.license!r} is not allowed"
                    )
            if not _CLIP_PATH.match(sound.clip):
                raise ManifestError(f"{sound.id}: clip path {sound.clip!r} is not clips/<id>.m4a")
            if not _IMAGE_PATH.match(sound.image):
                raise ManifestError(
                    f"{sound.id}: image path {sound.image!r} is not images/<id>.jpg"
                )
            for rel in (sound.clip, sound.image):
                if not (root / rel).is_file():
                    raise ManifestError(f"{sound.id}: missing file {rel}")
            sounds.append(sound)
        return cls(root, sounds)

    def all(self) -> list[Sound]:
        return list(self._sounds)

    def get(self, sound_id: str) -> Sound | None:
        return self._by_id.get(sound_id)

    def find(self, name: str) -> Sound | None:
        """Match a spoken name or alias: case-, punctuation-, article- and plural-tolerant."""
        key = normalise(name)
        if not key:
            return None
        for candidate in (key, *_singulars(key)):
            if sound := self._by_key.get(candidate):
                return sound
        for candidate in (key, *_singulars(key)):
            if sound := self._by_compact_key.get(candidate.replace(" ", "")):
                return sound
        return None

    def media_file(self, rel: str) -> Path | None:
        """Absolute path of a clip/image named in the manifest, else None."""
        return self.root / rel if rel in self._media else None

    def clip_path(self, sound: Sound) -> Path:
        return self.root / sound.clip

    def image_path(self, sound: Sound) -> Path:
        return self.root / sound.image


_MEDIA_TYPES = {"clips": "audio/mp4", "images": "image/jpeg"}

media_router = APIRouter(prefix="/media/sounds")


@media_router.get("/{kind}/{filename}")
def sound_media(kind: str, filename: str, request: Request) -> FileResponse:
    """Serve a clip or picture listed in the manifest (only those; no directory browsing)."""
    library: SoundLibrary = request.app.state.sounds
    path = library.media_file(f"{kind}/{filename}") if kind in _MEDIA_TYPES else None
    if path is None:
        raise HubError(404, "not_found", "No such sound media")
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[kind],
        headers={"Cache-Control": "private, max-age=86400"},
    )
