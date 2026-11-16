import Foundation
import Testing
@testable import CocoKit

@Suite struct DebugCommandParsingTests {
    @Test(arguments: [
        ("say: set a pasta timer for 10 minutes", DebugCommand.say("set a pasta timer for 10 minutes")),
        ("say:what's the weather", .say("what's the weather")),
        ("pair FE3D-45AB-301C-3A25-BF52-AD89", .pair(fingerprint: "FE3D-45AB-301C-3A25-BF52-AD89")),
        ("timer 90", .touchTimer(seconds: 90, name: nil)),
        ("timer 300 eggs", .touchTimer(seconds: 300, name: "eggs")),
        ("stop-alarm", .stopAlarm),
        ("show idle", .show(.idle)),
        ("show timers", .show(.timers)),
        ("night sleep", .night(.sleep)),
        ("night wake", .night(.wake)),
        ("forget", .forgetHub),
        ("pair-code FE3D-45AB-301C-3A25-BF52-AD89 482913",
         .pairCode(fingerprint: "FE3D-45AB-301C-3A25-BF52-AD89", code: "482913")),
        ("pair-uri cocohub://pair?v=1&host=h&port=1&token=t&cert=ab",
         .pairURI("cocohub://pair?v=1&host=h&port=1&token=t&cert=ab")),
    ])
    func parses(_ line: String, _ expected: DebugCommand) {
        #expect(DebugCommand.parse(line) == expected)
    }

    @Test(arguments: ["", "   ", "# comment", "say:", "timer", "timer -5", "timer abc", "show nope", "dance", "pair-code 482913", "pair-uri"])
    func rejects(_ line: String) {
        #expect(DebugCommand.parse(line) == nil)
    }

    @Test func parsesAFileOfLinesSkippingJunk() {
        let text = "say: hello\n\n# note\ntimer 60\nbogus\nstop-alarm\n"
        #expect(DebugCommand.parseAll(text) == [.say("hello"), .touchTimer(seconds: 60, name: nil), .stopAlarm])
    }
}

@Suite struct DebugPairingGuardTests {
    @Test func pairsOnlyWhenTheFingerprintMatchesTheCandidate() {
        #expect(DebugCommand.fingerprintMatches(expected: "ab12-CD34", candidate: "AB12-CD34"))
        #expect(!DebugCommand.fingerprintMatches(expected: "AB12-CD35", candidate: "AB12-CD34"))
        #expect(!DebugCommand.fingerprintMatches(expected: "", candidate: "AB12-CD34"))
    }
}

@Suite struct DebugLaunchSettingsTests {
    @Test func readsHostPortAndTokenFromArguments() {
        let args = ["CocoDisplay", "-CocoDebugHost", "127.0.0.1", "-CocoDebugPort", "18765",
                    "-CocoDebugToken", "tok-0123456789abcdef0123"]
        let launch = DebugLaunchSettings(arguments: args)
        #expect(launch == DebugLaunchSettings(host: "127.0.0.1", port: 18765, token: "tok-0123456789abcdef0123"))
    }

    @Test func absentWithoutAHost() {
        #expect(DebugLaunchSettings(arguments: ["CocoDisplay", "-CocoDebugToken", "x"]) == nil)
    }

    @Test func appliedSettingsKeepAnExistingPin() {
        let pin = HubPin(hubID: "6e79ebc4d730aeb4", certSHA256: Data(repeating: 1, count: 32))
        let current = HubSettings(manualHost: nil, token: nil, pin: pin)
        let launch = DebugLaunchSettings(host: "127.0.0.1", port: 18765, token: "t")!
        let applied = launch.applied(to: current)
        #expect(applied.manualHost == "127.0.0.1")
        #expect(applied.port == 18765)
        #expect(applied.token == "t")
        #expect(applied.pin == pin)
    }
}
