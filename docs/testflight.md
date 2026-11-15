# TestFlight: getting Coco Display onto the iPad

Issue #4. Once the one-time setup below is done, every new build is one command:

```sh
make display-testflight
```

It checks the config, runs the preflight (below), archives a signed Release build
(`display/build/CocoDisplay.xcarchive`) and uploads it to App Store Connect. The build appears
in TestFlight after Apple's processing (usually 10 to 30 minutes) and the iPad's TestFlight app
offers it. Follow the processing with `make testflight-status BUILD=<n>`.

The usual order, from GitHub (see "Uploading from GitHub Actions"):

1. **Rehearse**: run the TestFlight workflow with `dry_run` ticked (the default). It runs the
   checks and an unsigned Release archive, needs no secrets, uploads nothing, and keeps the
   archive as an artifact. Locally: `make display-archive-unsigned`.
2. **Upload**: run it again from `main` with `dry_run` unticked.
3. **Status**: the upload waits up to 20 minutes for Apple's processing. If Apple is slower,
   run the **TestFlight status** workflow (or `make testflight-status`) later.

### Preflight: provenance and build number

`make display-testflight-preflight` (run by `make display-testflight` and by the workflow,
before anything is signed) refuses to continue unless:

- HEAD is on `origin/main`'s history (`git fetch origin main` first if it says otherwise), and
- the build number is greater than every build App Store Connect already has for the current
  `MARKETING_VERSION`. Apple refuses a build number it has seen; this finds out before a
  20-minute archive rather than after. Fix with an up-to-date `main` or `BUILD_NUMBER=<higher>`.

It only reads from App Store Connect (`display/tools/asc.py`, with the same `ASC_*` env as the
upload). If App Store Connect cannot be reached, the preflight fails: it never treats "could
not check" as "no builds".

### Processing status

```sh
make testflight-status BUILD=123                 # VERSION defaults to MARKETING_VERSION
make testflight-status VERSION=0.1.0 BUILD=123 WAIT_MINUTES=5
```

