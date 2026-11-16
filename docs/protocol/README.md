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
`TimerInfo`, `PictureImage` and `ViewSpec` to avoid clashing with Foundation/SwiftUI, and
`Diagnostics` is `HubDiagnostics`.

## Transport, pairing and auth

- Base URL: `https://<hub-host>:8765`; the event stream is `wss://<hub-host>:8765/v1/events`.
  There is no plain-HTTP listener. The Hub advertises Bonjour service `_coco._tcp` on that port.
- **TLS with trust on first use.** On first run the Hub creates a self-signed EC P-256
  certificate, its key (mode 0600) and a random 16-hex-digit Hub ID in `<data_dir>/tls/`, and
  reuses them on every start. No CA is involved: the Display trusts the Hub because a person
  compared fingerprints when pairing.
  - **Fingerprint** = SHA-256 of the DER certificate. Human form = the first 24 hex digits,
    upper case, as six groups of four: `3F2A-91C0-7B4E-D815-A6C2-0E9F`. `make hub-fingerprint`
    prints it (and the Hub ID); the Hub also logs it at startup.
  - **Pin** (stored by the Display in the Keychain): Hub ID + full certificate SHA-256.
- **Pairing** (Display, when it has no pin):
  1. Find the Hub (manual host, or Bonjour) and `GET /healthz` over TLS **without** the token,
     accepting whatever certificate is presented and recording it.
  2. Show "Pair with Coco Hub?" with the fingerprint computed from that certificate (never the
     one in the response body or TXT record). The person checks it matches
     `make hub-fingerprint` on the Mac and taps Pair.
  3. Save the pin (`hub_id` from `/healthz` + certificate SHA-256). Only now is the token sent.

  A Display with no token and no pin looks for a Hub to pair with as above (the probe sends
  nothing secret); one with a pin but no token stops at "needs a token".
