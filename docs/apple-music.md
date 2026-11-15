# Apple Music (fallback, #42)

Coco plays music with Spotify by default (`docs/spotify.md`). If spike #5
(`docs/spikes/spotify-test-procedure.md`) shows Spotify can't play reliably while Coco holds
the screen under Guided Access, switch to Apple Music: the Display plays it itself with
MusicKit, inside the Coco app, so there is no other app to keep alive.

The same phrases work ("play Raffi", "play the Frozen soundtrack", "put on my dinner playlist",
"pause", "next song", "what's playing"); only who plays them changes.

## Switching

In `hub/config.local.toml`:

```toml
[music]
provider = "apple_music"   # default "spotify"
```

Restart the Hub. Spotify settings can stay; with `apple_music` the music intents never call
Spotify (pause / next / volume included).

## One-time setup (owner, needs the Apple Developer team)

None of this can be checked from CI or without the owner's Apple Developer account.

1. **Enable MusicKit for the App ID.** developer.apple.com → Certificates, Identifiers &
   Profiles → Identifiers → `com.stormkiln.heycoco.display` → App Services → check
   **MusicKit** → Save. (MusicKit is an App ID service, not an entitlement: nothing changes
   in the project's signing besides a refreshed provisioning profile.) Without it, catalog
   requests fail and Coco shows "Apple Music didn't start." on the Now Playing screen.
2. **Rebuild and install** the Display (signed with that team). `project.yml` already sets
   `NSAppleMusicUsageDescription`.
3. **On the iPad**, sign in to the Apple ID with the **Apple Music subscription** (Settings →
   Music). The first "play …" asks for Apple Music access: tap Allow. If it was denied, Coco
   shows "Coco isn't allowed to use Apple Music. Allow it in Settings." (Settings → Coco →
   Media & Apple Music).

## How it works

- Hub (`hub/src/coco_hub/intents/music.py`): `music.play` parses the request into
  `(kind, query)` as for Spotify, asks the media policy (kind `music`, the query), and replies
  with the Display command `music.play {query, kind}`, the `now_playing` view and "Playing
  Raffi on Apple Music.". `query` "" ("play some music") means resume. Transport and volume
  intents send only the existing `media.*` / `volume.*` commands (target `music`).
  "What's playing" shows the Now Playing screen ("Here's what's playing."): only the Display
  knows what MusicKit plays.
- Display (`display/CocoKit/Sources/CocoKit/Media/`): `MusicProvider` is the interface;
  `AppleMusicProvider` maps the kind to catalog types (artist / song / album / playlist, all
  four for `any`), searches with `MusicCatalogSearchRequest`, ranks like the Hub does for
  Spotify (exact name > all words > rest; then artist > song > album > playlist; then catalog
  order) and plays the best on `ApplicationMusicPlayer` (an artist plays its top songs).
  `LiveMusicKit` is the MusicKit side; `FakeMusicKit` stands in for it in `swift test`.
  `MediaCenter` routes `media.*` with target `music` to it, pauses it for a ringing alarm,
  and shows its now-playing (source `apple_music`) on the Now Playing screen and idle strip.
  Now playing stays on the iPad: nothing new is posted to the Hub.

## Limits (known)

- **Per-item policy checks don't run.** The Hub checks the query; it never sees Apple Music's
  results, so `MediaPolicy` can't veto a specific album or artist the way it can for Spotify.
  Fine with v1's `AllowAllPolicy`; a real lockdown would need the Display to send candidates
  back (a protocol change).
- **Volume and ducking.** MusicKit has no app-level gain, so "volume 5" and Coco's ducking
  while it listens don't change Apple Music; the iPad's volume buttons do. Whether iOS ducks
  `ApplicationMusicPlayer` while Coco listens is untested on hardware.
- **Errors are shown, not spoken.** The Hub says "Playing X on Apple Music." before the
  Display searches; if nothing is found or access is missing, the Now Playing screen shows why.
- **Artwork** comes from MusicKit's artwork URL; if it isn't a plain https URL on a device,
  the screen falls back to the music-note placeholder.
- Untested on a real iPad: needs the owner's team, the MusicKit App ID service and a
  subscription. CI covers the Hub routing, the protocol contract and `AppleMusicProvider`'s
  logic against `FakeMusicKit`, and builds the app for the simulator.
