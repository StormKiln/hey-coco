## Change

What problem this solves and what behaves differently now. `Closes #...` only if this PR
finishes the issue.

## Validation

Counts from the runs, not just "passed":

- [ ] `make lint`:
- [ ] `make hub-test`: N passed
- [ ] `make kit-test`: N tests passed
- [ ] `make display-build`:
- [ ] Other (`make repo-check`, `make hub-resilience`, manual checks):

## On-device checks still needed

What only the kitchen iPad (iPadOS 18) or the Hub's Mac can show: wake word, audio, Spotify,
pairing, Hub restarts. "None" if nothing.

## Limitations

Known gaps, follow-ups, release implications.

## Checklist

- [ ] Protocol changes update `docs/protocol/`, the Hub models, the Swift models and the fixtures
      together, in this PR.
- [ ] "Could not check" is never reported as "nothing found" (e.g. weather *stale* or
      *unavailable* is not "no weather"; a failed query is an error, not an empty list).
- [ ] No secrets or tokens in URLs, logs, fixtures or commits.
- [ ] `sleep:` defaults use `cocoRealSleep`, not an inline async closure (CocoSleep.swift).
