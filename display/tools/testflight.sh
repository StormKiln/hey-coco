#!/bin/sh
# TestFlight helpers for the Makefile (#4). Run from the repo root.
#
#   testflight.sh check [archive|upload]   validate config, list everything missing, exit 1 if any
#   testflight.sh team                     print the team ID (env DEVELOPMENT_TEAM, else local xcconfig)
#   testflight.sh bundle-id                print the bundle ID (env BUNDLE_ID, local xcconfig, default)
#   testflight.sh marketing-version        print MARKETING_VERSION from display/project.yml
#   testflight.sh export-options <out>     write ExportOptions.plist for an App Store Connect upload
#
# Config comes from display/Signing.local.xcconfig (DEVELOPMENT_TEAM, optional
# PRODUCT_BUNDLE_IDENTIFIER) and, for the upload, env ASC_KEY_ID, ASC_ISSUER_ID, ASC_KEY_PATH.
# See docs/testflight.md.
set -eu

LOCAL=display/Signing.local.xcconfig
DEFAULT_BUNDLE_ID=com.stormkiln.heycoco.display

# Value of KEY in the local xcconfig (last uncommented assignment), or empty.
xcconfig_value() {
    [ -f "$LOCAL" ] || return 0
    sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*([^[:space:]/]*).*/\\1/p" "$LOCAL" | tail -n 1
}

team() {
    if [ -n "${DEVELOPMENT_TEAM:-}" ]; then echo "$DEVELOPMENT_TEAM"; else xcconfig_value DEVELOPMENT_TEAM; fi
}

check() {
    mode=${1:-upload}
    missing=""
    add() { missing="$missing
  - $1"; }

    t=$(team)
    if [ ! -f "$LOCAL" ] && [ -z "${DEVELOPMENT_TEAM:-}" ]; then
        add "display/Signing.local.xcconfig (cp display/Signing.local.xcconfig.example display/Signing.local.xcconfig)"
    fi
    if [ -z "$t" ]; then
        add "DEVELOPMENT_TEAM: your 10-character Apple team ID, in display/Signing.local.xcconfig"
    elif [ "$t" = "ABCDE12345" ]; then
        add "DEVELOPMENT_TEAM: still the example value ABCDE12345 in display/Signing.local.xcconfig"
    elif ! echo "$t" | grep -Eq '^[A-Z0-9]{10}$'; then
        add "DEVELOPMENT_TEAM: '$t' is not a 10-character team ID (A-Z, 0-9)"
    fi

    if [ "$mode" = upload ]; then
        [ -n "${ASC_KEY_ID:-}" ] || add "ASC_KEY_ID: App Store Connect API key ID (env)"
        [ -n "${ASC_ISSUER_ID:-}" ] || add "ASC_ISSUER_ID: App Store Connect API issuer ID (env)"
        if [ -z "${ASC_KEY_PATH:-}" ]; then
            add "ASC_KEY_PATH: path to the AuthKey_<KEY_ID>.p8 file (env)"
        elif [ ! -f "$ASC_KEY_PATH" ]; then
            add "ASC_KEY_PATH: no file at $ASC_KEY_PATH"
        fi
    fi

    command -v xcodegen >/dev/null 2>&1 || add "xcodegen (brew install xcodegen)"

    echo "Bundle ID:    $(bundle_id)"
    echo "Version:      $(marketing_version)"
    echo "Team:         ${t:-<missing>}"
    echo "Build number: ${BUILD_NUMBER:-<from git>}"

    for f in PicovoiceAccessKey.txt hey-coco_ios.ppn; do
        if [ ! -s "display/WakeWord/$f" ]; then
            echo "warning: display/WakeWord/$f is missing: the archive will ship without the wake word (docs/wake-word.md)" >&2
        fi
    done

    if [ -n "$missing" ]; then
        echo "Not ready for TestFlight. Missing:$missing" >&2
        echo "See docs/testflight.md." >&2
        exit 1
    fi
    echo "TestFlight config OK ($mode)."
}

export_options() {
    out=$1
    t=$(team)
    [ -n "$t" ] || { echo "No DEVELOPMENT_TEAM" >&2; exit 1; }
    mkdir -p "$(dirname "$out")"
    cat > "$out" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>method</key>
	<string>app-store-connect</string>
	<key>destination</key>
	<string>upload</string>
	<key>teamID</key>
	<string>$t</string>
	<key>signingStyle</key>
	<string>automatic</string>
	<key>uploadSymbols</key>
	<true/>
	<key>manageAppVersionAndBuildNumber</key>
	<false/>
	<key>testFlightInternalTestingOnly</key>
	<false/>
</dict>
</plist>
EOF
    plutil -lint "$out" >/dev/null
}

# Bundle ID: env BUNDLE_ID (the workflows' repository variable), else the local xcconfig, else
# the committed default.
bundle_id() {
    if [ -n "${BUNDLE_ID:-}" ]; then echo "$BUNDLE_ID"; return; fi
    b=$(xcconfig_value PRODUCT_BUNDLE_IDENTIFIER)
    echo "${b:-$DEFAULT_BUNDLE_ID}"
}

# MARKETING_VERSION from display/project.yml (the version testers see).
marketing_version() {
    v=$(sed -nE 's/^[[:space:]]*MARKETING_VERSION:[[:space:]]*"?([0-9.]+)"?.*/\1/p' display/project.yml | head -n 1)
    [ -n "$v" ] || { echo "No MARKETING_VERSION in display/project.yml" >&2; exit 1; }
    echo "$v"
}

case "${1:-}" in
    check) check "${2:-upload}" ;;
    team) team ;;
    bundle-id) bundle_id ;;
    marketing-version) marketing_version ;;
    export-options) export_options "${2:?output path}" ;;
    *) echo "usage: $0 check [archive|upload] | team | bundle-id | marketing-version | export-options <out>" >&2; exit 2 ;;
esac
