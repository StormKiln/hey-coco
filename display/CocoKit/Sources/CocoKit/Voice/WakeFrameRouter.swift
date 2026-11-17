import Foundation

/// Where the wake-word mic's frames go (#112): to the wake-word detector, or, from the moment it
/// fires, into a capture that the speech recognizer takes over. So nothing said straight after
/// "hey coco" (while the session switches and the chime plays) is lost, and the conversation
/// never opens a second mic.
///
/// ```
/// detecting ──detection──▶ buffering ──attach──▶ streaming ──detach──▶ held
///     ▲                        │ (limit reached)                          │
///     └────────────────────────┴──────────── resumeDetection() ───────────┘
/// ```
///
/// `process` runs on the mic's serial queue; the rest on the main actor. Everything happens under
/// one lock, so captured frames reach the sink in order, buffered ones first.
public final class WakeFrameRouter: @unchecked Sendable {
    public let sampleRate: Double
    /// An unclaimed capture is dropped (and detection resumes) after this many frames.
    public let maxBufferedFrames: Int

    private enum Mode {
        case detecting
        case buffering([[Int16]])
        case streaming(@Sendable ([Int16]) -> Void)
        /// The recognizer is done with the audio; waiting for `resumeDetection()`.
        case held
    }

    private let lock = NSLock()
    private let detect: @Sendable ([Int16]) -> Bool
    private let resetDetector: @Sendable () -> Void
    private var mode: Mode = .detecting
    /// Bumped per capture, so a stale one can't attach to (or detach) a later one.
    private var generation = 0

    public init(
        sampleRate: Double, maxBufferedFrames: Int,
        detect: @escaping @Sendable ([Int16]) -> Bool,
        resetDetector: @escaping @Sendable () -> Void
    ) {
        self.sampleRate = sampleRate
        self.maxBufferedFrames = maxBufferedFrames
        self.detect = detect
        self.resetDetector = resetDetector
    }

    /// One mic frame. True if it completed a wake-word detection: the frames after it are
    /// captured until claimed (the detection frame itself is not).
    @discardableResult
    public func process(_ frame: [Int16]) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        switch mode {
        case .detecting:
            return detectWakeWord(in: frame)
        case .buffering(var frames):
            guard frames.count < maxBufferedFrames else {
                // Nobody came for it (the turn never started): listen for the wake word again.
                listenAgain()
                return detectWakeWord(in: frame)
            }
            frames.append(frame)
            mode = .buffering(frames)
        case .streaming(let sink):
            sink(frame)
        case .held:
            break
        }
        return false
    }

    /// Starts a capture as if the last frame had been a detection (fakes, tests).
    public func triggerDetection() {
        lock.lock()
        defer { lock.unlock() }
        startCapture()
    }

    /// The audio since the last detection, if it is still being captured and not yet attached.
    public func claimCapture() -> (any SpeechAudioFeed)? {
        lock.lock()
        defer { lock.unlock() }
        guard case .buffering = mode else { return nil }
        return WakeCapture(router: self, generation: generation)
    }

    /// Ends any capture and listens for the wake word again, fresh (the detector is reset).
    public func resumeDetection() {
        lock.lock()
        defer { lock.unlock() }
        listenAgain()
    }

    // MARK: - Capture (called by `WakeCapture`)

    fileprivate func attach(_ sink: @escaping @Sendable ([Int16]) -> Void, generation: Int) {
        lock.lock()
        defer { lock.unlock() }
        guard generation == self.generation, case .buffering(let frames) = mode else { return }
        for frame in frames { sink(frame) }
        mode = .streaming(sink)
    }

    fileprivate func detach(generation: Int) {
        lock.lock()
        defer { lock.unlock() }
        guard generation == self.generation else { return }
        switch mode {
        case .buffering, .streaming: mode = .held
        case .detecting, .held: break
        }
    }

    // MARK: - Private (lock held)

    private func startCapture() {
        generation += 1
        mode = .buffering([])
    }

    private func listenAgain() {
        generation += 1
        resetDetector()
        mode = .detecting
    }

    private func detectWakeWord(in frame: [Int16]) -> Bool {
        guard detect(frame) else { return false }
        startCapture()
        return true
    }
}

/// One capture of a `WakeFrameRouter`, handed to the speech recognizer.
private final class WakeCapture: SpeechAudioFeed {
    let router: WakeFrameRouter
    let generation: Int

    init(router: WakeFrameRouter, generation: Int) {
        self.router = router
        self.generation = generation
    }

    var sampleRate: Double { router.sampleRate }

    func attach(_ sink: @escaping @Sendable ([Int16]) -> Void) {
        router.attach(sink, generation: generation)
    }

    func detach() { router.detach(generation: generation) }
}
