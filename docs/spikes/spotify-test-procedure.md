# Spike #5: Spotify on the iPad while Coco holds the screen — test procedure

The question: in the real kitchen setup (Coco Display in front, Guided Access on, iPad on its
charger, nobody touching it), does the iPad's Spotify app stay in Spotify's Connect device
list, and can the Hub start playback on it "cold"? If yes, Coco keeps Spotify. If not, the
household switches to the Apple Music fallback (#42, `docs/apple-music.md`), which is already
built and is one setting away.

The tool is `make spotify-spike` (`hub/src/coco_hub/media/spotify/spike.py`). It uses the
Spotify login the Hub already stored and calls the Web API directly, without the Hub's wake
help (`spotify.wake_requested`), so it measures Spotify itself.

## Why the Display can't fix this under Guided Access

When the iPad is missing from the device list, the Hub asks the Display to open the Spotify
app (`spotify.wake_requested` on the event stream, `docs/protocol/README.md`). The Display
does that only when **Guided Access is off**: under Guided Access iOS won't switch to another
app, so the Display does nothing, and after `spotify.wake_wait_s` Coco says "Open Spotify on
the iPad once, then try again." Even without Guided Access, opening Spotify leaves Spotify in
front (iOS gives Coco no way to come back by itself). So in the kitchen setup the wake can't
help: Spotify only works if the iPad stays listed on its own. That is what this run measures.

## Before you start

1. The Hub is configured for Spotify and logged in (`docs/spotify.md`): `make hub-diag` shows
   `spotify.status` `ok` with `device_visible: true` while Spotify is open on the iPad.
   `spotify.device_name` in `hub/config.local.toml` is the iPad's Spotify Connect name.
2. On the iPad: Spotify installed and logged in with the same account; play a song once and
   stop it, so the iPad is a known Connect device.
3. Turn the iPad's volume **down low**: cold starts play a few seconds of music (every
   `--cold-start-every` minutes, all night) before pausing it again. Pick something quiet
   with `--uri` (a `spotify:track:` URI from Share → Copy Song Link, last path part) if you
   don't want "resume whatever was last played".
4. Keep the Mac awake for the whole run (it runs the Hub anyway; `caffeinate -i` in another
   terminal if in doubt).

## The run

1. On the iPad, open **Coco** and leave it in the foreground.
2. Turn on **Guided Access** (Settings → Accessibility → Guided Access; triple-click the top
   button in Coco → Start). This is the configuration the kitchen will use.
3. Put the iPad on its charger and leave it alone. Don't open Spotify again.
4. On the Hub's Mac, start the run (the Hub can keep running; both read the same tokens):

   ```sh
   make spotify-spike ARGS="--hours 10 --interval 60 --cold-start-every 30"
   ```

   It prints where it logs (default `~/Library/Application Support/CocoHub/spikes/
   spotify-run-<YYYYMMDD-HHMM>.jsonl`; `--out FILE` to choose) and one JSON line per poll /
   cold start. Ctrl-C stops early and still prints the summary.
5. In the morning it prints the summary and the decision. To see it again later:

   ```sh
   make spotify-spike ARGS="--summarize ~/Library/Application\ Support/CocoHub/spikes/spotify-run-….jsonl"
   ```

Optional second run, same procedure with Guided Access **off** and Coco in front, to see
whether Guided Access itself makes a difference.

## What it measures

- **visible %**: polls where the iPad was in `/me/player/devices` (by `spotify.device_name`,
  else the active / only device). Failed requests (network, Spotify 5xx) are counted
  separately and left out.
- **longest gap**: the longest stretch from the first poll that missed the iPad to the next
  poll that saw it again (a gap still open at the end counts until one interval after the last
  poll).
- **cold-start success %**: attempts where `PUT /me/player/play` on the iPad succeeded and
  `currently-playing` reported `is_playing` within 10 s (then it pauses). An attempt while
  the iPad isn't listed fails as "device not visible" without calling play. Median and worst
  latency are for the successful ones.

## Decision rule

| Result | Decision |
|---|---|
| run ≥ 6 h **and** ≥ 5 cold starts **and** visible ≥ 95 % **and** cold starts ≥ 90 % | **Keep Spotify** (close #42 as not needed, or keep it as the fallback) |
| run ≥ 6 h and ≥ 5 cold starts, but visible < 95 % or cold starts < 90 % | **Switch to Apple Music**: `[music] provider = "apple_music"` (`docs/apple-music.md`) |
| shorter run or too few cold starts | **Inconclusive**: run again overnight with `--cold-start-every` |

The tool prints which row applies. Record the summary, the date, the iPad / iPadOS / Spotify
versions and the decision in `docs/spikes/spotify.md` (the findings doc #5 asks for); keep the
JSONL log out of git.

## Not covered by this tool

Issue #5 also asks about the Spotify iOS SDK's App Remote as a wake path. That needs Spotify
iOS SDK integration in the Display and, like the URL scheme, brings the Spotify app to the
front, which Guided Access blocks. If the run above fails, App Remote is unlikely to rescue it
in the Guided Access setup; test it only if the household would drop Guided Access.
