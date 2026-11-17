# Smart plugs (TP-Link Kasa)

Coco can switch TP-Link Kasa smart plugs and power strips by voice: "hey coco, turn on the
desk lamp", "is the fan on". It talks to them **directly on your local network** with
[python-kasa](https://github.com/python-kasa/python-kasa): no TP-Link cloud round trip, no
Kasa app running, nothing else to install. Part of epic #131.

Nothing is hard-coded: Coco only knows the devices you list in the config. The names in this
guide ("desk lamp", "fan", "power strip") and the addresses (`192.0.2.x`, a documentation
range) are examples; use your own.

## What you can say

| Say | Coco does |
| --- | --- |
| "turn on the desk lamp" / "turn off fan" | switches it, then reads it back |
| "switch the fan on" / "switch the fan off" | same |
| "desk lamp on" / "desk lamp off" | same |
| "is the desk lamp on" | reads the state now and tells you |
| "dim the desk lamp" | "The desk lamp can't dim." (plugs only switch on and off) |

Coco only confirms what the device reports after the switch: "Desk lamp is on.", or "Desk
lamp was already on." if there was nothing to do. If the device takes the command but still
reports the old state, Coco says "I asked the desk lamp to turn on, but it still says it's
off." A power strip with some outlets on and some off is reported as such ("Power strip is
partly on: Monitor is on, Speakers is off."). Otherwise it says plainly what went wrong:

- "I couldn't reach the desk lamp. It may be unplugged or off the network."
- "The desk lamp needs a login before I can control it. See the Hub's device setup."
- "I don't know a device called toaster." (or "No devices are set up yet.")

Avoid naming a device after one of Coco's own features (music, radio, timer, alarm, sound,
picture, video, volume, screen): "turn the music off" keeps meaning the music.

Device commands are matched by the rules engine only; they are **never** passed to the Qwen
fallback. A misheard request gets "I don't know a device called ..." or no match, never a
plug switched by an LLM's best guess.

## Setup

### 1. Give each plug a fixed address (DHCP reservation)

Coco finds a device by its IP address. Plugs get their address from your router by DHCP, and
it can change after a power cut or router restart. In your router's admin page, add a
**DHCP reservation** (sometimes "static lease" or "address reservation") for each plug, keyed
on its MAC address. The Kasa app shows the MAC under the device's settings, and your router's
client list usually shows both the MAC and the current address. Restart the plug afterwards
so it picks up the reserved address.

The Hub's Mac and the plugs must be on the **same network and subnet**, and the router must
not isolate them from each other. If your plugs live on a separate "IoT" or guest network,
see [Troubleshooting](#troubleshooting).

### 2. Probe each device

On the Hub's Mac, from the repo root:

```sh
make kasa-probe HOST=192.0.2.10
```

This sends one query to that one address (it does not scan your network) and changes
nothing. It prints what the device says about itself:

```text
Host:         192.0.2.10
Model:        <model>  (hw <hw>, fw <firmware>)
MAC:          <mac>
Alias:        'desk lamp'
Protocol:     legacy (XOR): no TP-Link login needed
Credentials:  none
State:        off
Connect:      ... ms (discovery + first read)
State reads:  10, p50 ... ms, p95 ... ms, mean ... ms
```

A multi-outlet plug or power strip also lists its outlets, numbered from 0:

```text
Outlets (3):
  0: 'monitor' on
  1: 'speakers' off
  2: 'charger' off
```

The lines that matter for setup:

- **Protocol** says whether the device needs your TP-Link login (step 3).
- **Alias** and **Outlets** are the names set in the Kasa app; Coco uses them when you don't
  give a name in the config (step 4).
- **MAC** is what you'd put in the optional `mac` safety check.

To check that switching works, add `--toggle`: it flips the device and then flips it back, and
checks each step by reading the state. `--child` picks one outlet by number or name:

```sh
make kasa-probe HOST=192.0.2.10 ARGS="--toggle"
make kasa-probe HOST=192.0.2.11 ARGS="--toggle --child 0"
make kasa-probe HOST=192.0.2.11 ARGS='--toggle --child "speakers"'
```

Mind what's plugged in before you toggle it. Exit codes: `0` OK, `1` a toggle didn't read
back as expected, `2` unreachable, login needed, or unsupported device (the message says
which).

The probe output includes the model, firmware and MAC: don't paste it anywhere public.

### 3. Credentials: only if the probe says the device needs them

Kasa firmware uses one of two kinds of local protocol:

- **Legacy (XOR)**, on older firmware: **no login needed.** Leave credentials out.
- **KLAP or AES**, on newer firmware: the device wants your **TP-Link account
  username/password** for the *local* handshake. Coco still talks to the device directly on
  your network; the login is only used to set up the encrypted local connection, and nothing
  goes through TP-Link's cloud.

If the probe says `needs the TP-Link login`, or fails with "The device needs the TP-Link
login", put the TP-Link login in **`hub/config.local.toml`** (gitignored, never in
`config.example.toml` or any committed file):

