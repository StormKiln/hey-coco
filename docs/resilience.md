# Resilience checks

How we prove the design's anti-goals (#44): a timer never fails silently, the clock and timers
never depend on the internet, and Coco says "sorry" instead of hanging when something upstream
is down.

There are three layers:

1. **Hub, automated** (`make hub-resilience`, runs in CI): the real Hub as a subprocess.
2. **Display, automated** (`make kit-test`, runs in CI): CocoKit unit tests.
3. **On-device, manual**: a short checklist to run in the kitchen after a big change.

## 1. Hub: `make hub-resilience`

`hub/tests/resilience/` starts the real Hub (`python -m coco_hub`) with a temp config: a random
free port on `127.0.0.1`, a temp data and log dir, discovery off, a generated token, and Qwen
and Open-Meteo pointed at localhost ports. TLS is on, as in the kitchen: the test clients trust
only that Hub's own certificate (`data_dir/tls/hub.crt`), like a paired iPad (#54). Each upstream can be down in two ways:

- **refused**: nothing listens on the port (Ollama is stopped, or DNS or the route fails fast);
- **black hole**: the connection opens but nothing ever answers (a dead upstream behind working
  Wi-Fi). Requests hang until they time out.

Open-Meteo's URL is the `weather.forecast_url` setting (`COCO_WEATHER__FORECAST_URL`). Only tests
change it. The tests never touch the host's firewall, network settings, launchd or a real Ollama.

These tests are marked `resilience` and left out of `make hub-test` because they take about 30 s.

| Scenario | What is checked |
| --- | --- |
| Hub killed mid-timer | Create a 3 s timer, `SIGKILL` the Hub, wait 5 s, restart. The timer is `ringing` (it ended within `timers.missed_grace_s`), and a new `WS /v1/events` client gets it in `hello`. The DB passes `PRAGMA integrity_check`. |
| Missed by a long gap | A running timer whose end is 2 h in the past (more than the 1 h grace, seeded into the DB) is dismissed silently at startup. It is not in `GET /v1/timers` or `hello`. |
| Graceful restart | `SIGTERM` (what launchd sends) stops the Hub within 5 s, with or without an event client connected. The lifespan shutdown runs, the DB is closed (WAL checkpointed), `integrity_check` is ok, and after a restart `GET /v1/timers` returns the same timers. |
| Event stream reconnect | A connected client sees the socket close on `SIGTERM` or `SIGKILL`. After the restart it reconnects and gets a fresh `hello` with the current timers, then live events again. |
| Internet down (refused and black hole) | `GET /v1/idle` answers in under 1 s with `weather: null`. "What time is it" and "set a timer for 1 minute" work in under 1 s. "What's the weather" says "Sorry, I can't reach the weather service right now." (under 1 s if refused, about 3 s for a black hole). Diagnostics shows weather `error`. |
| TLS identity across a crash | `SIGKILL` and restart: `/healthz` reports the same `hub_id` and fingerprint and the same certificate is served, so a paired iPad keeps working. `tls.enabled = false` still serves plain HTTP/WS. |
| Ollama down (refused) | An unmatched utterance says "Sorry, I can't do that yet." in under 1 s. Rules still match (`matched_by: rules`). Diagnostics shows ollama `error`, not reachable. |
| Ollama hanging (black hole) | An unmatched utterance says "Sorry, that took too long." within `qwen.timeout_s` (6 s). Rules stay fast, and diagnostics answers within its 1.5 s check timeout. |

**Clock jumps** (Mac sleep and wake, NTP corrections) run at unit level with an injectable clock,
because a subprocess can't move the system clock: `hub/tests/resilience/test_clock_jumps.py`.
They are part of `make hub-test`. The timer runner re-checks at least every 30 s, so without
any wake:

- a jump past a timer's end fires it;
- a jump past the 1 h grace dismisses it silently;
- a jump past the 10 min ring cap dismisses a ringing timer;
- a backward jump fires nothing early and loses nothing;
- a jump decades into the past doesn't crash the runner.

Fast unit tests cover the same failure paths elsewhere: weather stale-on-failure and back-off
(`tests/weather/`), Qwen unavailable and timeouts (`tests/qwen/`), Spotify and YouTube error
paths (`tests/spotify/`, `tests/media/`), and slow event subscribers (`tests/test_events.py`).

