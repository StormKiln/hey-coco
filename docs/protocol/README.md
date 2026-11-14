# Coco Hub ↔ Coco Display protocol (v1)

The Hub is authoritative for state (timers, settings, now-playing). The Display sends what it
heard and renders what the Hub tells it. All JSON, UTF-8, `snake_case` keys. Timestamps are
ISO 8601 UTC with a `Z` suffix and whole seconds (`2026-11-14T15:04:05Z`). Durations are
integer seconds.

- Every key is always present: a nullable field is sent as `null`, never omitted.
- Unknown `type` values (views, commands, events) are ignored by the Display (decoded as
  "unknown") so an older Display survives a newer Hub. The Hub rejects unknown types and
  unknown keys.
- Clock times (`night.start`/`end`) are Hub-local `HH:MM`; dates (`WeatherDay.date`) are
  Hub-local `YYYY-MM-DD`.

Every fixture in `fixtures/` is a valid example of one message. Both sides have contract tests
that decode every fixture, so a change here must update the Hub models
(`hub/src/coco_hub/protocol.py`) and the Swift models (`display/CocoKit/Sources/CocoKit/Protocol/`)
in the same PR. Fixtures are mapped to models by filename prefix (`action_*` → `Action`,
`event_*` → `Event`, `timer_*` → `Timer`, …). In Swift, `Timer`, `Image` and `View` are named
`TimerInfo`, `PictureImage` and `ViewSpec` to avoid clashing with Foundation/SwiftUI.

## Transport and auth

- Base URL: `http://<hub-host>:8765`. The Hub advertises Bonjour service `_coco._tcp` on that port.
- Every endpoint except `GET /healthz` requires `Authorization: Bearer <token>` (shared LAN token
  from Hub settings). The WebSocket takes the same header on its upgrade request; a token in the
  URL (`?token=`) is not accepted, because query strings end up in access logs. A missing or
  wrong token closes the WebSocket with code 1008 before it is accepted.
- Errors: non-2xx with body `{"error": {"code": "<snake_case>", "message": "<human text>"}}`
  (`fixtures/error.json`).

## Endpoints

| Method & path | Request | Response |
|---|---|---|
| `GET /healthz` | – | `{"status": "ok"}` |
| `POST /v1/utterance` | `UtteranceRequest` | `Action` |
| `GET /v1/idle` | – | `IdleInfo` |
| `GET /v1/timers` | – | `{"timers": [Timer], "server_time": ts}` |
| `POST /v1/timers` | `{"name": str \| null, "duration_s": int}` | `Timer` (touch-created timer) |
| `POST /v1/timers/{id}/dismiss` | – | `Timer` |
| `DELETE /v1/timers/{id}` | – | `Timer` (state `cancelled`) |
| `WS /v1/events` | – | stream of `Event` |

## UtteranceRequest

```json
{"text": "set a pasta timer for 10 minutes", "source": "voice",
 "context": {"view": "idle", "alarm_ringing": false, "media_playing": null}}
```

- `source`: `voice` | `touch`.
- `context.view`: the `ViewType` currently on screen.
- `context.media_playing`: `null` | `youtube` | `music` | `sound`. Lets "pause" / "next" target the
  right thing.

## Action

```json
{"say": "Pasta timer set for 10 minutes.", "view": {"type": "timers"},
 "commands": [], "intent": "timer.create", "matched_by": "rules"}
```

- `say`: text to speak and show, or `null` for silent actions.
- `view`: what to show (below), or `null` to keep the current screen.
- `commands`: things only the Display can do (below). Applied in order, before `say`.
- `intent`: dotted intent name (`timer.create`, `weather.current`, `general.answer`, `none`, …).
- `matched_by`: `rules` | `qwen` | `none`.

### Views (`view.type`)

| type | payload |
|---|---|
| `idle` | – |
| `answer` | `text` |
| `timers` | – (Display renders from its timer state) |
| `weather` | `current: WeatherNow`, `hourly: [WeatherHour]`, `daily: [WeatherDay]` |
| `picture` | `query`, `images: [Image]` (first shown, "next" advances) — see below |
| `sound` | `name`, `clip_url`, `image_url`, `attribution` — see below |
| `youtube` | `video_id`, `title` |
| `now_playing` | – (Display renders from now-playing events) |

#### Media URLs in `picture` and `sound` views

