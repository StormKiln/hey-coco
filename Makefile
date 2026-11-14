.DEFAULT_GOAL := help
SIM_DEST ?= generic/platform=iOS Simulator

.PHONY: help hub-sync hub-run hub-test hub-lint hub-fmt display-gen display-build display-test kit-test test lint

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

hub-sync: ## Install Hub dependencies
	cd hub && uv sync

hub-run: hub-sync ## Run the Hub locally with reload
	cd hub && uv run uvicorn coco_hub.app:app --host 0.0.0.0 --port 8765 --reload

hub-test: hub-sync ## Run Hub tests
	cd hub && uv run pytest -q

hub-lint: hub-sync ## Lint and format-check the Hub
	cd hub && uv run ruff check . && uv run ruff format --check .

hub-fmt: hub-sync ## Auto-format the Hub
	cd hub && uv run ruff format . && uv run ruff check --fix .

display-gen: ## Generate the Xcode project from display/project.yml
	cd display && xcodegen generate -q

kit-test: ## Run CocoKit (Display logic) tests on macOS
	cd display/CocoKit && swift test

display-build: display-gen ## Build Coco Display for the iOS simulator
	cd display && xcodebuild -project CocoDisplay.xcodeproj -scheme CocoDisplay -destination '$(SIM_DEST)' build -quiet

display-test: kit-test display-build ## Test CocoKit and build the app

test: hub-test display-test ## Everything CI runs (tests)

lint: hub-lint ## Everything CI runs (lint)
