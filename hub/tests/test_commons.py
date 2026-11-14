"""Wikimedia Commons picture search: parsing, filtering, the HTTP client and the cache.

``fixtures/commons/*.json`` are real API responses recorded by hand with curl (see
``CommonsClient.search`` for the parameters); everything else is crafted.
"""

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from coco_hub.pictures.commons import (
    USER_AGENT,
    CommonsClient,
    PictureFetchError,
    PictureService,
    license_allowed,
    parse_results,
    search_terms,
    strip_html,
)
from coco_hub.protocol import Image

FIXTURES = Path(__file__).parent / "fixtures" / "commons"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def page(
    index: int,
    title: str = "File:Red panda.jpg",
    *,
    mime: str = "image/jpeg",
    width: int = 2000,
    height: int = 1500,
    license: str | None = "cc-by-sa-4.0",
    short: str | None = "CC BY-SA 4.0",
    artist: str | None = '<a href="//commons.wikimedia.org/wiki/User:Jo">Jo</a>',
    object_name: str | None = None,
    categories: str = "Ailurus fulgens|Self-published work",
) -> dict[str, Any]:
    meta: dict[str, Any] = {"Categories": {"value": categories}}
    if license is not None:
        meta["License"] = {"value": license}
    if short is not None:
        meta["LicenseShortName"] = {"value": short}
    if artist is not None:
        meta["Artist"] = {"value": artist}
    if object_name is not None:
        meta["ObjectName"] = {"value": object_name}
    name = title.removeprefix("File:").replace(" ", "_")
    return {
        "ns": 6,
        "title": title,
        "index": index,
        "imageinfo": [
            {
                "width": width,
                "height": height,
                "mime": mime,
                "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{name}"
                "?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original",
                "thumburl": f"https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/{name}"
                f"/1280px-{name}?utm_source=commons.wikimedia.org",
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}",
                "extmetadata": meta,
            }
        ],
    }


def response(*pages: dict[str, Any]) -> dict[str, Any]:
    return {"batchcomplete": True, "query": {"pages": list(pages)}}


# --- parsing the recorded responses ------------------------------------------------------


def test_recorded_fire_truck_response_parses_to_eight_good_images():
    images = parse_results(load("fire_truck.json"), query="fire truck")
    assert len(images) == 8
    for image in images:
        assert isinstance(image, Image)
        assert image.url.startswith("https://upload.wikimedia.org/")
        assert re.match(r"https://(upload|thumb)\.wikimedia\.org/", image.thumb_url)
        assert "utm_" not in image.url and "utm_" not in image.thumb_url
        assert image.source_url.startswith("https://commons.wikimedia.org/wiki/File:")
        assert min(image.width, image.height) >= 480
        assert image.license in {"CC0", "Public domain"} or image.license.startswith("CC BY")
        assert "<" not in image.title and "<" not in (image.author or "")


def test_recorded_response_keeps_search_rank_order():
    # The API returns pages in arbitrary order; `index` is the search rank.
    data = load("fire_truck.json")
    rank = {p["imageinfo"][0]["descriptionurl"]: p["index"] for p in data["query"]["pages"]}
    ranks = [rank[i.source_url] for i in parse_results(data, query="fire truck")]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1


def test_recorded_axolotl_drops_tiny_and_panorama_and_prefers_photos():
    images = parse_results(load("axolotl.json"), query="axolotl")
    titles = [i.title for i in images]
    assert len(images) == 8
    # Axolotl.jpg is 270x202 → dropped; "Axolotl Wild Type" is a 4760x1324 panorama → dropped.
    assert "Axolotl" not in titles
    assert not any("Wild Type" in t for t in titles)
    # The one PNG (rank 10) would make the cut by rank but photos come first.
    assert not any(i.url.endswith(".png") for i in images)


def test_recorded_author_html_is_stripped():
    images = parse_results(load("fire_truck.json"), query="fire truck")
    syced = [i for i in images if i.author == "Syced"]
    assert syced, [i.author for i in images]