```toml
[backends.kasa]
username = "you@example.com"
password = "your TP-Link password"
```

or in the environment as `COCO_BACKENDS__KASA__USERNAME` / `COCO_BACKENDS__KASA__PASSWORD`.
Then run the probe again: `Credentials:` should say `used (from config, not shown)` and the
device should answer. The password is never printed or logged.

The Hub only sends the login to a device that asks for it: it tries every device without
credentials first, so legacy devices never see it.

**Third-party compatibility.** On some firmware, local control by anything other than the
Kasa app is off until you enable it. If a KLAP/AES device still refuses the correct login, open
the device in the Kasa app and look in its settings for a **third-party
compatibility** (or "third-party services") option, turn it on, and probe again. Not every
device or firmware has this option; if yours doesn't, you don't need it.

### 4. Name your devices

Add one `[[devices]]` entry per thing you want to say out loud, in `hub/config.local.toml`
(see the commented example at the bottom of `hub/config.example.toml`):

```toml
[backends.kasa]
# timeout_s = 2.0              # limit on each call to a device (seconds, max 10)

[[devices]]                    # a whole single-outlet plug
name = "desk lamp"
aliases = ["lamp", "reading light"]
backend = "kasa"
options = { host = "192.0.2.10" }

[[devices]]                    # one outlet of a power strip
name = "speakers"
backend = "kasa"
options = { host = "192.0.2.11", child = 1 }

[[devices]]                    # the whole power strip: every outlet together
name = "power strip"
backend = "kasa"
options = { host = "192.0.2.11", mac = "<mac from the probe>" }
```

- **`name`** is what you call it and what Coco says back. It's optional: leave it out and
  Coco uses the name set in the Kasa app (the probe's `Alias`, or the outlet's name). Setting
  it is more robust (see below).
