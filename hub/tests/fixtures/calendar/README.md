# Calendar fixtures

Fictional calendars and events only (`@example.invalid` UIDs). Each scenario of the source
contract (`tests/calendar/contract.py`) is one file per source format:

- `family.ics` — "Family" (Hub timezone America/Chicago): a weekly event with an EXDATE, a
  moved-and-retitled occurrence and a cancelled occurrence; one- and three-day all-day events;
  a London-time event; a cancelled event; a daily event written in UTC with `X-WR-TIMEZONE`
  across the end of daylight saving (2026-11-01); a `CLASS:PRIVATE` event; an overnight event.
- `spring.ics` — "School": a weekly event across the start of daylight saving (2027-03-14),
  a floating-time event, a `CLASS:CONFIDENTIAL` event.
