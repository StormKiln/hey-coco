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

.PHONY: help hub-sync hub-run hub-test hub-lint hub-eval hub-fmt hub-render-plist hub-install-service hub-uninstall-service hub-logs hub-logs-launchd hub-diag spotify-login sounds-build display-gen display-build display-test kit-test test lint

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

hub-sync: ## Install Hub dependencies
	cd hub && uv sync

hub-run: hub-sync ## Run the Hub locally with reload (hub/config.local.toml + COCO_* env)
	cd hub && uv run python -m coco_hub --reload

hub-test: hub-sync ## Run Hub tests
	cd hub && uv run pytest -q

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
	@echo "  open http://127.0.0.1:$(HUB_PORT)/spotify/login"
	@echo "See docs/spotify.md for the one-time Spotify developer app setup."

hub-logs: ## Follow the Hub's JSON log (hub.jsonl), pretty-printed
	@mkdir -p "$(HUB_LOG_DIR)"
	@tail -n 100 -F "$(HUB_LOG_DIR)/hub.jsonl" | python3 -u hub/src/coco_hub/logfmt.py

hub-logs-launchd: ## Follow launchd's raw stdout/stderr capture (startup crashes land here)
	@mkdir -p "$(HUB_LOG_DIR)"
	tail -n 100 -F "$(HUB_LOG_DIR)/hub.out.log" "$(HUB_LOG_DIR)/hub.err.log"

# The token goes to curl on stdin (-H @-), never on a command line or the terminal.
hub-diag: ## Show GET /v1/diagnostics from the local Hub (token from hub/config.local.toml / env)
	@cd hub && uv run --quiet python -c 'from coco_hub.settings import load_settings; print("Authorization: Bearer " + load_settings().token.get_secret_value())' \
		| curl -fsS -H @- "http://127.0.0.1:$(HUB_PORT)/v1/diagnostics" | python3 -m json.tool

sounds-build: ## Rebuild the sound library from assets/sounds/sources.toml (needs ffmpeg, sips; network)
	uv run --with httpx python assets/sounds/tools/curate.py build $(ARGS)

display-gen: ## Generate the Xcode project from display/project.yml
	cd display && xcodegen generate -q

kit-test: ## Run CocoKit (Display logic) tests on macOS
	cd display/CocoKit && swift test

display-build: display-gen ## Build Coco Display for the iOS simulator
	cd display && xcodebuild -project CocoDisplay.xcodeproj -scheme CocoDisplay -destination '$(SIM_DEST)' -clonedSourcePackagesDirPath .spm build -quiet

display-test: kit-test display-build ## Test CocoKit and build the app

test: hub-test display-test ## Everything CI runs (tests)

lint: hub-lint ## Everything CI runs (lint)