- **Picture** `Image.url` / `thumb_url` are **absolute public URLs** on Wikimedia Commons'
  media hosts (`upload.wikimedia.org`, `thumb.wikimedia.org`). Load them directly, with **no** bearer header.
  `thumb_url` is about 1280 px wide (or the original when that is smaller); `url` is the
  original file. `width`/`height` are the original's. The Hub only sends bitmap photos with
  a reusable licence (CC0, public domain, CC BY, CC BY-SA); show `license`, `author` and
  `title` as the credit.
- **Sound** `clip_url` / `image_url` may be **Hub-relative paths**
  (`/v1/media/sounds/clips/cow.m4a`, `/v1/media/sounds/images/cow.jpg`). Resolve them against
  the Hub base URL and send the `Authorization: Bearer` header (they are protected endpoints;
  clips are `audio/mp4`, images `image/jpeg`). An absolute URL is used as is.
  `attribution` is a one-line credit for both (`Sound: A (CC0) · Picture: B (CC BY 2.0)`).

Hub behaviour (not a shape change):

- On "next" / "another" while a `picture` view is on screen, the Hub resends the same
  `picture` view with `images` rotated so the next image is first (`say` is `null`).
- On "again" while a `sound` view is on screen, the Hub resends the same `sound` view with
  `say: null`; receiving a `sound` view (even an identical one) plays the clip from the start.

### Commands (`commands[].type`)

| type | fields | meaning |
|---|---|---|
| `alarm.stop` | – | silence ringing alarms (Hub has already dismissed them) |
| `media.pause` / `media.resume` / `media.next` / `media.stop` | `target`: `youtube` \| `music` \| `sound` | control local playback |
| `volume.set` | `level`: 0–100 | set output volume |
| `volume.change` | `delta`: −100–100 | relative volume |
| `screen.wake` | – | leave night-dark mode |
| `screen.sleep` | – | go dark now (night mode only) |

## IdleInfo

```json
{"server_time": "…", "timezone": "America/Toronto",
 "weather": {"current": WeatherNow, "hourly": [WeatherHour …6+], "stale": false} | null,
 "night": {"start": "21:30", "end": "06:30", "dark_after_idle_s": 300}}
```

`weather` is `null` when the Hub has never fetched it; `stale: true` when the last fetch failed
and an old value is shown.

### Weather types

- `WeatherNow`: `time`, `temp`, `feels_like`, `unit` (`C`|`F`), `code` (WMO weather code),
  `description`, `is_day`.
- `WeatherHour`: `time`, `temp`, `unit`, `code`, `precip_probability` (0–100).
- `WeatherDay`: `date` (`YYYY-MM-DD`), `high`, `low`, `unit`, `code`, `precip_probability`.

## Timer

```json
{"id": "t_7f3a", "name": "pasta", "duration_s": 600, "created_at": "…", "ends_at": "…",
 "state": "running"}
```

`name` is nullable (unnamed timer). `state`: `running` | `ringing` | `dismissed` | `cancelled`. The Display schedules a local alarm
for every `running` timer's `ends_at` so it rings even if the Hub is unreachable.

Hub behaviour (not a shape change): the Hub normalises names (`"The Pasta timer"` → `pasta`)
and gives unnamed timers a default name (`timer`, then `timer 2`, … — the lowest free among
active timers), so timers it sends normally have a name; `null` stays valid. `duration_s` is
always `ends_at − created_at` (adding time grows it). A timer rings for at most
`timers.ring_cap_s` (default 600) before the Hub dismisses it. Timer endpoints return 404
`not_found` for an unknown id and 409 `conflict` for an invalid transition (dismissing a
running timer, cancelling a dismissed one); dismissing a dismissed or cancelling a cancelled
timer is a no-op that returns it.

## Events (WebSocket)

Every event has `type` and `server_time`.

| type | fields |
|---|---|
| `hello` | `timers: [Timer]`, `now_playing: NowPlaying \| null` — sent on connect |
| `timer.created` / `timer.updated` / `timer.fired` / `timer.dismissed` / `timer.cancelled` | `timer` |
| `now_playing` | `now_playing: NowPlaying \| null` |
| `settings.changed` | `night` (same shape as `IdleInfo.night`) |

`NowPlaying`: `source` (`spotify`|`apple_music`), `title`, `artist`, `album` (nullable),
`artwork_url` (nullable), `is_playing`.

`Image`: `url`, `thumb_url`, `title`, `width`, `height`, `license`, `author` (nullable),
`source_url`.
