# Media policy (lockdown hook)

All media that Coco starts goes through one Hub module:
`hub/src/coco_hub/media/policy.py`. **This is where parental controls / content lockdown
go.** v1 ships `AllowAllPolicy` (the family chose no lockdown for now; see the design doc).

## How it works

- The app registers one policy at `app.state.services["media_policy"]`.
- Intents get it with `policy_from(ctx.services)`, which fails closed: no policy registered
  is an error, never "allow".
- For a search (YouTube now, Spotify later), the policy is asked twice:
  1. `MediaRequest(kind, query)` before searching, so a blocked query costs no API quota;
  2. once per candidate with `item_id`, `title` and `channel` filled in. Coco plays the
     first allowed item. "Next" checks the following items the same way.
- A denial is spoken using `MediaDecision.reason` (default: "Sorry, I can't play that.").
- Pause / resume / stop / volume don't start new content and aren't checked.

In `hub/src/coco_hub/intents/media.py`, `_play_first_allowed` is the only code that makes a
`YoutubeView`. Tests in `hub/tests/intents/test_media_intents.py` check that play and next
both call the policy and that a deny policy blocks with its reason.

Spotify (`hub/src/coco_hub/intents/music.py`, kind `music`) works the same way: the query
is checked before searching, then each ranked candidate with `item_id` = Spotify URI,
`title` = its name and `channel` = the artist (or playlist owner); the first allowed one
plays. "Play some music" (resume whatever Spotify last had) is checked at the query level
only. `hub/tests/spotify/test_music_intents.py` checks every play phrasing hits the policy.

With `music.provider = "apple_music"` (#42) the Hub checks the query the same way before it
sends the Display `music.play`, but there is no per-candidate check: the Display searches
Apple Music itself and the Hub never sees the results (`docs/apple-music.md`).
`hub/tests/spotify/test_music_provider.py` checks a denial sends no command.

## Adding controls later

Implement `check(request: MediaRequest) -> MediaDecision` (for example: a channel
allow-list, blocked words, time-of-day rules, per-kind rules), register it in `app.py`
instead of `AllowAllPolicy`, and make every new media intent (music, sounds, pictures) call
it before it shows or plays anything.

YouTube searches use `safeSearch=moderate` as a default. That is not the lockdown: content
decisions belong in the policy.
