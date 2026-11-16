/// Which audio route changes restart the standby listener (#108).
///
/// Only hardware coming or going (headphones, a dock): the mic engine must then be rebuilt for
/// the new input. Everything else is ignored. In particular `routeConfigurationChange`, which iOS
/// posts whenever a mic engine with voice processing starts: restarting the listener on it starts
/// another such engine, and the wake word flapped armed ↔ paused without ever listening.
/// `PCMFrameMicrophone` restarts itself on `AVAudioEngineConfigurationChange` anyway.
///
/// Takes `AVAudioSession.RouteChangeReason.rawValue`, which is iOS-only, so it tests on macOS.
public enum AudioRouteChangePolicy {
    /// `newDeviceAvailable` (1) and `oldDeviceUnavailable` (2).
    static let hardwareReasons: Set<UInt> = [1, 2]

    public static func restartsListener(reasonRawValue: UInt?) -> Bool {
        reasonRawValue.map(hardwareReasons.contains) ?? false
    }
}
