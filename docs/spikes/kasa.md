# Spike #132: Kasa device probe

Part of #131 (smart plugs). Question: which local protocol do real Kasa devices use, do they
need the TP-Link login, and how fast is a local state read?

## Running the probe

```bash
make kasa-probe HOST=<device ip>                         # read-only
make kasa-probe HOST=<device ip> ARGS="--toggle"           # flips the device and back
make kasa-probe HOST=<device ip> ARGS="--toggle --child 0" # one outlet (index or alias)
```

It sends one unicast query to that address (no network scan), prints model, hardware and
firmware version, alias, outlets and state, which protocol the device negotiated and whether
that needs the TP-Link login, and times 10 state reads. KLAP/AES devices need
`[backends.kasa]` `username` / `password` in `hub/config.local.toml` (never printed).
Exit codes: 0 OK, 1 the toggle didn't read back as expected, 2 unreachable / login needed /
unsupported.

## Results

Recorded without household details (no names, addresses or MACs).

| Device class | Protocol | Login needed | p50 read | p95 read | Toggle |
| --- | --- | --- | --- | --- | --- |
| _pending: owner runs the probe_ | | | | | |

## Findings

_Pending the first real-device run._