- **`aliases`** are extra names that also work. Names are matched the way speech is
  transcribed (case and a leading "the" don't matter). Two devices can't share a name: the
  Hub refuses to start and says which name is taken twice. Names that come from the devices
  themselves are only known once they answer, so a clash between two unnamed devices can't be
  caught at startup: the first device keeps the name and the log (`make hub-logs`) warns.
  Give one of them a `name`.
- **`options.host`** is the reserved IP address (or a hostname).
- **`options.child`** picks **one outlet** of a multi-outlet plug or strip: its number from
  the probe's `Outlets` list (0-based) or its name. A number always means the position, so
  for an outlet whose name is only digits, use its number. Without `child`, the entry controls the
  **whole device**: "turn off the power strip" switches every outlet, and "is the power strip
  on" reports mixed outlets as mixed. You can configure the whole strip and some of its outlets
  as separate entries, as above.
- **`options.mac`** (optional) is a safety check: if a *different* device ever answers at
  that address (say the reservation was lost and another plug got it), Coco refuses to control
  it and reports the device as unreachable. It does not go looking for the device elsewhere.

**Why set `name`.** An entry without `name` has no spoken name until the device first
answers, because its name lives on the device. If it's unplugged when the Hub starts, Coco
doesn't know it yet ("I don't know a device called ..."); the Hub retries every minute and the
name works as soon as the device answers. With `name` set, the name works from startup and
Coco just says the device is unreachable until it's back. A name with `name` set also survives
someone renaming the device in the Kasa app.

### 5. Restart the Hub

The Hub reads devices at startup. After editing the config, restart it: stop and rerun
`make hub-run`, or for the launchd service run `make hub-install-service` again. A config
mistake (unknown backend, missing `host`, duplicate name) stops the Hub at startup with a
message saying which entry is wrong; `make hub-logs` (or `make hub-logs-launchd` for startup
crashes) shows it.

Then try "hey coco, is the desk lamp on?".

## Firmware updates

TP-Link firmware updates have, for some users, switched a device from the legacy protocol
to KLAP, which needs the login for local control. A device that worked yesterday then stops
answering Coco after an update (Coco says it needs a login, or can't reach it).

The tradeoff: updates bring security fixes (TP-Link has published advisories for some Kasa
models), and may change how local control works. Keeping auto-update off avoids surprises but
leaves known issues unfixed. Either way, if a device stops working after an update:

1. Run `make kasa-probe HOST=<address>` and read the **Protocol** line.
2. If it now says KLAP or AES (`needs the TP-Link login`), add `[backends.kasa]`
   `username` / `password` (step 3) and restart the Hub.
3. If the login is set and still rejected, check the third-party compatibility option in the
   Kasa app (step 3).

## Troubleshooting

**Coco says it can't reach the device / probe: "No Kasa device answered"**

- Is the plug powered, and does it still work from the Kasa app on the same Wi-Fi?
- Has its address moved? Check the router's client list; if it changed, the DHCP reservation
  is missing or wasn't applied (restart the plug after adding one). With `mac` set, a
  different device at the configured address also shows up as unreachable.
- Same network and subnet: the Hub's Mac and the plug must be able to reach each other
  directly. Plugs on a guest or IoT network, or behind **AP / client isolation** (often a
  default on guest networks and some mesh systems), can't be reached from the Mac.

**Coco says the device needs a login / probe: "needs the TP-Link login"**

The device uses KLAP/AES. Add or fix `[backends.kasa]` `username` / `password` (your TP-Link
account, the one the Kasa app signs in with), check the third-party compatibility option, and
restart the Hub. If you changed your TP-Link password, update it here too.

**"I don't know a device called X"**

- Check the `name` and `aliases` in your `[[devices]]` entries, and say it the way it's
  written there (or as the Kasa app names it if `name` is unset).
- An entry without `name` gets its name only once the device has answered; if it was
  unreachable when the Hub started, either wait for the next retry (about a minute) or set
  `name`.
- A `child` that doesn't match any outlet (wrong number or outlet name) leaves that entry
  without a device; `make hub-logs` shows a warning listing the outlets the device really
  has. Check the outlet list with the probe.
- Did you restart the Hub after editing the config?

**Slow answers or timeouts**

Each call to a device (connect, read, switch) has a limit of `[backends.kasa] timeout_s`
(default 2 seconds); a device that doesn't answer in time is reported as unreachable. The
probe's `State reads` line shows how fast the device normally answers: if p95 is close to
the limit, the Wi-Fi link to the plug is weak (move it, or the access point, closer) and you
can raise `timeout_s` up to 10. A one-off timeout is usually the plug rebooting or Wi-Fi
roaming; the next request reconnects.

## Adding another backend (developers)

Kasa is the first backend of a generic device layer in `hub/src/coco_hub/devices/`. Another
brand (Shelly, Hue, Matter, Home Assistant, ...) is a new backend module plus config; the
intents, the config schema and the Hub <-> Display protocol don't change.

1. Write `hub/src/coco_hub/devices/backends/<name>.py` with a `DeviceBackend` subclass
   (`devices/backend.py`): `parse_options` validates the `[[devices]]` `options` at startup,
   `connect` returns a `DeviceHandle` with `alias`, `capabilities`, `get_state` (always a live
   read) and `set_power`. Raise only the typed errors in `devices/errors.py` (`Unreachable`,
   `AuthRequired`, `UnknownDevice`, `Unsupported`). Keep the vendor library's imports in that
   one module.
2. Register it in `BACKENDS` in `devices/backends/__init__.py` (`"<name>":
   "coco_hub.devices.backends.<name>:<Class>"`). It's imported only when a configured device
   uses it.
3. Add a `Rig` for its fake device to `hub/tests/devices/test_backend_contract.py` and a
   `BackendContract` test class, so it passes the shared contract in
   `hub/tests/devices/backend_contract.py`. Tests never talk to real devices.

Shared settings for the backend go in `[backends.<name>]` (`timeout_s`, and optional
`username` / `password` / `token`, kept as secrets).