- **Easier pairing** (#89). `make hub-pair` (`python -m coco_hub --pair`) on the Hub's Mac prints,
  to its terminal only (never logged), a QR code, a one-time 6-digit code and the short
  fingerprint.
  - **QR code** (main way): the pairing URI
    `cocohub://pair?v=1&host=<LAN IPv4 hint>&port=<port>&token=<token>&cert=<64 hex>`. Query
    values are percent-encoded (`%20`, never `+`); `cert` is the full certificate SHA-256 in
    lowercase hex; the Display accepts only `v=1`. The Display probes `host` (then the Hub it
    discovered, then its manual host: the address is only a hint) and pairs only if the Hub
    there presents exactly the certificate `cert`: the pin is `hub_id` from that `/healthz` plus
    `cert`, and the token is `token`. No fingerprint comparison is needed. How the Display
    finds the Hub afterwards (Bonjour or manual host) is unchanged.
  - **6-digit code** (fallback): the person confirms the fingerprint as in step 2, enters the
    code, and the Display sends `POST /v1/pair` `{"code": "123456"}` without a bearer, over TLS
    pinned to that certificate (a different certificate cancels the handshake). The Hub answers
    `{"token": "..."}` and the Display saves the pin and the token. A code is valid for 2
    minutes, works once, and is locked after 5 wrong attempts; a new `make hub-pair` replaces
    it. The Hub stores only a salted hash in its database (the CLI and the server are separate
    processes) and compares in constant time. Refusals (error body as usual):

    | Status | `code` | When |
    |---|---|---|
    | 404 | `no_pairing_code` | none issued, or already used |
    | 401 | `pairing_code_wrong` | wrong code (attempts 1-4) |
    | 429 | `pairing_code_locked` | the 5th wrong attempt, and every attempt after it |
    | 410 | `pairing_code_expired` | more than 2 minutes old |
    | 422 | `invalid_request` | not exactly 6 digits (doesn't count as an attempt) |
  - Typing the token in the Display's Settings stays as the last resort.
- **Every later connection** (REST, the WebSocket, Hub media) runs through a TLS trust check
  that accepts the handshake only if the leaf certificate's SHA-256 equals the pin; otherwise
  the handshake is cancelled, so no request (and no token) is ever sent. Before connecting, the
  Display probes `/healthz` the same way as when pairing and requires both the certificate and
  `hub_id` to match the pin. A discovered Hub that doesn't match is ignored ("Unknown Coco Hub"
  notice) and discovery continues; a manual host that doesn't match stops with that notice.
- **Re-pairing**: "Forget Hub" in the Display's Settings deletes the pin and the token. A new
  certificate on the Hub (e.g. `data_dir` deleted, Hub moved to another Mac) always needs this.
- `tls.enabled = false` on the Hub serves plain HTTP/WS for tests and local development only;
  the Display can't pair with such a Hub.
- Every endpoint except `GET /healthz` and `POST /v1/pair` (whose credential is the pairing code)
  requires `Authorization: Bearer <token>` (shared LAN token from Hub settings;
  `hub/tests/test_pairing.py` checks every other `/v1` route refuses a request without it). The WebSocket takes the same header on its upgrade request; a token in the
  URL (`?token=`) is not accepted, because query strings end up in access logs. A missing or
  wrong token closes the WebSocket with code 1008 before it is accepted.
- Errors: non-2xx with body `{"error": {"code": "<snake_case>", "message": "<human text>"}}`
  (`fixtures/error.json`).

## Endpoints

| Method & path | Request | Response |
|---|---|---|
| `GET /healthz` | – | `Health` (unauthenticated) |
| `POST /v1/pair` | `PairRequest` (`fixtures/pair_request.json`) | `PairResponse` (`fixtures/pair_response.json`); no bearer, see "Easier pairing" |
| `POST /v1/utterance` | `UtteranceRequest` | `Action` |
| `GET /v1/idle` | – | `IdleInfo` |
| `GET /v1/timers` | – | `{"timers": [Timer], "server_time": ts}` |
| `POST /v1/timers` | `{"name": str \| null, "duration_s": int}` | `Timer` (touch-created timer) |
| `POST /v1/timers/{id}/dismiss` | – | `Timer` |
| `POST /v1/timers/{id}/add` | `{"seconds": int}` | `Timer` (time added; touch "+1 min") |
| `DELETE /v1/timers/{id}` | – | `Timer` (state `cancelled`) |
| `GET /v1/diagnostics` | – | `Diagnostics` |
| `GET /v1/settings/location` | – | `LocationSettings` (see "Home location") |
| `PUT /v1/settings/location` | `LocationQueryRequest` \| `LocationPlaceRequest` | `LocationSettings` |
| `DELETE /v1/settings/location` | – | `LocationSettings` (back to the config file's) |
| `WS /v1/events` | – | stream of `Event` |

## Health

```json
{"status": "ok", "hub_id": "5d0c8a91f3b2e746", "fingerprint": "3F2A-91C0-7B4E-D815-A6C2-0E9F"}
```

`GET /healthz` needs no token: it is liveness and the Hub's identity for pairing
(`fixtures/health.json`). `hub_id` is 16 lowercase hex digits and never changes for a Hub's
`data_dir`. `fingerprint` is the short certificate fingerprint, or `null` when the Hub runs
without TLS (`fixtures/health_no_tls.json`). Both are informational: the Display pins the
certificate it was actually served.

## Bonjour

Service type `_coco._tcp`, on the Hub's port. TXT record:

| key | value |
|---|---|
| `api` | `v1` |
| `id` | Hub ID (same as `/healthz` `hub_id`) |
| `fp` | short fingerprint; absent without TLS |

TXT records are unauthenticated (anyone on the LAN can advertise `_coco._tcp` with any TXT), so
they are advisory only: the Display decides trust from the certificate, never from TXT. Once
paired, the Display skips (does not even probe) a service whose TXT `id` differs from the pinned
Hub ID; a matching `id` still has to pass the pinned `/healthz` probe.

**Rediscovery (Display).** A Hub found with Bonjour that stops answering (5 failed attempts in a
row, or 30 s since the first failure, before or after connecting) is assumed to have moved (e.g.
a new DHCP address): the Display browses again and reconnects to the service with the pinned Hub
ID. It also browses again when it returns to the foreground or the network path changes while it
isn't connected. A manually entered host is never rediscovered: it keeps retrying and the banner
says "Can't reach <host> — check the Mac".

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
| `weather` | `current: WeatherNow`, `hourly: [WeatherHour]`, `daily: [WeatherDay]`, `place` (nullable) — see below |
| `picture` | `query`, `images: [Image]` (first shown, "next" advances) — see below |
| `sound` | `name`, `clip_url`, `image_url`, `attribution`, `caption` (nullable) — see below |
| `youtube` | `video_id`, `title` |
| `now_playing` | – (Display renders from now-playing events) |

#### `place` in `weather` views

`place` is `null` for home. For a place the person named ("what's the forecast in Portland", #93) it is how the Hub names it, e.g. `"Portland, Oregon"`, and the Display shows it as the screen's title. Its `hourly` times are instants as usual; its `daily` dates are that place's local dates. The `say` text names the place too, and says which one the Hub picked when the name was ambiguous.

#### Media URLs in `picture` and `sound` views

- **Picture** `Image.url` / `thumb_url` are **absolute public URLs** on Wikimedia Commons'
  media hosts (`upload.wikimedia.org`, `thumb.wikimedia.org`). Load them directly, with **no** bearer header.
  `thumb_url` is about 1280 px wide (or the original when that is smaller); `url` is the
  original file. `width`/`height` are the original's. The Hub only sends bitmap photos with
  a reusable licence (CC0, public domain, CC BY, CC BY-SA); show `license`, `author` and
  `title` as the credit.
- **Sound** `clip_url` / `image_url` may be **Hub-relative paths**
  (`/v1/media/sounds/clips/cow.m4a`, `/v1/media/sounds/images/cow.jpg`). Resolve them against
  the Hub base URL and send the `Authorization: Bearer` header over the pinned connection
  (they are protected endpoints;
  clips are `audio/mp4`, images `image/jpeg`). An absolute URL is used as is.
  `attribution` is a one-line credit for both (`Sound: A (CC0) · Picture: B (CC BY 2.0)`).

Hub behaviour (not a shape change):

- On "next" / "another" while a `picture` view is on screen, the Hub resends the same
  `picture` view with `images` rotated so the next image is first (`say` is `null`).
- On "again" while a `sound` view is on screen, the Hub resends the same `sound` view with
  `say: null`; receiving a `sound` view (even an identical one) plays the clip from the start.
- A `sound` view always comes with `say: null` (#94): the clip is the answer, and speech
  would talk over it. `caption` ("A fire truck goes nee naw!") is what the Hub used to say;
  the Display shows it under the name and never speaks it.

### Commands (`commands[].type`)

| type | fields | meaning |
|---|---|---|
| `alarm.stop` | – | silence ringing alarms (Hub has already dismissed them) |
| `media.pause` / `media.resume` / `media.next` / `media.stop` | `target`: `youtube` \| `music` \| `sound` | control local playback |
| `volume.set` | `level`: 0–100 | set output volume |
| `volume.change` | `delta`: −100–100 | relative volume |
| `screen.wake` | – | leave night-dark mode |
| `screen.sleep` | – | go dark now (night mode only) |
| `music.play` | `query`: str, `kind`: `artist` \| `album` \| `playlist` \| `song` \| `any` | Apple Music (#42): search the catalog and play the best match; `query` `""` resumes |

Hub behaviour (not a shape change): `music.play` is sent only with `music.provider =
"apple_music"`, after the media policy allowed the query, together with the `now_playing`
view (`fixtures/action_music_play.json`). With Apple Music the Hub also sends `media.*`
commands with target `music` (and `volume.*`) without driving Spotify; the Display's own
player handles them, and the Display reports its Apple Music now-playing locally (source
`apple_music`), not through the Hub. See `docs/apple-music.md`.

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

## Home location

The place home weather is for (the idle screen's forecast, and weather questions that name no
place), set from the Display's Settings (#96). A place set this way is stored in the Hub's
database and overrides `[location]` in the Hub's config file until it is deleted. The Hub's
`timezone` setting is not changed: home forecasts (and `WeatherDay.date`) stay Hub-local.

`LocationSettings` (`fixtures/location_settings*.json`):

```json
{"source": "override",
 "location": {"name": "Portland", "label": "Portland, Oregon", "lat": 45.52345,
              "lon": -122.67621, "timezone": "America/Los_Angeles", "country": "United States",
              "country_code": "US", "admin1": "Oregon"},
 "ambiguous": true, "alternatives": [HomeLocation …]}
```

- `source`: `override` (set from the Display) | `config` (the config file's `[location]`, which
  the Display calls "the Hub's default"). `location` is `null` when the source is `config` and
  the config file has no coordinates.
- `HomeLocation`: `name`, `label` (how to show it), `lat`, `lon`, `timezone` (IANA), and
  `country`, `country_code`, `admin1` (state or province), each nullable. For `config`, `name`
  and `label` are the config's `name` (`"Home"` when empty), `timezone` is the Hub's, and the
  rest are `null`.
- `ambiguous` and `alternatives` are only set in the reply to a `PUT` by name: the other matches
  for the name, most populous first (at most 5), and whether the name fitted more than one
  sizeable place (the same rule as "weather in Portland", #93). Otherwise `false` and `[]`.

`PUT` takes one of two bodies, told apart by their keys:

- `LocationQueryRequest` (`fixtures/location_update_query.json`): `{"query": "Portland, Oregon"}`.
  The Hub geocodes it (Open-Meteo, as for a named place), saves the best match at once, and
  answers with it and the alternatives. A comma or trailing words qualify the name by state,
  province or country.
- `LocationPlaceRequest` (`fixtures/location_update_place.json`): `name`, `lat`, `lon`,
  `timezone`, `country`, `country_code`, `admin1`: an alternative the person picked, saved as
  is. The Display sends every key; the Hub also accepts the last three missing.

Refusals (error body as usual):

| Status | `code` | When |
|---|---|---|
| 404 | `location_not_found` | nothing matches the name; the message says what to try |
| 503 | `geocoding_unavailable` | the Hub can't reach the geocoding service |
| 422 | `invalid_request` | a blank `query`, both bodies' keys mixed, coordinates out of range, an unknown `timezone` |

A refused `PUT` changes nothing. Setting or deleting the location drops the cached home forecast;
the next `GET /v1/idle` (or weather question) fetches the new one.

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

`POST /v1/timers/{id}/add` (`fixtures/timer_add_request.json`) follows the same rules as the
spoken "add N minutes": a `running` timer's `ends_at` moves out by `seconds`; a `ringing` one
restarts `seconds` from now (state back to `running`; the Hub sends `timer.updated`). `seconds`
must be > 0 (422 `invalid_request`), and the timer may not end more than 24 hours from now (422
`invalid_timer`). 404 `not_found` for an unknown id; 409 `conflict` for a dismissed or cancelled
timer.

## Events (WebSocket)

Every event has `type` and `server_time`.

| type | fields |
|---|---|
| `hello` | `timers: [Timer]`, `now_playing: NowPlaying \| null` — sent on connect |
| `timer.created` / `timer.updated` / `timer.fired` / `timer.dismissed` / `timer.cancelled` | `timer` |
| `now_playing` | `now_playing: NowPlaying \| null` |
| `settings.changed` | `night` (same shape as `IdleInfo.night`) |
| `spotify.wake_requested` | `device_name` (nullable): the iPad's Spotify Connect name |

`spotify.wake_requested` (#5, `fixtures/event_spotify_wake_requested.json`): Spotify doesn't
list the iPad, so the Hub asks the Display to open the Spotify app, then waits
`spotify.wake_wait_s` and lists devices again. The Display opens the `spotify:` URL scheme
only when Guided Access is off (and at most once a minute); under Guided Access it does
nothing and the Hub says "Open Spotify on the iPad once, then try again." The Display sends
nothing back. The Hub only publishes it while a Display is connected.

`NowPlaying`: `source` (`spotify`|`apple_music`), `title`, `artist`, `album` (nullable),
`artwork_url` (nullable), `is_playing`.

`Image`: `url`, `thumb_url`, `title`, `width`, `height`, `license`, `author` (nullable),
`source_url`.

## Diagnostics

`GET /v1/diagnostics` (protected) feeds the hidden diagnostics screen
(`fixtures/diagnostics.json`). It never contains a secret: settings are summarised as booleans
and names. It answers within about 1.5 s: each health check runs concurrently with a short
timeout, and a check that fails or times out reports `status: "error"` (`detail` says why)
instead of failing the request.

```json
{"server_time": "…", "version": "0.1.0", "git_sha": "3e190cd" | null,
 "started_at": "…", "uptime_s": 43500,
 "settings": {"timezone": "America/Toronto", "units": "metric", "location_set": true,
              "qwen": {"enabled": true, "model": "qwen3:8b"}, "youtube_configured": true,
              "spotify": {"configured": true, "logged_in": true},
              "night": Night, "log_transcripts": true},
 "health": {"ollama": OllamaHealth, "weather": WeatherHealth,
            "youtube": YoutubeHealth, "spotify": SpotifyHealth},
 "utterances": [UtteranceTrace …up to 20, newest first]}
```

- `settings.location_set` is true when the Hub has a home location from either source.
- `version`: Hub package version. `git_sha`: short commit, best-effort (`null` if unknown).
- Every health entry has `status` (`ok` | `warn` | `error` | `off`) and `detail`
  (nullable human text). `off` means not configured / disabled.
  - `OllamaHealth`: `reachable`, `model_present`, `model_loaded` (all `false` when `off`).
  - `WeatherHealth`: `fetched_at` (nullable), `stale`.
  - `YoutubeHealth`: `quota_used`, `quota_limit` (API units today, Pacific day).
  - `SpotifyHealth`: `device_visible`, `device_name` (nullable).
- `UtteranceTrace`: `ts`, `text`, `source`, `view` (the request's `context.view`), `intent`,
  `matched_by`, `say`, `latency_ms`, `error`. `intent`, `matched_by` and `say` are `null` and
  `error` is set (`"<ExceptionType>: <message>"`) when handling failed.

Hub behaviour (not a shape change): the Hub keeps the last 100 traces in memory only and logs
each at INFO to its local log file. With `diagnostics.log_transcripts = false` the log lines
omit `text` and `say`; this endpoint still returns them.
