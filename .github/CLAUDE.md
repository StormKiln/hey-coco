# .github

CI and TestFlight workflows. The root `CLAUDE.md` rules apply here too. Patterns borrowed from
NextRound (TestFlight status, provenance) and headstate (required-context guard, SHA pins,
dry-run rehearsal).

## The ruleset requires exactly one check: `ci-gate`

`ci-gate` is the last job in `ci.yml`. It `needs` every other job, runs `if: always()`, and
passes only if every needed job's result is `success`.

- **Only `success` passes.** `skipped` and `cancelled` fail the gate. Branch protection treats a
  skipped *required* check as passing, so a gate that skipped along with a failed dependency
  would wave the PR through. headstate lost a release to treating `skipped` as fine.
- **Never rename `ci-gate`** (the job id, or a `name:` on it). The ruleset would keep requiring a
  context nothing reports, and every PR would wait forever. No branch can fix that; only an
  admin editing the ruleset can. If it must change: change `REQUIRED_CONTEXTS` in
  `scripts/check-required-contexts.py` in the same PR, and edit the ruleset as soon as it
  merges.
- **Adding or renaming any other job needs no ruleset edit**: add it to `ci-gate`'s `needs`.
  `make repo-check` fails if a job is missing from `needs`, if `ci-gate` disappears, or if it
  loses `if: always()`.
- Matrix legs report as `display (Xcode 16.3)` etc. Never require those directly; the gate
  covers them.

## Supply chain

- Every third-party `uses:` is pinned to a 40-hex commit SHA with a `# vX.Y.Z` comment
  (`scripts/check-action-pins.py`, in `make repo-check`). Look up a SHA with
  `gh api repos/<owner>/<repo>/git/ref/tags/<tag>`, dereferencing annotated tags to the commit.
  Dependabot (`dependabot.yml`) bumps actions and the Hub's `uv.lock` weekly.
- Every workflow has top-level `permissions: contents: read`; checkouts that never push use
  `persist-credentials: false`.
- CI runs uv with `UV_LOCKED=1` and `make hub-sync` uses `--locked` under `CI`: a stale
  `hub/uv.lock` fails CI instead of being re-locked on the runner.
- actionlint runs in CI from a pinned release tarball with its SHA-256 checked
  (`make check-workflows` locally skips if actionlint is not installed).

## Pinned Xcode

Runners are explicit images (`macos-15`, `macos-26`), and Xcode is chosen with `DEVELOPER_DIR`
by `make ci-xcode XCODE_VERSION=...`, which fails naming the installed versions if the path is
missing. Never rely on the image default: an image update would silently change the compiler.

- `kit-test` runs on the oldest that compiles CocoKit (16.3 on `macos-15`; 16.0-16.2, Swift 6.0.x, reject
  `TimerStore.swift` with a spurious actor-isolation error) and the primary (26.6 on `macos-26`).
- `display-build` and the TestFlight workflow use the primary, `XCODE_PRIMARY` in the Makefile.
  `make repo-check` fails if `ci.yml` has no leg on `XCODE_PRIMARY`.
- When GitHub drops an Xcode from an image, `ci-xcode` fails with the list of what is there:
  pick the replacement from that list, or from the image README in actions/runner-images.

## TestFlight

`testflight.yml` defaults to `dry_run: true` (checks + unsigned archive artifact, no secrets).
A real run checks secrets (Preflight), main ancestry, and that App Store Connect has no build
number as high as this one, all before signing, then waits for Apple's processing.
`testflight-status.yml` re-checks processing later. See `docs/testflight.md`.
