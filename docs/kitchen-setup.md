# Kitchen kiosk setup guide

How to turn a spare Mac and an iPad into the always-on Coco kitchen assistant.

You need two things:

- **Coco Hub** runs on a Mac that stays on. It holds the timers, fetches the weather and works out
  what you asked for.
- **Coco Display** runs on the iPad (7th generation, iPadOS 18) that sits on the counter. It shows
  the clock and screens, listens, speaks, and rings the alarms.

Both must be on the same home Wi-Fi network. Set up the Mac first, then the iPad.

This guide is for someone comfortable with a Terminal. Anything that is not built yet is marked
"(coming in #NN)". Those numbers are GitHub issues in this repo.

## Part 1: The Mac (Coco Hub)

### Quick start: `make hub-setup`

After cloning (1.2), one command does most of 1.1 and 1.3 for you and is safe to run again:

```sh
make hub-setup
```

It checks for `uv` and Ollama and, when one is missing, asks before running `brew install`
(press Enter to just print the command instead). It does the same for
`brew services start ollama` and `ollama pull qwen3:8b` (the model named in your config). If
`hub/config.local.toml` doesn't exist yet, it creates it from the example with a freshly generated
token, asks for your home latitude, longitude and timezone, and turns Qwen on; an existing file is
never changed. Finally it prints the Hub's TLS fingerprint and the next step,
`make hub-install-service` (1.4).

Without a terminal to answer questions (for example over a script), pass the values instead.
Nothing is installed then; brew and ollama commands are only printed:

```sh
make hub-setup ARGS="--non-interactive --lat 45.42 --lon -75.69 --timezone America/Toronto"
```

(or set `COCO_SETUP_LAT`, `COCO_SETUP_LON` and `COCO_SETUP_TIMEZONE`). The steps below are the same
thing by hand; read them for what each setting means.

### 1.1 Install the prerequisites

