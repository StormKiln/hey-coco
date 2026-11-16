import Testing
@testable import CocoKit

/// #108: which `AVAudioSession.RouteChangeReason`s restart the standby listener. Raw values, so
/// this runs on macOS (no `AVAudioSession` there).
@Suite struct AudioRouteChangePolicyTests {
    @Test func hardwareChangesRestartTheListener() {
        #expect(AudioRouteChangePolicy.restartsListener(reasonRawValue: 1))  // newDeviceAvailable
        #expect(AudioRouteChangePolicy.restartsListener(reasonRawValue: 2))  // oldDeviceUnavailable
    }

    /// Starting a mic engine with voice processing posts `routeConfigurationChange`; restarting
    /// the listener on it starts the engine again, so the wake word flapped armed ↔ paused forever.
    @Test func ourOwnConfigurationChangesDoNot() {
        #expect(!AudioRouteChangePolicy.restartsListener(reasonRawValue: 8))  // routeConfigurationChange
        #expect(!AudioRouteChangePolicy.restartsListener(reasonRawValue: 3))  // categoryChange
        #expect(!AudioRouteChangePolicy.restartsListener(reasonRawValue: 4))  // override
        #expect(!AudioRouteChangePolicy.restartsListener(reasonRawValue: 0))  // unknown
        #expect(!AudioRouteChangePolicy.restartsListener(reasonRawValue: nil))
    }
}
