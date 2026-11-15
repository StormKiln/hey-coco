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
///
/// The session work behind `activateStandby()` / `beginConversation()` may block for a long time
/// (or, seen in the simulator, forever), so it never runs on the main actor: callers await it,
/// with a timeout where waiting matters. `deactivate()` returns at once.
@MainActor
public protocol VoiceAudioSession: AnyObject {
    var onEvent: (@MainActor (AudioSessionEvent) -> Void)? { get set }
    /// Play-and-record with echo cancellation, mixing with (not ducking) other audio.
    /// Idempotent: while an activation is in flight, joins it rather than starting another.
    func activateStandby() async throws
    /// Like standby, but ducks other audio. Called with the mic engines stopped.
    func beginConversation() async throws
    /// Plays the listening chime and returns when it has finished (so the mic doesn't hear it).
    func playListeningChime() async
    /// Releases the session so other audio un-ducks. Called with the mic engines stopped. Does
    /// not wait for the session work.
    func deactivate()
}

/// `VoiceAudioSession` that does nothing (previews).
@MainActor
public final class SilentVoiceAudioSession: VoiceAudioSession {
    public var onEvent: (@MainActor (AudioSessionEvent) -> Void)?
    public init() {}
    public func activateStandby() async throws {}
    public func beginConversation() async throws {}
    public func playListeningChime() async {}
    public func deactivate() {}
}