1. Install [Homebrew](https://brew.sh) if you don't have it.
2. Install `uv`, which runs the Hub's Python environment (it fetches Python 3.13 for you):

   ```sh
   brew install uv
   ```

3. Install Ollama, which runs the local Qwen model for general questions, and start it as a
   background service that comes back after a reboot:

   ```sh
   brew install ollama
   brew services start ollama
   ollama pull qwen3:8b
   ```

   The model download is several GB, so allow some time. Qwen answers "how tall is Mount
   Everest?" style questions. Timers, weather and the other everyday requests don't need it.

### 1.2 Get the code

```sh
git clone https://github.com/StormKiln/hey-coco.git
cd hey-coco
```

Put the clone somewhere permanent. The background service remembers this folder, so if you move
it later, re-run step 1.4.

### 1.3 Write your settings

1. Copy the example file:

   ```sh
   cp hub/config.example.toml hub/config.local.toml
   ```

   `config.local.toml` is ignored by git, so your token stays out of the repo.

2. Make a token. It is a shared password between the Hub and the iPad:

   ```sh
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
   ```

3. Open `hub/config.local.toml` and fill in:

   - `token`: paste the token between the quotes. The Hub refuses to start with an empty or short
     token (at least 24 characters). Keep a copy, because you type it on the iPad later.
   - `timezone`: your IANA timezone, for example `America/Toronto`.
   - `units`: `"metric"` or `"imperial"`.
   - `[location]`: your home `lat` and `lon` (right-click your house in Google Maps to copy them)
     and a `name`. Without a location there is no forecast on the idle screen.
   - `[night]`: `start` and `end` as 24-hour `HH:MM`, for example `"21:30"` and `"06:30"`, and
     `dark_after_idle_s`, the idle seconds before the screen goes dark. At `start` the iPad dims;
     after `dark_after_idle_s` with nobody using it the screen goes black at minimum brightness.
     "Hey coco", a tap anywhere or a ringing timer wakes it, and at `end` it returns to your normal
     brightness. It never goes dark while a timer rings or Coco is playing something.
   - `[qwen]`: change `enabled = false` to `enabled = true`. Leave `model = "qwen3:8b"` and
     `base_url = "http://localhost:11434"` as they are.

   Leave the rest at the defaults. Every setting can also be overridden with a `COCO_*`
   environment variable (see the comments at the top of the example file).

### 1.4 Install and start the service

```sh
make hub-install-service
```

This installs a launchd agent (`com.stormkiln.coco-hub`) that starts the Hub at login and restarts
it if it crashes. It first runs `uv sync` and checks that `hub/config.local.toml` exists.

Check that it works:

```sh
curl -k https://localhost:8765/healthz
```

You should see `{"status":"ok", "hub_id": ..., "fingerprint": ...}`. (`-k` because the Hub's
certificate is self-signed: the iPad checks it by pairing instead.) If you don't, follow the
logs:

```sh
make hub-logs
```

Press Ctrl-C to stop following; the Hub keeps running. The log files live in
`~/Library/Logs/CocoHub/`. Typical first-run problems are a missing or short token and a typo in
`config.local.toml`. After you edit that file, run `make hub-install-service` again to restart the
Hub with the new settings.

Other useful commands:

- `make hub-render-plist` prints the launchd file it would install.
- `make hub-uninstall-service` stops the Hub and removes the agent.

### 1.5 Keep the Mac awake and logged in

The Hub is a LaunchAgent, so it runs only while a user is logged in. It has to survive a power
cut and run with nobody at the keyboard.

1. **Automatic login.** System Settings, then Users & Groups, then "Automatically log in as", and
   pick the account that runs the Hub. This option is unavailable if FileVault is on. Without
   automatic login the Hub only comes back after someone types the password. Decide whether a Mac
   that logs itself in is acceptable where it lives.
2. **Don't sleep.** Open System Settings, then Energy (or Battery, then Options, on a laptop) and
   turn on "Prevent automatic sleeping when the display is off" while on power. The wording differs
   a little between macOS versions. A laptop must stay plugged in with the lid open, because
   closing the lid sleeps it. The display itself can still turn off.
3. **Start after a power failure.** In the same place, turn on "Start up automatically after a
   power failure" if your Mac offers it.
4. **Firewall prompt.** The first time the Hub runs, macOS may ask whether Python should accept
   incoming network connections. Click **Allow**. Otherwise the iPad cannot reach port 8765. If you
   missed the prompt, open System Settings, then Network, then Firewall, then Options, and allow
   incoming connections for Python. If the firewall is off, there is nothing to do.

Reboot once to prove it all works: after the Mac comes back up,
`curl -k https://localhost:8765/healthz` should succeed without you touching anything.

**The Hub's fingerprint.** On first start the Hub makes its own security certificate
(in `~/Library/Application Support/CocoHub/tls/`). Print its fingerprint; you compare it with
the iPad when pairing:

```sh
make hub-fingerprint
```

```
Coco Hub fingerprint: 3F2A-91C0-7B4E-D815-A6C2-0E9F
Hub ID: 5d0c8a91f3b2e746
```

It stays the same across restarts and reboots. If you delete that folder or move the Hub to
another Mac, it changes and the iPad must pair again (2.3).

### 1.6 Later: YouTube and Spotify keys

Skip these for now. The Hub works without them, and the sections stay commented out in
`config.local.toml`.

- A YouTube API key goes under `[keys]` as `youtube`. See #38 (YouTube search and intents).
- Spotify (`client_id`, `client_secret`, `redirect_uri` under `[spotify]`) is covered by #40
  (Spotify control from the Hub).

## Part 2: The iPad (Coco Display)

### 2.1 Install through TestFlight

1. Sign in to the iPad with the Apple ID that was invited to the TestFlight beta.
2. Install the **TestFlight** app from the App Store.
3. Open the invitation email (or the TestFlight link) on the iPad, tap **Accept**, then
   **Install** next to Coco Display.

The builds come from the Mac (or GitHub Actions) with `make display-testflight`. The one-time
Apple Developer and App Store Connect setup, and adding the iPad's Apple ID as a tester, are in
[`docs/testflight.md`](testflight.md). TestFlight builds expire after 90 days, so install each
new build when TestFlight offers it.

On the Home Screen the app is called **Coco**. TestFlight shows the name of its App Store Connect
record (for example "Coco Kitchen").

### 2.2 First launch: say Allow to everything

The first time Coco opens (and the first time you use a feature) it asks for permissions. Tap
**Allow** (or **OK**) on each one:

| Prompt | Why Coco needs it |
|---|---|
| Local Network | To find and talk to Coco Hub on your Wi-Fi. Without it the iPad can't see the Mac at all. |
| Microphone | To hear you when you speak to it. |
| Speech Recognition | To turn what you say into text. |
| Notifications | So a timer still rings if the Hub is unreachable. |

If you tapped "Don't Allow" by mistake, fix it in the iPad's Settings app: scroll down to Coco
Display and switch the permission on.

### 2.3 Connect the iPad to the Hub

1. On the Coco screen, tap the small **gear** icon, or press and hold the screen for 1.5 seconds.
   Settings opens. (The "Can't reach Coco Hub" or "needs a valid token" pill at the top also opens
   it when tapped.)
2. **Hub host:** leave it blank and Coco finds the Mac with Bonjour (service `_coco._tcp`). If it
   can't find it, type the Mac's IP address, for example `192.168.1.20`. Leave the port at `8765`
   unless you changed it on the Hub.
3. **Token:** paste the token from `hub/config.local.toml`.
4. Tap **Save**.
5. **Pair.** Coco finds the Hub and asks **"Pair with Coco Hub?"** with a fingerprint like
   `3F2A-91C0-7B4E-D815-A6C2-0E9F`. On the Mac, run `make hub-fingerprint`. If the two match
   exactly, tap **It matches. Pair**. If they don't, tap **Not now** and see Troubleshooting:
   something else on your network is answering as Coco Hub. (Tapped Not now by mistake? Tap the
   "Found Coco Hub. Tap to pair." pill at the top.)

Coco only sends the token after you pair, and from then on only to that exact Hub, over an
encrypted connection. The pairing survives Hub restarts and the Mac's IP address changing.

The idle screen should show the clock and, once the Hub has fetched it, the forecast. Try tapping
the mic and asking for "a pasta timer for 10 minutes". Saying "hey coco" without tapping is coming
in #21 (wake-word engine); until then use the mic button.

Coco never shows the saved token again, so keep your own copy.

### 2.4 Make the iPad a kiosk

Do these once, in the iPad's Settings app.

1. **Auto-Lock: Never.** Settings, then Display & Brightness, then Auto-Lock, then **Never**. (The
   app also asks iPadOS not to sleep while it is open, but set this too.)
2. **Guided Access** pins the iPad to Coco so a stray swipe can't leave the app.
   1. Settings, then Accessibility, then Guided Access, and switch it **on**.
   2. Tap **Passcode Settings**, then **Set Guided Access Passcode**, and choose a code you will
      remember. You need it to end a session. This iPad has Touch ID on the Home button, so decide
      whether you also want it to end sessions.
   3. Tap **Display Auto-Lock** and choose **Never**. This is separate from the normal Auto-Lock
      and applies while Guided Access is running.
   4. Settings, then Accessibility, then Accessibility Shortcut, and tick **Guided Access** so that
      triple-clicking the Home button starts it.
   5. Open Coco, **triple-click the Home button**, and tap **Start** (top right). Don't circle
      any areas to disable: Coco needs the whole screen, including the gear and the mic. Leave
      the touch option on. To leave, triple-click Home and enter the passcode.
3. **Keep notifications off the screen** so nothing covers the clock.
   - Settings, then Focus, then Do Not Disturb (or make a new Focus called "Kitchen"), and turn it
     on or schedule it for all day.
   - Be careful with timers. The alarm normally rings inside Coco, but the backup that rings it
     when the Hub is unreachable is a local notification. A Focus can silence that backup unless
     **Coco Display** is in the Focus's allowed apps. Add it, then test as in section 2.5, step 4.
4. **Volume.** Press volume up on the side of the iPad until you can hear it across the kitchen,
   and make sure it is not muted. If your iPad has a side switch, check what it is set to do
   (Settings, then Control Center, or the rotation-lock and mute setting for your iPadOS version)
   and test that the alarm is audible with it in each position.
5. **Keep it plugged in.** Leave the charger connected all the time. That is fine for the short
   term, but a permanently full battery can wear over a year or more. Open Settings, then Battery,
   then Battery Health (or Battery Health & Charging) and see what your iPad offers. If you see
   **Optimized Battery Charging** or a charge limit, turn it on. The 7th-generation iPad may not
   offer these, so check what yours shows rather than assuming. Keep it out of direct sun and away
   from the stove.
6. **Wi-Fi.** Settings, then Wi-Fi: make sure the iPad is on the same network as the Mac, with
   Auto-Join on. In your router, give the **Mac a DHCP reservation** (a fixed IP address tied to
   its hardware address) so its address never changes. This matters most if you typed the IP into
   Coco. If you left the host blank, Bonjour finds the Mac anyway, but a stable address still
   helps when debugging.
7. **Position.** Put it somewhere out of splashes, with the microphone clear and the speaker
   unblocked.

### 2.5 Final check

1. Tap the mic and say "what's the weather". You should see a forecast and hear an answer.
2. Set a one-minute timer, let it ring, and stop it by tapping.
3. Ask a general question such as "how tall is Mount Everest". A spoken answer means Qwen is
   working.
4. **Hub-down test:** set a two-minute timer, then run `make hub-uninstall-service` on the Mac
   before it ends. The iPad should still ring. Then restore the Hub with
   `make hub-install-service`.
5. Leave it for a day. Check that the screen is still on, Coco is still in front, and timers
   work. At bedtime it should dim, go black after the idle time, and wake on a tap.

On-device verification of this guide is still pending. If a step was wrong or unclear, please
fix it here.

## Run on the MacBook

The same Display app runs on an Apple silicon Mac as "Designed for iPad", for development or as a
spare display. It is not a Mac Catalyst build.

1. Xcode signed in with your Apple ID (Xcode, then Settings, then Accounts). macOS only launches a
   signed iPad app, so you need your team ID (Accounts, select the team, the ID is shown there, or
   developer.apple.com, then Membership).
2. For "hey coco", put `hey-coco_mac.ppn` (and the AccessKey) in `display/WakeWord/`, see
   `docs/wake-word.md`. Without the Mac keyword the iOS one is tried; without either, use the mic
   button.
3. Build, sign and open it:

   ```sh
   make display-run-mac DEVELOPMENT_TEAM=ABCDE12345
   ```

   `make display-build-mac` builds without signing (a compile check only). The app ends up in
   `display/DerivedData/mac/`.
4. First launch: allow Local Network, Microphone and Speech Recognition, then connect it to the Hub
   as in section 2.3.

Differences from the iPad: night mode blacks out the window but cannot change the Mac's display
brightness, and the app does not keep the Mac awake. Use the Mac's own Lock Screen and Energy
settings for that. Guided Access does not apply.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| **"Can't reach Coco Hub"** | The Mac is off, asleep or logged out, the Hub isn't running, the two devices are on different Wi-Fi, or the Mac's IP changed. | On the Mac run `curl -k https://localhost:8765/healthz` and `make hub-logs`; re-run `make hub-install-service` if it is down. Check both devices are on the same Wi-Fi. If you typed a host in Coco's Settings, confirm the IP, or clear it to use Bonjour. Check Local Network is allowed for Coco (iPad Settings, Coco Display) and that you clicked Allow on the Mac's firewall prompt. |
| **"Unknown Coco Hub at …"** | The iPad found a Coco Hub that isn't the one it paired with: the Hub's certificate changed (you deleted `~/Library/Application Support/CocoHub`, reinstalled, or moved the Hub to another Mac), or another device on the network claims to be Coco Hub. Coco ignores it and doesn't send the token. | If you know the Hub changed: on the iPad open Settings, tap **Forget Hub and Re-pair…**, enter the token again, Save, and pair after checking `make hub-fingerprint` on the Mac. If nothing changed on the Mac, don't re-pair: find the other device. |
| **"Pair with Coco Hub?" fingerprint doesn't match `make hub-fingerprint`** | Something other than your Hub answered. | Tap **Not now**. Type the Mac's IP address as the Hub host in Settings so Coco asks that Mac directly, then pair. |
| **"Coco Hub needs a valid token"** (unauthorized) | The token on the iPad doesn't match `token` in `hub/config.local.toml`. | Open Coco's Settings, enter the exact token, and Save. If you changed the token on the Mac, run `make hub-install-service` again to restart the Hub. |
| **No forecast** ("Forecast unavailable") | The Hub has no home location, or can't reach the weather service. | Set `[location]` `lat` and `lon` in `hub/config.local.toml` and re-run `make hub-install-service`. "Forecast may be out of date" means the last fetch failed, so check the Mac's internet. |
| **"Hey coco" does nothing** | The wake word isn't built yet, or its AccessKey and keyword aren't configured. | Use the mic button for now. Wake-word setup is coming in #21; see `docs/wake-word.md` once that lands. |
| **The mic does nothing**, or Coco says it needs a permission | Microphone or Speech Recognition was denied. | iPad Settings, then Coco Display, and switch both on. |
| **No spoken answers to general questions** | Ollama isn't running, the model isn't pulled, or Qwen is disabled. | On the Mac: `brew services list` (ollama should be started), `ollama list` (should show `qwen3:8b`), and check `enabled = true` under `[qwen]` in `hub/config.local.toml`. Run `make hub-logs` and look for a Qwen unavailable message. If a question takes too long, Coco says "Sorry, that took too long." |
| **A timer didn't ring** | Notifications are off for Coco, a Focus is hiding the backup notification, the volume is down or muted, or Coco was closed. | Check iPad Settings, then Coco Display, then Notifications. Add Coco to your Focus's allowed apps. Turn the volume up and check the side switch. Start Guided Access so the app stays open. |
| **The iPad screen went to sleep** | Auto-Lock, or Guided Access's Display Auto-Lock, isn't set to Never. | Set both (section 2.4). |
| **After a power cut nothing works** | The Mac didn't log in, so the LaunchAgent never started. | Enable automatic login and "Start up automatically after a power failure" (section 1.5). |
