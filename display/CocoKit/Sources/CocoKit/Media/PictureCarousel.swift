import Foundation
import Observation

/// Calls `onTimeout` once `timeout` passes after `arm()` with no `touch()` in between.
/// Used by the Picture screen (idle after a minute of no swiping) and the Sound screen (idle a
/// little after the clip ends).
@MainActor
public final class IdleCountdown {
    public let timeout: Duration
    public private(set) var isArmed = false

    private let sleep: @Sendable (Duration) async throws -> Void
    private let onTimeout: @MainActor () -> Void
    private var task: Task<Void, Never>?

    public init(
        timeout: Duration,
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
        onTimeout: @escaping @MainActor () -> Void
    ) {
        self.timeout = timeout
        self.sleep = sleep
        self.onTimeout = onTimeout
    }

    /// Starts (or restarts) the countdown.
    public func arm() {
        task?.cancel()
        isArmed = true
        task = Task { [weak self, sleep, timeout] in
            do { try await sleep(timeout) } catch { return }
            guard let self, !Task.isCancelled else { return }
            self.isArmed = false
            self.task = nil
            self.onTimeout()
        }
    }

    /// Interaction: restarts the countdown if it is running.
    public func touch() {
        if isArmed { arm() }
    }

    public func disarm() {
        task?.cancel()
        task = nil
        isArmed = false
    }
}

/// The Picture screen's state: which of the view's images is showing, and when to go back to
/// idle. A new picture view (including the Hub's "next one", which resends the images rotated so
/// the next one is first) starts again at its first image; a swipe moves locally.
@MainActor
@Observable
public final class PictureCarousel {
    public private(set) var images: [PictureImage] = []
    public private(set) var index = 0
    /// Called after `idleAfter` with no swipe.
    @ObservationIgnored public var onIdle: (@MainActor () -> Void)?
    @ObservationIgnored private var countdown: IdleCountdown!

    public init(
        idleAfter: Duration = .seconds(60),
        sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        countdown = IdleCountdown(timeout: idleAfter, sleep: sleep) { [weak self] in self?.onIdle?() }
    }

    public var current: PictureImage? { images.indices.contains(index) ? images[index] : nil }
    public var canAdvance: Bool { images.count > 1 }

    /// Shows a new set from its first image and (re)starts the idle countdown.
    public func show(_ images: [PictureImage]) {
        self.images = images
        index = 0
        countdown.arm()
    }

    public func next() { move(by: 1) }
    public func previous() { move(by: -1) }

    /// The screen went away.
    public func stop() { countdown.disarm() }

    private func move(by step: Int) {
        guard canAdvance else { return }
        index = (index + step + images.count) % images.count
        countdown.touch()
    }
}

extension PictureImage {
    /// The smaller `thumb_url` when there is one, else the original.
    public var displayURL: String { thumbURL.isEmpty ? url : thumbURL }

    /// The title for a caption. Commons titles are file names ("File:Red_fox.jpg" → "Red fox").
    public var caption: String {
        var text = title
        if text.hasPrefix("File:") { text = String(text.dropFirst(5)) }
        if let dot = text.lastIndex(of: "."), text[dot...].count <= 5 { text = String(text[..<dot]) }
        return text.replacingOccurrences(of: "_", with: " ")
    }

    /// "Author · License", or just the license when the author is unknown.
    public var attributionLine: String {
        let author = author?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return author.isEmpty ? license : "\(author) · \(license)"
    }
}