### Bugs found and fixed (#44)

- **A black-holed internet stalled the idle screen.** `WeatherService.get()` waited on the fetch
  for the HTTP client's full 10 s read timeout, holding a lock. So every `/v1/idle` request
  (and weather question) waited behind it, about once a minute after each back-off. Now `get`
  waits at most `max_wait` and otherwise answers from the cache while the fetch finishes in the
  background. `/v1/idle` waits 0.5 s and weather questions wait 3 s. Only one fetch runs at a time.
- **`import coco_hub.timers.service` failed on its own** with a circular import
  (`coco_hub.intents` imported every feature module eagerly). It only worked because something
  else had imported `coco_hub.intents` first, so `pytest tests/test_timer_service.py` run alone
  errored. The intent modules are now imported when the registry is built.
  `tests/test_imports.py` imports every module in a fresh interpreter.

### Known limits (not fixed)

- If the wall clock jumps **backwards** while a timer is ringing, the 10 min ring cap counts
  from the old time, so it rings longer. Big backward jumps don't happen in normal use: the Hub
  works in UTC, and DST doesn't move UTC.

## 2. Display: CocoKit unit tests (`make kit-test`)

The iPad behaviour is covered by CocoKit tests on macOS:

- **Local alarms** (`TimerStoreTests`, `AlarmSchedulerTests`): every Hub timer is also scheduled
  locally, so it rings with the Hub stopped mid-timer (`alarmStillRingsWithHubStoppedMidTimer`,
  `localFireRingsWithoutTheHub`). Alarms are rescheduled when moved, cancelled when removed, and
  fire at once if the date has passed.
- **Offline touch timers** (`TimerStoreTests`, `AppModelTimerTests`): if the Hub is unreachable,
  a touch timer is created locally, rings locally and syncs on reconnect without duplicates.
  Cancels and dismissals made offline are queued and are not brought back by `hello`.
- **iPad relaunch** (`relaunchRestoresTimersAndSchedulesThem`,
  `relaunchAfterEndTimeRingsImmediately`): timers are restored from disk and rescheduled.
- **Event stream reconnect** (`EventStreamTests`): exponential back-off, capped, and reset after a
  successful connection. `hello` replaces the Hub timers and keeps local-only ones.
- **Clock skew** (`remainingCorrectsForHubClockAhead`/`Behind`): countdowns correct for the Hub's
  clock being ahead or behind the iPad's.
- **Idle weather** (`IdleModelTests`): keeps the last good value and goes stale on failure, and
  refreshes on reconnect.

## 3. On-device checklist (kitchen)

Run this after a release that touches timers, the Hub service or networking. Each step takes a
minute or two. Write down anything that fails and file an issue.

1. **Hub killed mid-timer.** Set a 2-minute timer by voice. On the Mac run
   `pkill -9 -f coco_hub` within the first minute. The iPad still rings at 2:00. launchd
   restarts the Hub within seconds (`make hub-logs` shows it), and the Display reconnects
   on its own.
2. **Internet off.** Unplug the router's WAN cable, or turn off internet access for the
   household (leave the Wi-Fi up). The clock keeps ticking. "Set a timer for 1 minute" works by
   voice and rings. A touch timer works. "What's the weather" says it can't reach the weather
   service. The idle screen shows the last weather marked stale, or none. Plug the WAN back in:
   the weather comes back within about 10 minutes.
3. **Mac reboot.** Set a 10-minute timer, then reboot the Mac. After login the Hub comes back
   (launchd), the Display reconnects without a tap, and the timer is still counting down on
   the iPad and in `make hub-diag`.
4. **iPad relaunch.** Set a 5-minute timer. Swipe Coco Display away in the app switcher and open
   it again. The timer is still there with the right time left, and still rings.
5. **Ollama stopped.** Run `brew services stop ollama` and ask something only Qwen handles
   ("why is the sky blue"). Coco says "Sorry, I can't do that yet." quickly, and timers and the
   time still work. `make hub-diag` shows ollama `error`. Run `brew services start ollama`
   afterwards.
