.DEFAULT_GOAL := help
SIM_DEST ?= generic/platform=iOS Simulator

# launchd agent for the Hub
SERVICE_LABEL := com.stormkiln.coco-hub
HUB_DIR := $(CURDIR)/hub
UV_BIN ?= $(shell command -v uv)
PLIST_TEMPLATE := hub/deploy/$(SERVICE_LABEL).plist.template
PLIST_DEST := $(HOME)/Library/LaunchAgents/$(SERVICE_LABEL).plist
HUB_LOG_DIR := $(HOME)/Library/Logs/CocoHub
LAUNCH_DOMAIN = gui/$$(id -u)

.PHONY: help hub-setup hub-sync hub-run hub-test hub-resilience hub-lint hub-fingerprint hub-eval hub-fmt hub-render-plist hub-install-service hub-uninstall-service hub-logs hub-logs-launchd hub-diag spotify-login spotify-spike sounds-build display-gen display-build display-testflight-check display-testflight-preflight display-archive display-archive-unsigned display-artifact display-testflight-upload display-testflight testflight-status display-icon display-build-mac display-run-mac display-test kit-test test lint repo-check check-workflows ci-xcode

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

hub-setup: ## Bootstrap the Hub's Mac: uv, Ollama + model, hub/config.local.toml, fingerprint (re-runnable)
	python3 hub/deploy/setup.py $(ARGS)

# Under CI (CI=true) the lockfile must already match pyproject.toml: --locked fails instead of
# re-locking. Locally a plain sync may update uv.lock; commit that.
hub-sync: ## Install Hub dependencies (--locked under CI)
	cd hub && uv sync $(if $(CI),--locked,)

hub-run: hub-sync ## Run the Hub locally with reload (hub/config.local.toml + COCO_* env)
	cd hub && uv run python -m coco_hub --reload

hub-test: hub-sync ## Run Hub tests
	cd hub && uv run pytest -q

hub-resilience: hub-sync ## Run the real Hub as a subprocess: kills, restarts, internet/Ollama down (~1 min)
	cd hub && uv run pytest -q -m resilience tests/resilience

hub-lint: hub-sync ## Lint and format-check the Hub
	cd hub && uv run ruff check . && uv run ruff format --check .

hub-eval: hub-sync ## Score rules + Qwen on hub/eval/utterances.jsonl (needs local Ollama; not CI)
	cd hub && uv run python eval/run_eval.py $(ARGS)

hub-fmt: hub-sync ## Auto-format the Hub
	cd hub && uv run ruff format . && uv run ruff check --fix .

hub-render-plist: ## Print the rendered launchd plist to stdout
	@test -n "$(UV_BIN)" || { echo "uv not found; set UV_BIN=/path/to/uv" >&2; exit 1; }
	@sed -e 's|@LABEL@|$(SERVICE_LABEL)|g' \
		-e 's|@UV_DIR@|$(patsubst %/,%,$(dir $(UV_BIN)))|g' \
		-e 's|@UV@|$(UV_BIN)|g' \
		-e 's|@HUB_DIR@|$(HUB_DIR)|g' \
		-e 's|@LOG_DIR@|$(HUB_LOG_DIR)|g' \
		$(PLIST_TEMPLATE)

hub-install-service: hub-sync ## Install + start the Hub launchd agent (starts at login, restarts on crash)
	@test -f hub/config.local.toml || { echo "Create hub/config.local.toml first (see hub/config.example.toml)" >&2; exit 1; }
	mkdir -p "$(HUB_LOG_DIR)" "$(dir $(PLIST_DEST))"
	$(MAKE) -s hub-render-plist > "$(PLIST_DEST).tmp"
	plutil -lint "$(PLIST_DEST).tmp"
	mv "$(PLIST_DEST).tmp" "$(PLIST_DEST)"
	-launchctl bootout $(LAUNCH_DOMAIN)/$(SERVICE_LABEL) 2>/dev/null
	launchctl bootstrap $(LAUNCH_DOMAIN) "$(PLIST_DEST)"
	@echo "Installed $(SERVICE_LABEL). Follow logs with: make hub-logs"

hub-uninstall-service: ## Stop and remove the Hub launchd agent
	-launchctl bootout $(LAUNCH_DOMAIN)/$(SERVICE_LABEL) 2>/dev/null
	rm -f "$(PLIST_DEST)"

HUB_PORT ?= 8765

spotify-login: ## Print the URL to connect Spotify (open it in a browser on the Hub's Mac)
	@echo "With the Hub running, open this on the Hub's Mac (loopback only):"
	@echo "  open https://127.0.0.1:$(HUB_PORT)/spotify/login"
	@echo "The browser warns about the Hub's self-signed certificate: continue to 127.0.0.1."
	@echo "See docs/spotify.md for the one-time Spotify developer app setup."

