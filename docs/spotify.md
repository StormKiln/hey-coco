# Spotify

Coco plays music with the owner's **Spotify Premium** account. Audio comes out of the
iPad's own speaker through the **Spotify iOS app**, which shows up as a Spotify Connect
device; the Hub drives it with the Spotify Web API (search, start playback on the iPad,
pause / resume / skip / volume, now playing). Apple Music is a separate, conditional
fallback (see the design doc) and isn't covered here.

Without a `spotify.client_id` in the config, Spotify is off: nothing talks to Spotify and
music requests get "Spotify isn't set up yet."

## One-time setup

1. **Create a Spotify app.** Sign in at <https://developer.spotify.com/dashboard> with the
   Premium account and choose *Create app*:
   - Name / description: anything (e.g. "Coco Hub").
   - **Redirect URI:** `http://127.0.0.1:8765/spotify/callback` (use your `port` if it
     isn't 8765). Spotify only accepts `http` redirects to loopback **IP literals**:
     `localhost` is rejected, so type `127.0.0.1`.
   - APIs used: **Web API**.
   - Save, then copy the **Client ID**. (The Hub logs in with PKCE, so the client secret
     is not needed and should not be put anywhere.)
   - Apps in development mode only work for users listed under *User Management*; the
     owner's account is listed automatically.

2. **Configure the Hub.** In `hub/config.local.toml` (gitignored):

   ```toml
   [spotify]
   client_id = "your-client-id"
   device_name = "Kitchen iPad"   # the iPad's name as shown in Spotify's device picker
   # redirect_uri = "http://127.0.0.1:8765/spotify/callback"  # only if you changed it
   ```

   Restart the Hub (`make hub-install-service` again, or restart `make hub-run`).

3. **Connect the account** on the Hub's Mac, with the Hub running:

   ```sh
   make spotify-login        # prints the URL below
   open http://127.0.0.1:8765/spotify/login
   ```

   Approve the consent page. The browser lands on "Spotify is connected". The login pages
   only answer requests from the Mac itself (127.0.0.1 / ::1); from the iPad or any other
   machine they return 403, even with the LAN token.

   Scopes requested: `user-read-playback-state`, `user-modify-playback-state`,
   `user-read-currently-playing`.

4. **On the iPad**, install Spotify, log in with the same account, and play something once
   so the iPad appears as a Connect device. If `device_name` isn't set, Coco uses the
   active device (or the only one).

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
  device list. Open the Spotify app on the iPad and retry. (Spike #5 is finding a way for
  Coco to wake it itself; the hook is `DeviceWaker` in `hub/src/coco_hub/media/spotify/service.py`.)
- **"Spotify needs a Premium account for that."** Playback control needs Premium.
- **"Spotify won't let me change the iPad's volume."** Some iOS devices report
  `supports_volume: false`; use the iPad's volume buttons.
- **"Spotify is busy right now."** Spotify rate-limited the Hub; it backs off by itself.
- Spotify no longer returns its own algorithmic playlists (e.g. Discover Weekly) in search
  results for new apps; ask for them by a playlist you saved under your own name instead.
