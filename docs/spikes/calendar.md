# Spike #138: Google Calendar ICS feeds

**Decision: build on Google's secret iCal (ICS) addresses.** Edits reached the feed within
seconds to half a minute, so Coco's own 15-minute poll dominates the lag, inside the owner's
"about 15 minutes" target. The Google Calendar API with OAuth stays a later fallback behind the
same `CalendarSource` interface; nothing in this spike needs it.

## Method

- Two Google calendars in `hub/config.local.toml` (gitignored; never printed or committed):
  a large public calendar (about 930 events) for format and speed, and a throwaway calendar
  the owner created for the lag test. Neither is named here.
- Script: `docs/spikes/calendar/spike.py`, run with `make calendar-spike ARGS=...`
  (`--selftest`, `--check`, `--watch MARKER`). It prints calendars as "calendar 1, 2", never a
  URL, calendar name or event title, except titles containing the marker the owner chose for
  the test event. httpx errors (which contain the URL) are reduced to their type.
- Lag: `--watch` polled every 30 s and compared the moment a change first appeared with the
  event's `LAST-MODIFIED`, which Google stamps with the edit time. The resolution is the
  30 s poll.

## Results

### Update lag (owner's test calendar, 2026-11-17)

| Change | `LAST-MODIFIED` (UTC) | First seen in feed | Lag |
|---|---|---|---|
| New all-day event | 21:48:02 | 21:48:32 (first poll) | ≤ 30 s |
| All-day → 15:00–16:00 | 21:49:05 | 21:49:34 | 29 s |
| Renamed | 21:56:44 | 21:56:48 | 4 s |
| Moved to 17:00–18:00 | 21:56:59 | 21:57:19 | 20 s |
| Deleted | (gone: no timestamp) | 22:09:14, the same minute the owner reported deleting it | ≤ 1 min |

An earlier delete that seemed to lag turned out not to have been saved; deleted events simply
vanish from the feed (Google does not publish them as `STATUS:CANCELLED`), so the Hub must
treat "missing from the latest fetch" as deleted — it replaces each calendar's events wholesale.

So with the Hub's 15-minute poll (±10% jitter), a change shows on Coco within about 17
minutes at worst, and 8 minutes on average. If that ever feels slow, polling every 5 minutes
costs one ~400 KB download per calendar each time; OAuth would not be needed for speed.

### Feed format (`--check`)

- `Content-Type: text/calendar; charset=utf-8`; `Cache-Control: no-cache, no-store`.
- **No `ETag` or `Last-Modified`**, so conditional requests never get a `304`: every poll
  downloads the whole feed (about 400 KB for 930 events; under 1 KB for a small calendar).
  The Hub still sends validators when a server offers them (other providers).
- **Event order changes on every download**, so a raw hash of the body can't detect "no
  change": the Hub parses every poll.
- `X-WR-CALNAME` and `X-WR-TIMEZONE` are present; every event has `LAST-MODIFIED`.
- The large feed used all the features we planned for: `RRULE` (43), `EXDATE` (10),
  `RECURRENCE-ID` overrides (20), all-day (511, 155 of them multi-day), UTC times (358) and
  `TZID` times.
- Parse and expand for the −1 d … +14 d window: about 230 ms for the 930-event feed, so the
  Hub parses in a worker thread (`asyncio.to_thread`), not on the event loop.

### Parsing (`--selftest`, `docs/spikes/calendar/sample.ics`)

`icalendar` 7.3.0 + `recurring-ical-events` 3.8.2 + `x-wr-timezone` 2.0.1 parse every case
correctly against the Hub timezone (America/Chicago in the sample), all with fictional events:

| Case | Example | Result |
|---|---|---|
| Recurring with exceptions | weekly "Soccer practice": one week excluded (`EXDATE`), one moved to 18:30 and retitled, one cancelled | 2 occurrences, the moved one at 18:30 with its new title |
| All-day | "School holiday" | covers its local day; `DTEND` exclusive |
| Multi-day all-day | "Camping trip", 3 days | start → end spans 3 days |
| Cross-timezone | "Call with Grandma", 15:00 London | 09:00 Hub time |
| UTC time | "Dentist" at 20:00Z | 14:00 Hub time |
| Daylight saving | daily "Morning swim" written in UTC with `X-WR-TIMEZONE`, across the end of DST | stays at 07:30 local every day |
| Cancelled | "Piano lesson", `STATUS:CANCELLED` | dropped |
| Private | `CLASS:PRIVATE` | flagged, so the Hub shows "Busy" |

`recurring_ical_events.of()` already applies `X-WR-TIMEZONE` (it calls
`x_wr_timezone.to_standard`), so the explicit call in the Hub is redundant but harmless. Never
hand-expand recurrences.

## Consequences for #139

- One `ics` source module, the only importer of the three libraries; any ICS URL works
  (`webcal://` too), not just Google's.
- Poll every 15 min with jitter; send conditional headers when the server gives validators;
  cap a feed at 20 MB; parse off the event loop.
- Google answers **404** once a secret address is reset: treat 401/403/404/410 as "the address
  needs attention" (`AuthRequired`), not as a network blip.
- The secret address is a bearer secret: `SecretStr`, never logged, and errors carry only
  the exception type.

## Owner answers (#137 open questions)

1. Acceptable lag: **about 15 minutes** → ICS is enough.
2. Night mode: **show events by default** (a setting can hide them).
3. Writing events: not now (read-only; would need OAuth).