Read-only. Exits 0 when Apple marks the build VALID, 1 if Apple marks it FAILED or INVALID (check
Apple's email), 2 if it is still processing when the wait ends. Do not re-upload just to check.

What lives where:

| Value | Where | Committed? |
|---|---|---|
| Bundle ID default `com.stormkiln.heycoco.display`, automatic signing | `display/Signing.xcconfig` | yes |
| Your team ID, bundle ID override | `display/Signing.local.xcconfig` | no (gitignored) |
| App Store Connect API key ID, issuer ID, `.p8` path | env `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_PATH` | no |
| The `.p8` key file | anywhere outside the repo, e.g. `~/.appstoreconnect/private_keys/` | no (`*.p8` is gitignored too) |
| Version shown to testers (`0.1.0`) | `MARKETING_VERSION` in `display/project.yml` | yes |
| Build number | the commit count, `git rev-list --count HEAD`, at archive time | n/a |

No certificate or provisioning profile is stored anywhere in the repo: signing is automatic, and
Xcode creates or downloads what it needs (`-allowProvisioningUpdates`).

## One-time setup

You need a paid Apple Developer Program membership and Xcode on the Mac you upload from.

### 1. Find your team ID

developer.apple.com, then Account, then Membership details: **Team ID**, 10 characters such as
`ABCDE12345`.

### 2. Register the bundle ID

developer.apple.com, then Certificates, Identifiers & Profiles, then Identifiers, then **+**:

1. App IDs, then App.
2. Description: `Coco Display`. Bundle ID: **Explicit**, `com.stormkiln.heycoco.display`.
   If Apple says it is taken (bundle IDs are global), pick your own, for example
   `com.<yourname>.coco.display`, and use it in step 5.
3. Capabilities: leave the defaults. Coco needs none of the optional ones today (if a later
   build adds MusicKit, tick it here then).

Automatic signing can also register the ID on the first archive, but doing it by hand first
makes step 3 simpler.

### 3. Create the app record in App Store Connect

appstoreconnect.apple.com, then Apps, then **+**, then New App:

- Platform: **iOS**.
- Name: the name testers see in TestFlight. App names are unique across the whole App Store, so
  "Coco" is probably taken: try **Coco Kitchen** (or anything else; the icon on the iPad still
  says "Coco", which comes from the build, not from this name).
- Primary language: English.
- Bundle ID: pick the one from step 2.
- SKU: anything, e.g. `coco-display`.
- User access: Full access.

You do not need screenshots, a description or a privacy policy URL for internal TestFlight
testing.

### 4. Create an App Store Connect API key

appstoreconnect.apple.com, then Users and Access, then **Integrations**, then App Store Connect
API, then Team Keys, then **+** (the first time, request access to the API):

- Name: `coco-testflight`.
- Access: **App Manager**. For uploads from GitHub Actions (below), also tick **Access to Cloud
  Managed Distribution Certificate**, or use **Admin**.

Then:

- Note the **Issuer ID** (top of the page) and the key's **Key ID**.
- **Download** the `AuthKey_<KEYID>.p8` file. Apple lets you download it only once. Keep it out
  of the repo:

  ```sh
  mkdir -p ~/.appstoreconnect/private_keys
  mv ~/Downloads/AuthKey_*.p8 ~/.appstoreconnect/private_keys/
  chmod 600 ~/.appstoreconnect/private_keys/AuthKey_*.p8
  ```

### 5. Fill in `Signing.local.xcconfig`

```sh
cp display/Signing.local.xcconfig.example display/Signing.local.xcconfig
```

Edit it: set `DEVELOPMENT_TEAM` to your team ID, and uncomment `PRODUCT_BUNDLE_IDENTIFIER` only if
you registered a different bundle ID in step 2.

### 6. Export the API key settings

In your shell (or your shell profile, or a gitignored `.env` you source):

```sh
export ASC_KEY_ID=XXXXXXXXXX
export ASC_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
export ASC_KEY_PATH=~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8
```

### 7. Wake word model

The wake word needs no secret: its openWakeWord models are committed in `display/WakeWord/` and
bundled into the archive. Until `display/WakeWord/hey_coco.onnx` is trained and committed
(`make wakeword-train`, see `docs/wake-word.md`) the build works with tap-to-talk only, and the
check warns: "archive will ship without the wake word".

### 8. Check, then upload

```sh
make display-testflight-check   # validates everything above without building
make display-testflight         # archive + upload
```

The check lists every missing value at once. The first archive takes a while (it downloads
the ~50 MB ONNX Runtime package) and Xcode may create an Apple Distribution certificate in your
login keychain and an App Store provisioning profile for you; that is expected.

Upload from an up-to-date `main`: the build number is the commit count, and App Store Connect
rejects a build number it has already seen for the same version. To force one:
`make display-testflight BUILD_NUMBER=1234`.

### 9. Add testers and install on the iPad

1. App Store Connect, then your app, then **TestFlight**. Wait for the build to finish
   processing. Export compliance is already answered (the app declares it uses no non-exempt
   encryption), so it should go straight to "Ready to Test".
2. Internal Testing, then **+**, create a group (e.g. `Kitchen`), add yourself and anyone else
   on the team who should get builds, and add the build to the group (or tick "Automatically
   distribute").
3. On the iPad: sign in with that tester's Apple ID, install **TestFlight** from the App Store,
   open the invitation email (or TestFlight itself), then **Accept** and **Install**.

Then continue with `docs/kitchen-setup.md`, section 2.2.

TestFlight builds expire after 90 days. Upload a new build before then.

## Other targets

- `make display-archive`: archive only (needs the team ID; uses the API key if the env vars are
  set, otherwise the Apple ID signed in to Xcode).
- `make display-archive-unsigned`: the rehearsal archive, `display/build/CocoDisplay-unsigned.xcarchive`.
  Same Release configuration for devices, unsigned; needs no team or key.
- `make display-artifact [ARCHIVE=build/CocoDisplay-unsigned.xcarchive]`: zip an archive with
  `SHA256SUMS` and `commit.txt` into `display/build/artifact/` (what the workflow keeps).
- `make display-icon`: redraw the app icon from `display/tools/make-app-icon.swift`.
- `make display-build`: the simulator build CI runs. Always unsigned, needs none of the above.

## Uploading from GitHub Actions

`.github/workflows/testflight.yml` runs the same steps as `make display-testflight` on a GitHub
macOS runner, with the same pinned Xcode as CI's `display-build` (`XCODE_PRIMARY` in the
Makefile). It only runs when you start it: Actions, then TestFlight, then **Run workflow**. It
never runs on pull requests. Runs queue one at a time and are never cancelled mid-upload.

- **`dry_run` ticked (default): rehearsal.** Any branch. `make lint`, `make hub-test`,
  `make kit-test`, then an unsigned Release archive, kept 14 days as an artifact
  (`coco-display-unsigned-*`: zipped `.xcarchive`, `SHA256SUMS`, `commit.txt`). No secrets
  needed, nothing uploaded. The run fails if the openWakeWord feature models are missing from
  the app, and warns if `hey_coco.onnx` is (no wake word).
- **`dry_run` unticked: real upload.** In order, failing at the first problem: Preflight lists
  every missing required secret; HEAD must be on `main`'s history; the same lint and tests;
  `make display-testflight-check`; the App Store Connect build-number preflight; then the
  signed archive, the upload, and up to 20 minutes
  waiting for Apple's processing (fails if Apple marks it INVALID; still processing is only a
  notice). The API key file is deleted at the end whatever happened.

  **The signed archive is never kept as an artifact**: it is the distributable app, and this
  repo's artifacts are public. Only `SHA256SUMS` (the signed archive's
  hash) and `commit.txt` are kept, 14 days, as `coco-display-signed-provenance-*`.

