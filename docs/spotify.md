# Spotify

Coco plays music with the owner's **Spotify Premium** account. Audio comes out of the
iPad's own speaker through the **Spotify iOS app**, which shows up as a Spotify Connect
device; the Hub drives it with the Spotify Web API (search, start playback on the iPad,
pause / resume / skip / volume, now playing). Apple Music is a separate, conditional
fallback (`[music] provider = "apple_music"`, `docs/apple-music.md`).

Without a `spotify.client_id` in the config, Spotify is off: nothing talks to Spotify and
music requests get "Spotify isn't set up yet."

## One-time setup

1. **Create a Spotify app.** Sign in at <https://developer.spotify.com/dashboard> with the
   Premium account and choose *Create app*:
   - Name / description: anything (e.g. "Coco Hub").
   - **Redirect URI:** `https://127.0.0.1:8765/spotify/callback` (use your `port` if it
     isn't 8765). The Hub serves HTTPS only (#54). Spotify wants a loopback **IP literal**:
     `localhost` is rejected, so type `127.0.0.1`. (An app registered before #54 with
     `http://127.0.0.1:8765/...` must change it to `https://`.)
   - APIs used: **Web API**.
   - Save, then copy the **Client ID**. (The Hub logs in with PKCE, so the client secret
     is not needed and should not be put anywhere.)
   - Apps in development mode only work for users listed under *User Management*; the
     owner's account is listed automatically.

2. **Configure the Hub.** In `hub/config.local.toml` (gitignored):

   ```toml
   [spotify]
   client_id = "your-client-id"
   device_name = "iPad"   # the iPad's name as shown in Spotify's device picker (always "iPad")
   # redirect_uri = "https://127.0.0.1:8765/spotify/callback"  # only if you changed it
   ```

   Restart the Hub (`make hub-install-service` again, or restart `make hub-run`).

3. **Connect the account** on the Hub's Mac, with the Hub running:

   ```sh
   make spotify-login        # prints the URL below
   open https://127.0.0.1:8765/spotify/login
   ```

   The browser warns that the certificate isn't trusted (it is the Hub's own self-signed one).
   Check the address is `127.0.0.1` and continue. Approve the consent page. The browser lands on "Spotify is connected". The login pages
   only answer requests from the Mac itself (127.0.0.1 / ::1); from the iPad or any other
   machine they return 403, even with the LAN token.

   Scopes requested: `user-read-playback-state`, `user-modify-playback-state`,
   `user-read-currently-playing`.

4. **On the iPad**, install Spotify, log in with the same account, and play something once
   so the iPad appears as a Connect device. If `device_name` isn't set, Coco uses the
   active device (or the only one).

   Spotify always lists an iPad as **"iPad"**, whatever you named it in the iPad's Settings:
   iPadOS hides the user-chosen device name from apps that don't hold a special Apple
   entitlement. So `device_name = "iPad"`. If two
   iPads on the same Spotify account are both listed, "iPad" is ambiguous and Coco may pick
   the wrong one; keep Spotify signed out (or closed) on the other iPad, or leave
   `device_name` unset so Coco uses the active device.