spotify-spike: hub-sync ## Spike #5: log iPad visibility in Spotify (ARGS="--hours 10 --cold-start-every 30")
	cd hub && uv run python -m coco_hub.media.spotify.spike $(ARGS)

hub-fingerprint: hub-sync ## Print the Hub's TLS fingerprint (compare it with the iPad when pairing)
	@cd hub && uv run --quiet python -m coco_hub --fingerprint

hub-logs: ## Follow the Hub's JSON log (hub.jsonl), pretty-printed
	@mkdir -p "$(HUB_LOG_DIR)"
	@tail -n 100 -F "$(HUB_LOG_DIR)/hub.jsonl" | python3 -u hub/src/coco_hub/logfmt.py

hub-logs-launchd: ## Follow launchd's raw stdout/stderr capture (startup crashes land here)
	@mkdir -p "$(HUB_LOG_DIR)"
	tail -n 100 -F "$(HUB_LOG_DIR)/hub.out.log" "$(HUB_LOG_DIR)/hub.err.log"

# The token goes to curl on stdin (-H @-), never on a command line or the terminal. curl
# trusts only the Hub's own certificate (--cacert), which names 127.0.0.1.
hub-diag: ## Show GET /v1/diagnostics from the local Hub (token from hub/config.local.toml / env)
	@cd hub && uv run --quiet python -c 'from coco_hub.settings import load_settings; print("Authorization: Bearer " + load_settings().token.get_secret_value())' \
		| curl -fsS --cacert "$$(uv run --quiet python -c 'from coco_hub.settings import load_settings; from coco_hub.identity import load_or_create; print(load_or_create(load_settings().data_dir).cert_path)')" \
			-H @- "https://127.0.0.1:$(HUB_PORT)/v1/diagnostics" | python3 -m json.tool

sounds-build: ## Rebuild the sound library from assets/sounds/sources.toml (needs ffmpeg, sips; network)
	uv run --with httpx python assets/sounds/tools/curate.py build $(ARGS)

display-gen: ## Generate the Xcode project from display/project.yml
	cd display && xcodegen generate -q

kit-test: ## Run CocoKit (Display logic) tests on macOS
	cd display/CocoKit && swift test

display-build: display-gen ## Build Coco Display for the iOS simulator (unsigned)
	cd display && xcodebuild -project CocoDisplay.xcodeproj -scheme CocoDisplay -destination '$(SIM_DEST)' -clonedSourcePackagesDirPath .spm build -quiet CODE_SIGNING_ALLOWED=NO

# TestFlight (#4, docs/testflight.md). Team ID and bundle ID come from display/Signing.local.xcconfig
# (env DEVELOPMENT_TEAM overrides the team); the upload authenticates with an App Store Connect API
# key from env ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH. The build number must grow with every
# upload: by default it is the commit count, so upload from main (or pass BUILD_NUMBER=...).
# display-testflight-preflight refuses a build number App Store Connect already has.
TESTFLIGHT := display/tools/testflight.sh
ARCHIVE_PATH := build/CocoDisplay.xcarchive
UNSIGNED_ARCHIVE_PATH := build/CocoDisplay-unsigned.xcarchive
EXPORT_OPTIONS := build/ExportOptions.plist
BUILD_NUMBER ?= $(shell git rev-list --count HEAD)
MARKETING_VERSION = $(shell $(TESTFLIGHT) marketing-version)
ASC_AUTH = $(if $(and $(ASC_KEY_PATH),$(ASC_KEY_ID),$(ASC_ISSUER_ID)),-authenticationKeyPath "$(abspath $(ASC_KEY_PATH))" -authenticationKeyID "$(ASC_KEY_ID)" -authenticationKeyIssuerID "$(ASC_ISSUER_ID)",)
# Read-only App Store Connect queries (display/tools/asc.py). Runs in the Hub's locked
# environment for `cryptography` (ES256 token signing): no second dependency pin to keep current.
ASC := uv run --quiet --project hub --locked python display/tools/asc.py
# testflight-status: VERSION defaults to MARKETING_VERSION; BUILD is required.
VERSION ?= $(MARKETING_VERSION)
WAIT_MINUTES ?= 20
PENDING_EXIT ?= 2

display-testflight-check: ## Check the TestFlight config (team, bundle ID, ASC key env) without building
	@BUILD_NUMBER=$(BUILD_NUMBER) $(TESTFLIGHT) check upload

display-testflight-preflight: ## Refuse unless HEAD is on origin/main and BUILD_NUMBER beats App Store Connect's highest
	$(ASC) preflight --bundle-id "$$($(TESTFLIGHT) bundle-id)" --version "$(MARKETING_VERSION)" --build "$(BUILD_NUMBER)"

