import Foundation

/// Something the system did to the audio session that the listening loop must react to.
public enum AudioSessionEvent: Sendable, Equatable {
    /// Siri, a call, an alarm from another app: the mic is gone until `interruptionEnded`.
    case interruptionBegan
    case interruptionEnded
    /// Headphones / AirPlay / speaker changed; running audio engines need restarting.
    case routeChanged
    /// The media server restarted; everything audio must be rebuilt.
    case mediaServicesReset
}

/// The audio-session side of listening, owned by `VoiceLoop`. `AudioSessionCoordinator` is the
/// real one (iOS `AVAudioSession`; no-ops on macOS).
///
/// Modes: inactive; standby (mic available for the wake / stop word, other audio untouched);
/// conversation (other audio ducked while Coco listens and talks).
@MainActor
public protocol VoiceAudioSession: AnyObject {
    var onEvent: (@MainActor (AudioSessionEvent) -> Void)? { get set }
    /// Play-and-record with echo cancellation, mixing with (not ducking) other audio.
    /// Idempotent.
    func activateStandby() throws
    /// Like standby, but ducks other audio. Called with the mic engines stopped.
    func beginConversation() throws
    /// Plays the listening chime and returns when it has finished (so the mic doesn't hear it).
    func playListeningChime() async
    /// Releases the session so other audio un-ducks. Called with the mic engines stopped.
    func deactivate()
}

/// `VoiceAudioSession` that does nothing (previews).
@MainActor
public final class SilentVoiceAudioSession: VoiceAudioSession {
    public var onEvent: (@MainActor (AudioSessionEvent) -> Void)?
    public init() {}
    public func activateStandby() throws {}
    public func beginConversation() throws {}
    public func playListeningChime() async {}
    public func deactivate() {}
}