# --- filtering ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "short", "ok"),
    [
        ("cc0", "CC0", True),
        ("pd", "Public domain", True),
        ("cc-by-2.0", "CC BY 2.0", True),
        ("cc-by-4.0", "CC BY 4.0", True),
        ("cc-by-sa-3.0", "CC BY-SA 3.0", True),
        ("cc-by-sa-3.0-de", "CC BY-SA 3.0 de", True),
        ("cc-by-sa-2.5,2.0,1.0", "CC BY-SA 2.5", True),
        ("cc-by-nc-2.0", "CC BY-NC 2.0", False),
        ("cc-by-nd-4.0", "CC BY-ND 4.0", False),
        ("cc-by-nc-sa-4.0", "CC BY-NC-SA 4.0", False),
        ("gfdl", "GFDL", False),
        ("fal", "Free Art License", False),
        (None, "CC BY-SA 4.0", True),
        (None, "Public domain", True),
        (None, "CC BY-NC 2.0", False),
        (None, None, False),
        ("", "", False),
    ],
)
def test_license_allowed(code, short, ok):
    assert license_allowed(code, short) is ok


def test_disallowed_licences_are_dropped():
    data = response(
        page(1, "File:A.jpg", license="cc-by-nc-2.0", short="CC BY-NC 2.0"),
        page(2, "File:B.jpg", license="gfdl", short="GFDL"),
        page(3, "File:C.jpg", license=None, short=None),
        page(4, "File:D.jpg", license="cc0", short="CC0"),
    )
    assert [i.title for i in parse_results(data, query="d")] == ["D"]


@pytest.mark.parametrize(
    ("mime", "width", "height", "kept"),
    [
        ("image/jpeg", 2000, 1500, True),
        ("image/png", 2000, 1500, True),
        ("image/webp", 1280, 960, True),
        ("image/gif", 2000, 1500, False),
        ("image/svg+xml", 2000, 1500, False),
        ("image/tiff", 4000, 3000, False),
        ("application/pdf", 2000, 1500, False),
        ("video/webm", 1920, 1080, False),
        ("image/jpeg", 640, 480, True),
        ("image/jpeg", 639, 479, False),
        ("image/jpeg", 600, 400, False),
        ("image/jpeg", 480, 640, True),
        ("image/jpeg", 4000, 1000, False),  # panorama
    ],
)
def test_mime_and_size_filtering(mime, width, height, kept):
    data = response(page(1, "File:X.jpg", mime=mime, width=width, height=height))
    assert bool(parse_results(data, query="x")) is kept


@pytest.mark.parametrize(
    ("title", "categories"),
    [
        ("File:Red panda range map.png", "Maps of Ailurus fulgens"),
        ("File:Red panda diagram.jpg", "Ailurus fulgens"),
        ("File:Red panda.jpg", "Drawings of Ailurus fulgens"),
        ("File:Red panda skeleton illustration.jpg", "Ailurus fulgens"),
        ("File:Red panda.jpg", "Scientific illustrations"),
        ("File:Red panda logo.png", "Logos"),
        ("File:Red panda.jpg", "Nude photography"),
    ],
)
def test_diagrams_maps_drawings_and_unsuitable_are_dropped(title, categories):
    data = response(page(1, title, categories=categories), page(2, "File:Good.jpg"))
    assert [i.title for i in parse_results(data, query="red panda")] == ["Good"]


def test_a_map_is_fine_when_asked_for_one():
    data = response(page(1, "File:Canada map.jpg", categories="Maps of Canada"))
    assert [i.title for i in parse_results(data, query="map of canada")] == ["Canada map"]


def test_photos_rank_before_pngs_and_search_rank_is_kept_otherwise():
    data = response(
        page(3, "File:Three.jpg"),
        page(1, "File:One.png", mime="image/png"),
        page(2, "File:Two.jpg"),
        page(4, "File:Four.webp", mime="image/webp"),
    )
    assert [i.title for i in parse_results(data, query="x")] == ["Two", "Three", "Four", "One"]


def test_at_most_eight_images():
    data = response(*(page(n, f"File:P{n}.jpg") for n in range(1, 21)))
    images = parse_results(data, query="x")
    assert [i.title for i in images] == [f"P{n}" for n in range(1, 9)]