display-archive: display-gen ## Archive a signed Release build to display/build/CocoDisplay.xcarchive
	@BUILD_NUMBER=$(BUILD_NUMBER) $(TESTFLIGHT) check archive
	rm -rf display/$(ARCHIVE_PATH)
	cd display && xcodebuild -project CocoDisplay.xcodeproj -scheme CocoDisplay -configuration Release \
		-destination 'generic/platform=iOS' -clonedSourcePackagesDirPath .spm \
		-archivePath $(ARCHIVE_PATH) archive -quiet -allowProvisioningUpdates $(ASC_AUTH) \
		CODE_SIGNING_ALLOWED=YES CODE_SIGN_STYLE=Automatic \
		DEVELOPMENT_TEAM=$$(cd .. && $(TESTFLIGHT) team) CURRENT_PROJECT_VERSION=$(BUILD_NUMBER)
	@echo "Archived build $(BUILD_NUMBER) to display/$(ARCHIVE_PATH)"

# The TestFlight rehearsal (dry_run): the same Release archive for devices, unsigned, so it needs
# no team, no API key and no secrets. Proves the Release build compiles and links.
display-archive-unsigned: display-gen ## Archive an UNSIGNED Release build (TestFlight rehearsal; no secrets)
	rm -rf display/$(UNSIGNED_ARCHIVE_PATH)
	cd display && xcodebuild -project CocoDisplay.xcodeproj -scheme CocoDisplay -configuration Release \
		-destination 'generic/platform=iOS' -clonedSourcePackagesDirPath .spm \
		-archivePath $(UNSIGNED_ARCHIVE_PATH) archive -quiet \
		CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY= \
		CURRENT_PROJECT_VERSION=$(BUILD_NUMBER)
	@test -d display/$(UNSIGNED_ARCHIVE_PATH)/Products/Applications/CocoDisplay.app || { echo "No CocoDisplay.app in the archive" >&2; exit 1; }
	@echo "Archived unsigned build $(BUILD_NUMBER) to display/$(UNSIGNED_ARCHIVE_PATH)"

# Zip an archive (ARCHIVE=, relative to display/) with SHA256SUMS and commit.txt into
# display/build/artifact. The TestFlight workflow publishes the zip only for unsigned rehearsals
# (signed archives bundle the wake-word key; artifacts are public): see docs/testflight.md.
ARCHIVE ?= $(ARCHIVE_PATH)
ARTIFACT_DIR := display/build/artifact
display-artifact: ## Package an archive (ARCHIVE=, under display/) as zip + SHA256SUMS + commit.txt in display/build/artifact
	@test -d display/$(ARCHIVE) || { echo "No archive at display/$(ARCHIVE)" >&2; exit 1; }
	rm -rf $(ARTIFACT_DIR)
	mkdir -p $(ARTIFACT_DIR)
	ditto -c -k --keepParent display/$(ARCHIVE) $(ARTIFACT_DIR)/$(basename $(notdir $(ARCHIVE)))-$(MARKETING_VERSION)-$(BUILD_NUMBER).xcarchive.zip
	printf 'commit %s\nversion %s\nbuild %s\n' "$$(git rev-parse HEAD)" "$(MARKETING_VERSION)" "$(BUILD_NUMBER)" > $(ARTIFACT_DIR)/commit.txt
	cd $(ARTIFACT_DIR) && shasum -a 256 *.zip commit.txt > SHA256SUMS && cat SHA256SUMS

display-testflight-upload: ## Export + upload display/build/CocoDisplay.xcarchive (display-testflight runs the checks first)
	$(TESTFLIGHT) export-options display/$(EXPORT_OPTIONS)
	cd display && xcodebuild -exportArchive -archivePath $(ARCHIVE_PATH) \
		-exportOptionsPlist $(EXPORT_OPTIONS) -exportPath build/export -allowProvisioningUpdates $(ASC_AUTH)
	@echo "Uploaded $(MARKETING_VERSION) ($(BUILD_NUMBER)). Apple processes it (10-30 min); follow it with:"
	@echo "  make testflight-status BUILD=$(BUILD_NUMBER)"

display-testflight: display-testflight-check display-testflight-preflight display-archive display-testflight-upload ## Check, preflight, archive and upload Coco Display to TestFlight

testflight-status: ## Wait (read-only) for Apple to process BUILD=<n> [VERSION=x.y.z WAIT_MINUTES=20]
	@test -n "$(BUILD)" || { echo "Pass BUILD=<build number> (and VERSION= if not $(MARKETING_VERSION))" >&2; exit 1; }
	$(ASC) status --bundle-id "$$($(TESTFLIGHT) bundle-id)" --version "$(VERSION)" --build "$(BUILD)" --wait-minutes "$(WAIT_MINUTES)" --pending-exit-code "$(PENDING_EXIT)"

