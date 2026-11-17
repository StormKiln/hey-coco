# Testing Coco Display in the iOS Simulator

DEBUG builds carry a small harness (`display/App/Debug/DebugHarness.swift`) so an end-to-end run
needs no taps. Release and TestFlight builds compile none of it.

## 1. A scratch Hub

Use a temporary config so the run never touches your real Hub's data or token:

```toml
token = "<at least 24 characters>"
host = "127.0.0.1"
port = 59xxx                      # any free port
data_dir = "/tmp/coco-scratch/data"
log_dir = "/tmp/coco-scratch/logs"
timezone = "America/Toronto"
[location]
lat = 45.42
lon = -75.69
[discovery]
enabled = false                   # don't advertise a second Hub on the LAN
```

```sh
cd hub && COCO_CONFIG=/tmp/coco-scratch/config.toml uv run python -m coco_hub
```

Its log prints `pairing fingerprint XXXX-XXXX-…`.

## 2. The app

```sh
make display-run-sim SIM_DEVICE="iPad Air 13-inch (M4)"
```

Then relaunch it pointed at the scratch Hub (`<udid>` from `xcrun simctl list devices`):

```sh
xcrun simctl terminate <udid> com.stormkiln.heycoco.display
xcrun simctl launch <udid> com.stormkiln.heycoco.display \
  -CocoDebugHost 127.0.0.1 -CocoDebugPort <port> -CocoDebugToken <token>
```

## 3. Commands

Write commands, one per line, to the app's inbox; the app reads and deletes the file every
half second and appends the resulting state to `coco-debug-log.txt` next to it:

```sh
docs=$(xcrun simctl get_app_container <udid> com.stormkiln.heycoco.display data)/Documents
printf 'pair FFCE-42CE-F4C8-2DE2-E73A-FAB9\n' > "$docs/coco-debug-commands.txt"
printf "say: what's the weather\n" > "$docs/coco-debug-commands.txt"
cat "$docs/coco-debug-log.txt"
```

Commands (`DebugCommand`): `pair <fingerprint>`, `pair-code <fingerprint> <code>`,
`pair-uri <cocohub://pair?...>`, `say: <text>`, `timer <seconds> [name]`, `stop-alarm`,
`show idle|timers|now_playing`, `night sleep|wake`, `forget`, `screenshot <name>`,
`rotate portrait|landscape`, `now-playing <title> [| <artist>]` / `now-playing off` (music as
the Hub would report it, to look at Now Playing without Spotify), and `view <name>`. Pairing still
requires the fingerprint to match the one the app sees.

`view <name>` shows the `ViewSpec` JSON in `$docs/<name>.json` (the protocol's view object, e.g.
`{"type": "weather", "place": "Paris, France", "current": …, "hourly": […], "daily": […],
"compare": {"name": "Home", …}}`) as if the Hub had sent it: recorded data for a screen that is
hard to reach live, such as weather beside home with rain on one side. Weather hours and days are
filtered by the clock, so write the fixture with times from now. It needs no Hub, but a Hub-relative
media path (`/v1/media/…`) only loads while connected.

To try the easier pairing (#89) without a camera, launch without `-CocoDebugToken` (or send
`forget`), so the app looks for a Hub to pair with, and run the pair command against the scratch
Hub: `cd hub && COCO_CONFIG=/tmp/coco-scratch/config.toml uv run python -m coco_hub --pair`. Then
`pair-code <fingerprint> <code>` with what it printed is Enter code. `pair-uri` is Scan QR code;
the URI isn't printed as text (it holds the token), so build it with
`coco_hub.pairing.pairing_uri(...)` from the scratch config. The simulator has no camera: Scan QR
code shows "No camera available".

## 4. Look at it

```sh
xcrun simctl io <udid> screenshot /tmp/coco.png
```

A permission alert (microphone, notifications) the simulator can't dismiss without a tap covers
that screenshot. The `screenshot <name>` command draws only the app's own windows, to
`$docs/<name>.png`. For dark mode (what night mode shows): `xcrun simctl ui <udid> appearance dark`.

Night mode follows the Hub's `[night]` window; `night sleep` only works inside it. Stop the
scratch Hub when done.
