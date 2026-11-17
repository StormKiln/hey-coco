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

| Device | Protocol | Login needed | p50 read | p95 read |
| --- | --- | --- | --- | --- |
| Device 1 | legacy (XOR) | No | 40 ms | 129 ms |

## Findings

- Auth: the legacy local protocol answered with no credentials.
  KLAP/AES (login) is covered by fake-device tests only.
- 10 state reads over Wi-Fi: p50 40 ms, p95 129 ms.
  Connect (one discovery query + first read): ~1 s.
  Both fit inside the Hub's 2 s per-call limit.
- Not measured: `--toggle` switching latency.