display-icon: ## Redraw the app icon PNG (display/tools/make-app-icon.swift)
	swift display/tools/make-app-icon.swift display/App/Assets.xcassets/AppIcon.appiconset/AppIcon.png

# Coco Display on an Apple silicon Mac as "Designed for iPad" (#25). Launching it needs a signed
# build: pass your Apple Developer team, e.g. `make display-run-mac DEVELOPMENT_TEAM=ABCDE12345`.
MAC_DEST ?= platform=macOS,arch=arm64,variant=Designed for iPad
MAC_DERIVED := DerivedData/mac
MAC_APP := display/$(MAC_DERIVED)/Build/Products/Debug-iphoneos/CocoDisplay.app
DEVELOPMENT_TEAM ?=
MAC_SIGNING = $(if $(DEVELOPMENT_TEAM),CODE_SIGNING_ALLOWED=YES CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=$(DEVELOPMENT_TEAM) -allowProvisioningUpdates,CODE_SIGNING_ALLOWED=NO)

display-build-mac: display-gen ## Build Coco Display for this Mac ("Designed for iPad"; unsigned without DEVELOPMENT_TEAM)
	cd display && xcodebuild -project CocoDisplay.xcodeproj -scheme CocoDisplay -destination '$(MAC_DEST)' -clonedSourcePackagesDirPath .spm -derivedDataPath $(MAC_DERIVED) build -quiet $(MAC_SIGNING)

display-run-mac: ## Build, sign and open Coco Display on this Mac (needs DEVELOPMENT_TEAM=<team id>)
	@test -n "$(DEVELOPMENT_TEAM)" || { echo "Set DEVELOPMENT_TEAM=<your Apple team ID>: macOS only runs signed iPad apps (docs/kitchen-setup.md)" >&2; exit 1; }
	$(MAKE) display-build-mac DEVELOPMENT_TEAM=$(DEVELOPMENT_TEAM)
	open "$(MAC_APP)"

display-test: kit-test display-build ## Test CocoKit and build the app

test: hub-test display-test ## Everything CI runs (tests)

lint: hub-lint ## Everything CI runs (lint)

# CI and supply-chain guards (.github/CLAUDE.md): the required `ci-gate` context still exists and
# needs every job; every action is SHA-pinned; plus the unit tests of those guards and asc.py.
repo-check: ## Check CI wiring (ci-gate, action pins) and run the CI/TestFlight script tests
	python3 scripts/check-required-contexts.py
	python3 scripts/check-action-pins.py
	@grep -q 'xcode: "$(XCODE_PRIMARY)"' .github/workflows/ci.yml || { echo "ci.yml has no display leg on XCODE_PRIMARY $(XCODE_PRIMARY): CI would not build what TestFlight ships" >&2; exit 1; }
	cd hub && uv run --locked ruff check --config pyproject.toml ../scripts ../display/tools && uv run --locked ruff format --config pyproject.toml --check ../scripts ../display/tools
	python3 -m unittest discover -s scripts -p 'test_*.py'
	uv run --quiet --project hub --locked python -m unittest discover -s display/tools -p 'test_*.py'

check-workflows: ## Lint .github/workflows with actionlint (skipped if it is not installed)
	@if command -v actionlint >/dev/null 2>&1; then actionlint && echo "actionlint OK"; \
	else echo "actionlint not installed: skipping (CI runs it)"; fi

# Xcode on GitHub's macOS runners is pinned, never the image default, so an image update cannot
# change the compiler under us. XCODE_PRIMARY builds what ships (CI's display-build and the
# TestFlight workflow); keep it equal to the primary leg in .github/workflows/ci.yml.
XCODE_PRIMARY := 26.6
XCODE_VERSION ?= $(XCODE_PRIMARY)
ci-xcode: ## (CI) Select Xcode XCODE_VERSION= (default XCODE_PRIMARY) via DEVELOPER_DIR; fail if this runner lacks it
	@dir=/Applications/Xcode_$(XCODE_VERSION).app/Contents/Developer; \
	if [ ! -d "$$dir" ]; then \
		echo "::error::Xcode $(XCODE_VERSION) is not installed on this runner ($$dir). Installed:" >&2; \
		ls -d /Applications/Xcode* >&2; exit 1; \
	fi; \
	echo "DEVELOPER_DIR=$$dir" >> "$${GITHUB_ENV:-/dev/null}"; \
	DEVELOPER_DIR=$$dir xcodebuild -version
