import Foundation

/// Listens for a bare "stop" (no wake word) while an alarm rings (#23). `VoiceLoop` only runs it
/// while an alarm is ringing, so "stop" is never acted on at any other time.
///
/// A dedicated "stop" wake-word model would need its own training (and a one-syllable word
/// false-triggers easily), so the real listener is short speech-recognition windows
/// (`RecognizerStopWordListener`).
@MainActor
public protocol StopWordListener: AnyObject {
    var isRunning: Bool { get }
    /// Starts listening; `onStop` fires (on the main actor) when a stop phrase is heard, after
    /// which the listener has stopped itself.
    func start(onStop: @escaping @MainActor @Sendable () -> Void) throws
    func stop()
}

/// Exact-phrase matching for alarm dismissal: the whole utterance must be one of the stop
/// phrases (optionally after "hey coco" / "coco", so "hey coco, stop" works too), so "don't stop"
/// or "stop the music" never dismiss.
public enum StopWordMatcher {
    public static let phrases: Set<String> = [
        "stop", "stop it", "ok", "okay", "enough", "dismiss", "shut up", "off",
    ]
    static let wakePrefixes: [[String]] = [["hey", "coco"], ["coco"]]

    public static func matches(_ transcript: String) -> Bool {
        var words = transcript.lowercased()
            .components(separatedBy: CharacterSet.letters.inverted)
            .filter { !$0.isEmpty }
        for prefix in wakePrefixes where words.starts(with: prefix) {
            words.removeFirst(prefix.count)
            break
        }
        guard !words.isEmpty else { return false }
        if phrases.contains(words.joined(separator: " ")) { return true }
        // "stop stop", "stop, stop it": every piece is itself a stop phrase.
        return split(words)
    }

    /// True if `words` is a sequence of whole stop phrases.
    private static func split(_ words: [String]) -> Bool {
        if words.isEmpty { return true }
        for length in (1...min(2, words.count)).reversed()
        where phrases.contains(words.prefix(length).joined(separator: " ")) {
            if split(Array(words.dropFirst(length))) { return true }
        }
        return false
    }
}

/// `StopWordListener` over a `SpeechRecognizer`: back-to-back short recognition windows (the
/// recognizer's endpointer closes each after silence or a few seconds), checking every partial so
/// the alarm goes quiet the moment "stop" is heard.
@MainActor
public final class RecognizerStopWordListener: StopWordListener {
    public private(set) var isRunning = false

    private let recognizer: any SpeechRecognizer
    private let retryDelay: Duration
    private var loop: Task<Void, Never>?
    private var generation = 0

    public init(recognizer: any SpeechRecognizer, retryDelay: Duration = .seconds(1)) {
        self.recognizer = recognizer
        self.retryDelay = retryDelay
    }

    public func start(onStop: @escaping @MainActor @Sendable () -> Void) throws {
        stop()
        isRunning = true
        generation += 1
        let generation = generation
        loop = Task { [weak self] in await self?.listen(generation, onStop: onStop) }
    }

    public func stop() {
        guard isRunning || loop != nil else { return }
        isRunning = false
        generation += 1
        loop?.cancel()
        loop = nil
        recognizer.cancel()
    }

    private func isCurrent(_ generation: Int) -> Bool {
        generation == self.generation && !Task.isCancelled
    }

    private func listen(_ generation: Int, onStop: @MainActor @Sendable () -> Void) async {
        guard await recognizer.requestPermission() == .granted else {
            if isCurrent(generation) { isRunning = false }
            return
        }
        while isCurrent(generation) {
            do {
                let stream = try recognizer.start()
                for try await update in stream {
                    guard isCurrent(generation) else { return }
                    if StopWordMatcher.matches(update.text) {
                        stop()
                        onStop()
                        return
                    }
                    if update.isFinal { break }
                }
            } catch {
                // Recognizer busy or failed: try again shortly, while the alarm still rings.
                guard isCurrent(generation) else { return }
                try? await Task.sleep(for: retryDelay)
            }
        }
    }
}