To check processing later: Actions, then **TestFlight status**, then Run workflow with the
version and build number the upload printed. Read-only.

Values every StormKiln app shares go on the **org** (github.com/organizations/StormKiln/settings,
then Secrets and variables, then Actions), with repository access set to the repos that use them.
The org is on the Free plan, where org secrets reach public repositories only; hey-coco is public.
A repository secret or variable of the same name overrides the org's.

| Org | Kind | Value |
|---|---|---|
| `APPLE_TEAM_ID` | variable | Team ID from step 1 |
| `ASC_KEY_ID` | secret | Key ID from step 4 |
| `ASC_ISSUER_ID` | secret | Issuer ID from step 4 |
| `ASC_KEY_P8` | secret | `base64 -i AuthKey_XXXXXXXXXX.p8 \| gh secret set ASC_KEY_P8 --org StormKiln --visibility selected --repos hey-coco` |

| hey-coco repository | Kind | Value |
|---|---|---|
| `BUNDLE_ID` | variable | Only if you are not using `com.stormkiln.heycoco.display` |

Spotify and YouTube values are not here: the Hub uses them at run time on the Mac, from
`hub/config.local.toml`, and CI never needs them.

The runner has no Apple ID and no certificates. Automatic signing still works because
`-allowProvisioningUpdates` with the API key lets Xcode fetch the profile and sign with Apple's
cloud-managed distribution certificate. That needs the key to have Admin, or App Manager with
"Access to Cloud Managed Distribution Certificate". If the run fails at signing with a
permissions error, that is the setting to check.

## App Store requirements already handled

- App icon: `display/App/Assets.xcassets/AppIcon.appiconset` (single 1024×1024 image, drawn
  by `display/tools/make-app-icon.swift`).
- Privacy manifest: `display/App/PrivacyInfo.xcprivacy`. Declares UserDefaults (CA92.1) and
  file timestamps (C617.1, the media cache), no tracking, no collected data. If you later fill
  in App Privacy for a public release, "Data Not Collected" matches: audio and requests go only
  to your own Hub.
- `ITSAppUsesNonExemptEncryption = NO`: only standard TLS (and SHA-256 hashing).
- Display name "Coco", iPad only, all four orientations, full screen.
- Usage descriptions for the microphone, speech recognition and local network.

## Troubleshooting

| Error | Fix |
|---|---|
| `No Accounts` / `No signing certificate "iOS Distribution" found` | Set the `ASC_*` env vars (the archive then authenticates with the API key), or sign in to Xcode with your Apple ID (Xcode, Settings, Accounts). |
| `No profiles for '...' were found` | The bundle ID in `Signing.local.xcconfig` doesn't match one registered to your team (step 2), or `DEVELOPMENT_TEAM` is wrong. |
| `The bundle version must be higher than the previously uploaded version` | Build number already used: pull `main`, or pass `BUILD_NUMBER=` higher than the last upload. The preflight normally catches this before archiving. |
| `HEAD is not on origin/main's history` | Merge first, then `git fetch origin main` and upload from `main`. Rehearsals (`dry_run`) are allowed from any branch. |
| `Missing required icon file` / `CFBundleIconName` | Run `make display-gen` so the project includes `Assets.xcassets`. |
| Upload succeeds but no build in TestFlight | Wait for processing; check the email Apple sends if processing failed (often a privacy manifest or usage-description issue). |