5. **Let Coco wake Spotify and come back (#116).** When the iPad has dropped out of the device
   list, Coco opens Spotify with the Spotify iOS SDK, which plays what you asked for and
   sends the iPad straight back to Coco. That needs the *same* Spotify app (same Client ID)
   to know about the Coco Display iOS app. At <https://developer.spotify.com/dashboard>, open
   the app, *Settings* → *Edit*:
   - **Redirect URIs:** add exactly `cocokitchen-spotify://callback` and keep the existing
     `https://127.0.0.1:8765/spotify/callback` (the Hub's login still uses it).
   - **APIs used** ("Which API/SDKs are you planning to use?"): keep **Web API** and also
     tick **iOS**.
   - **iOS app bundles** / **Bundle ID:** `com.stormkiln.heycoco.display` (the app's
     `PRODUCT_BUNDLE_IDENTIFIER`; if you overrode it in `display/Signing.local.xcconfig`,
     use that value instead).
   - Save.

   Nothing changes in `hub/config.local.toml`: the Hub sends its `spotify.client_id` to the
   Display with each wake. The first SDK wake shows Spotify's consent screen on the iPad
   (your Spotify app, e.g. "Coco Hub", wants to control playback): someone has to tap
   *Agree* once; after that Spotify returns to Coco by itself.

   Without this step (or if the SDK fails) Coco falls back to opening the plain `spotify:`
   link, which works but leaves Spotify in front until someone switches back to Coco.
   The Display's diagnostics screen shows the last wake ("Spotify wake").

## Where the tokens live

The refresh token and the current access token are stored in the Hub's SQLite database
(`data_dir`, default `~/Library/Application Support/CocoHub/coco-hub.sqlite3`, table
`spotify_tokens`). They are never in git and never sent to the Display. Access tokens are
refreshed automatically. To disconnect, revoke "Coco Hub" at
<https://www.spotify.com/account/apps/>; the next request then says "Spotify needs to be
connected on the Mac first.", and you run the login again.

## What you can say

| Say | Does |
|---|---|
| "play Raffi", "play Baby Shark", "play Taylor Swift on Spotify" | searches every kind and plays the best match (exact name first, then popularity) |
| "play songs by the Wiggles" | artist |
| "play the Frozen soundtrack", "play the Moana album" | album |
| "put on my dinner playlist", "play my Discover Weekly" | playlist |
| "play the Baby Shark song", "play the song Let It Go" | song |
| "play some music" | resumes whatever Spotify last played, on the iPad |
| "pause", "resume", "skip", "next song", "stop" | while music is playing |
| "volume 5", "louder", "turn it down" | while music is playing (if the device allows remote volume) |
| "what's playing?", "what song is this?" | says the title and artist |

"Play X on YouTube", "play a cow sound" and "show me a picture of …" are not music.

Every play request goes through the media policy (`docs/media-policy.md`) with kind
`music`: once with the query, then per candidate (Spotify URI, name, artist / playlist
owner).

## Troubleshooting

- **"Open Spotify on the iPad once, then try again."** Spotify drops an idle iPad from the
  device list. Open the Spotify app on the iPad and retry. Before saying this, the Hub asks
  the Display to open Spotify itself (`spotify.wake_requested`), then lists devices every
  `spotify.wake_poll_s` (default 1 s) for up to `spotify.wake_wait_s` (default 12 s; 0 turns
  it off). That only works with **Guided Access off**; under Guided Access iOS won't switch
  apps, so the Display does nothing. For a play request the Display uses the Spotify iOS SDK
  (setup step 5), which starts the music and brings Coco back to the front; otherwise (a
  volume change, the SDK not set up, or the SDK failed) it opens the plain `spotify:` link and
  Spotify stays in front. Whether the iPad stays listed on its own under Guided Access is what
  spike #5 measures: `make spotify-spike`, procedure and decision rule in
  `docs/spikes/spotify-test-procedure.md`. If it doesn't, switch to Apple Music
  (`docs/apple-music.md`).
- **Spotify opens but Coco doesn't come back.** Check the diagnostics screen's "Spotify wake"
  row. "Opened Spotify (no client ID from the Hub)": the Hub has no `spotify.client_id`.
  "Spotify SDK failed: …": the redirect URI or bundle ID in step 5 doesn't match, or Spotify
  never redirected; Coco uses the plain link for an hour, then tries the SDK again.
  "Spotify SDK: back in Coco, Spotify said unknown_error": a known Spotify SDK 5.x issue
  (spotify/ios-sdk#474); Coco is back and the Hub starts the music through the Web API, so
  nothing to fix.
- **"Spotify needs a Premium account for that."** Playback control needs Premium.
- **"Spotify won't let me change the iPad's volume."** Some iOS devices report
  `supports_volume: false`; use the iPad's volume buttons.
- **"Spotify is busy right now."** Spotify rate-limited the Hub; it backs off by itself.
- Spotify no longer returns its own algorithmic playlists (e.g. Discover Weekly) in search
  results for new apps; ask for them by a playlist you saved under your own name instead.