def test_empty_and_odd_responses_give_no_images():
    assert parse_results({"batchcomplete": True}, query="x") == []
    assert parse_results({"query": {"pages": []}}, query="x") == []
    broken = page(1, "File:Broken.jpg")
    del broken["imageinfo"]
    weird = page(2, "File:Weird.jpg")
    weird["imageinfo"][0]["width"] = "big"
    assert parse_results(response(broken, weird), query="x") == []


# --- text cleanup ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ('<a href="//commons.wikimedia.org/wiki/User:Syced" title="User:Syced">Syced</a>', "Syced"),
        ("Brandon Segura Torres &amp; Priscilla Vieto", "Brandon Segura Torres & Priscilla Vieto"),
        ("<span>Jo</span>\n  <i>Smith</i> ", "Jo Smith"),
        ('<div class="fn"><bdi>Kim &quot;K&quot; Hansen</bdi></div>', 'Kim "K" Hansen'),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_strip_html(raw, clean):
    assert strip_html(raw) == clean


def test_titles_and_authors_are_cleaned():
    data = response(
        page(1, "File:Red_panda_(24986761703).jpg", object_name="<b>Red Panda</b>"),
        page(2, "File:Another_red_panda.JPEG", artist=None),
        page(3, "File:Third.jpg", artist="<span>  </span>"),
        page(4, "File:Fourth.jpg", artist="x" * 300),
    )
    images = parse_results(data, query="red panda")
    assert [i.title for i in images] == ["Red Panda", "Another red panda", "Third", "Fourth"]
    assert images[0].author == "Jo"
    assert images[1].author is None
    assert images[2].author is None
    assert images[3].author is not None and len(images[3].author) <= 120
    assert images[0].license == "CC BY-SA 4.0"
    assert images[0].url == (
        "https://upload.wikimedia.org/wikipedia/commons/a/ab/Red_panda_(24986761703).jpg"
    )


def test_licence_label_falls_back_to_the_code():
    data = response(page(1, "File:A.jpg", license="cc-by-2.0", short=None))
    assert parse_results(data, query="a")[0].license == "CC BY 2.0"


@pytest.mark.parametrize(
    ("query", "terms"),
    [
        ("fire truck", '"fire truck" filetype:bitmap'),
        ('red "panda"', '"red panda" filetype:bitmap'),
        ("insource:/x/ cat", '"insource x cat" filetype:bitmap'),
        ("  Penguins  ", '"penguins" filetype:bitmap'),
        ("crème brûlée", '"crème brûlée" filetype:bitmap'),
    ],
)
def test_search_terms_are_a_quoted_phrase_with_no_search_syntax(query, terms):
    assert search_terms(query) == terms


def test_search_terms_unquoted():
    assert search_terms("fire truck", phrase=False) == "fire truck filetype:bitmap"


# --- HTTP client -------------------------------------------------------------------------


class FakeCommons:
    """MockTransport handler: answers by search terms, records requests."""

    def __init__(self, answers: dict[str, Any] | None = None, *, status: int = 200) -> None:
        self.answers = answers or {}
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text="nope")
        terms = request.url.params["gsrsearch"]
        return httpx.Response(200, json=self.answers.get(terms, {"batchcomplete": True}))

    def client(self) -> CommonsClient:
        return CommonsClient(transport=httpx.MockTransport(self))


@pytest.mark.anyio
async def test_client_sends_the_right_query():
    fake = FakeCommons({'"fire truck" filetype:bitmap': load("fire_truck.json")})
    images = await fake.client().search("fire truck")
    assert len(images) == 8
    [req] = fake.requests
    assert req.headers["user-agent"] == USER_AGENT
    assert req.url.host == "commons.wikimedia.org"
    params = req.url.params
    assert params["action"] == "query"
    assert params["generator"] == "search"
    assert params["gsrnamespace"] == "6"
    assert params["prop"] == "imageinfo"
    assert set(params["iiprop"].split("|")) >= {"url", "size", "mime", "extmetadata"}
    assert params["iiurlwidth"] == "1280"
    assert params["format"] == "json"


