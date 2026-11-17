# Calendars

Coco can read your calendars: "hey coco, what's going on today?", "what's on Saturday?",
"when is soccer practice?", and the home screen lists today's events (and tomorrow's, when
there's room) beside the forecast. Part of epic #137.

Nothing is hard-coded: Coco only reads the calendars you list in the Hub's config. The
calendars and events in this guide ("Family", "Soccer practice") are examples.

Coco only **reads** calendars. It never adds, changes or deletes events.

## What you can say

| Say | Coco does |
| --- | --- |
| "what's going on today" / "what's on tomorrow" | a short summary, plus the day on screen |
| "what's on Saturday" / "anything on Friday" | the next such day |
| "what's on the calendar this weekend" | Saturday and Sunday |
| "when is soccer practice" | the next time it's on |

Answers start with how many things there are, then all-day items, then the first three timed
events, then "and N more": "3 things today: School holiday all day, Dentist at 10 AM, and
Soccer practice at 6 PM." An empty day is "Nothing on the calendar tomorrow." If "when is …"
finds nothing: "I don't see the bake sale on the calendar."

"what is today" still tells you the date, and "how does tomorrow look" still gives the weather.

## On screen

- **Home screen:** beside the forecast (below it in portrait), "Today" lists up to 5 events
  (colour dot, time or "All day", title) and "+N more". Events drop off once they've ended, by
  the iPad's clock. "Tomorrow" (up to 3) appears when 3 or fewer events are left today. A small
  "Updated 2:15 PM" chip means the Hub couldn't refresh lately.
- **After a question:** a list of the day(s) asked about, all-day first, with each event's
  calendar name; it closes with the X or after 30 seconds untouched, like the weather screen.

## Adding a Google calendar

Coco reads each calendar's **secret address in iCal format**: a private link that lets
anything holding it read that calendar, without signing in.

1. On a computer, open [Google Calendar](https://calendar.google.com).
2. Settings (gear icon) → **Settings** → under "Settings for my calendars", pick the calendar.
3. **Integrate calendar** → copy **Secret address in iCal format** (it ends in
   `/basic.ics`). Google's help: <https://support.google.com/calendar/answer/37648>.
4. On the Hub's Mac, add it to `hub/config.local.toml` (that file is gitignored; never put
   the address anywhere else, and don't paste it into chats, issues or screenshots):

   ```toml
   [[calendars]]
   source = "ics"
   url = "https://calendar.google.com/calendar/ical/…/private-…/basic.ics"
   ```

5. Restart the Hub (`make hub-run`, or `make hub-install-service` for the launchd agent).
   Events appear within a few seconds; `make hub-diag` shows each calendar's `fetched_at`.

Repeat with one `[[calendars]]` block per calendar. Shared and family calendars have their own
secret address under their own settings. A calendar with **no** secret address option is one
you can't fully see (your Google Workspace admin may have turned it off).

Any other calendar app that gives you an `.ics` (iCal) link works the same way, including
`webcal://` links.

### Options

```toml
[[calendars]]
source = "ics"
url = "…"
name = "Family"        # what Coco calls it; default: the calendar's own name
color = "#33B679"      # #RRGGBB dot on screen; default: one per calendar, by position
show_on_idle = true    # false: voice answers only, not on the home screen
busy_only = false      # true: show and say "Busy" instead of titles
```

Typos and unknown keys stop the Hub at startup with a message naming the problem (never the
address).

## Privacy

A kitchen screen is seen by guests, and Coco says event titles out loud.

- **`busy_only = true`** on a calendar: Coco shows and says "Busy" with the time, never titles
  (e.g. a work calendar). "When is …" never finds busy-only events.
- **Private events:** events marked Private (iCal `CLASS:PRIVATE` or `CONFIDENTIAL`) always
  show as "Busy", whatever the calendar's setting.
- **`show_on_idle = false`**: keeps a calendar off the home screen but answers questions about it.
- **Night mode:** events stay on the home screen at night by default. To hide them during
  night hours:

  ```toml
  [night]
  hide_calendar = true
  ```

- **Logs:** with `[diagnostics] log_transcripts = false` the Hub's log file leaves out what
  Coco heard and said (which includes event titles). `/v1/diagnostics` and the log name
  calendars only by position ("calendar 2") and never include an address.

## If an address leaks: reset it

Anyone with the secret address can read that calendar until you reset it.

1. Google Calendar → Settings → the calendar → **Integrate calendar** → **Reset** next to
   "Secret address in iCal format". The old address stops working at once.
2. Copy the new address into `hub/config.local.toml` and restart the Hub.

Until you update the config, the Hub keeps showing the last events it had, marked stale, and
`make hub-diag` reports that calendar as "address or sign-in refused".

## How fresh is it?

The Hub fetches every calendar every 15 minutes (a little jitter so they don't line up), so a
change in Google Calendar shows on Coco within about 15 minutes. Google's own feed picks up
edits within seconds (spike: `docs/spikes/calendar.md`).

If a fetch fails (no internet, Google down), Coco keeps the last events it had: the home screen
shows a stale marker and spoken answers start "As of 2 PM, …". The last good events are saved
on the Hub's Mac, so they survive a restart. The clock and timers never wait on calendars.

With no `[[calendars]]` configured, the home screen looks exactly as it did before calendars,
and calendar questions get "Calendars aren't set up yet."

## Adding another kind of calendar (developers)

Sources live in `hub/src/coco_hub/calendar/sources/`. A new one (iCloud CalDAV, the Google
Calendar API, …) is a module with an `Options` model for its config keys (secrets as
`SecretStr`) and a class implementing `CalendarSource.fetch(start, end)`, plus one line in
`SOURCES`. Subclass `CalendarSourceContract` (`hub/tests/calendars/contract.py`) with fixtures
in the source's own format to get the same tests as the ICS source: recurring events with
exceptions, all-day and multi-day, time zones and daylight saving, cancelled and private
events, and typed errors. Nothing else changes.