@pytest.mark.anyio
async def test_client_retries_without_quotes_when_the_phrase_finds_nothing():
    fake = FakeCommons({"red panda filetype:bitmap": response(page(1, "File:Loose.jpg"))})
    images = await fake.client().search("red panda")
    assert [i.title for i in images] == ["Loose"]
    assert [r.url.params["gsrsearch"] for r in fake.requests] == [
        '"red panda" filetype:bitmap',
        "red panda filetype:bitmap",
    ]


@pytest.mark.anyio
async def test_client_single_word_does_not_retry():
    fake = FakeCommons()
    assert await fake.client().search("zzzz") == []
    assert len(fake.requests) == 1


@pytest.mark.anyio
async def test_client_http_error_raises_fetch_error():
    with pytest.raises(PictureFetchError):
        await FakeCommons(status=503).client().search("cow")


@pytest.mark.anyio
async def test_client_bad_json_raises_fetch_error():
    client = CommonsClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<")))
    with pytest.raises(PictureFetchError):
        await client.search("cow")


@pytest.mark.anyio
async def test_client_api_error_raises_fetch_error():
    body = {"error": {"code": "maxlag", "info": "Waiting"}}
    client = CommonsClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(PictureFetchError):
        await client.search("cow")


@pytest.mark.anyio
async def test_client_connection_error_raises_fetch_error():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(PictureFetchError):
        await CommonsClient(transport=httpx.MockTransport(boom)).search("cow")


@pytest.mark.anyio
async def test_unsuitable_query_never_hits_the_network():
    fake = FakeCommons()
    assert await fake.client().search("naked people") == []
    assert await fake.client().search("   ") == []
    assert fake.requests == []


# --- service: cache and last result ------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def fire_truck_fake() -> FakeCommons:
    return FakeCommons({'"fire truck" filetype:bitmap': load("fire_truck.json")})


@pytest.mark.anyio
async def test_service_caches_for_a_day(fire_truck_fake):
    clock = Clock()
    service = PictureService(fire_truck_fake.client(), clock=clock)
    first = await service.search("fire truck")
    assert await service.search("Fire Truck ") == first
    clock.now += timedelta(hours=23)
    await service.search("fire truck")
    assert len(fire_truck_fake.requests) == 1
    clock.now += timedelta(hours=2)
    await service.search("fire truck")
    assert len(fire_truck_fake.requests) == 2


@pytest.mark.anyio
async def test_service_caches_empty_results_but_not_errors():
    fake = FakeCommons()
    service = PictureService(fake.client(), clock=Clock())
    assert await service.search("zzzz") == []
    assert await service.search("zzzz") == []
    assert len(fake.requests) == 1

    failing = FakeCommons(status=500)
    service = PictureService(failing.client(), clock=Clock())
    for _ in range(2):
        with pytest.raises(PictureFetchError):
            await service.search("cow")
    assert len(failing.requests) == 2


@pytest.mark.anyio
async def test_service_cache_is_bounded():
    fake = FakeCommons()
    service = PictureService(fake.client(), clock=Clock(), max_entries=2)
    for q in ("aa", "bb", "cc", "aa"):
        await service.search(q)
    assert len(fake.requests) == 4  # "aa" was evicted by "cc"


@pytest.mark.anyio
async def test_service_cached_list_cannot_be_mutated_by_callers(fire_truck_fake):
    service = PictureService(fire_truck_fake.client(), clock=Clock())
    first = await service.search("fire truck")
    first.clear()
    assert len(await service.search("fire truck")) == 8


def test_service_last_result_and_advance():
    service = PictureService(CommonsClient(), clock=Clock())
    assert service.last is None
    assert service.advance() is None
    images = parse_results(load("fire_truck.json"), query="fire truck")[:3]
    service.remember("fire truck", images)
    assert service.last is not None and service.last.query == "fire truck"
    assert service.last.current == images
    nxt = service.advance()
    assert nxt is not None and nxt.current == [images[1], images[2], images[0]]
    assert service.advance().current[0] == images[2]  # type: ignore[union-attr]
    assert service.advance().current[0] == images[0]  # type: ignore[union-attr]
    one = copy.deepcopy(images[:1])
    service.remember("cow", one)
    assert service.advance() is None  # only one picture: nothing to advance to
    assert service.last.current == one  # type: ignore[union-attr]
