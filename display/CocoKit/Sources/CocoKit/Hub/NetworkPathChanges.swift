import Foundation
import Network

/// Network path changes (Wi-Fi rejoined, a new interface), for `AppModel.networkChanged()`:
/// after one the Hub may answer at a different address.
public enum NetworkPathChanges {
    /// Yields each time the path changes to usable (`satisfied`), not for the initial path.
    /// Monitoring stops when the consumer stops iterating.
    public static func updates() -> AsyncStream<Void> {
        AsyncStream { continuation in
            let monitor = NWPathMonitor()
            let state = PathState()
            monitor.pathUpdateHandler = { path in
                if state.shouldReport(path) { continuation.yield() }
            }
            continuation.onTermination = { _ in monitor.cancel() }
            monitor.start(queue: DispatchQueue(label: "coco.network-path"))
        }
    }

    /// Only touched on the monitor's serial queue.
    private final class PathState: @unchecked Sendable {
        private var previous: NWPath?

        func shouldReport(_ path: NWPath) -> Bool {
            defer { previous = path }
            guard let previous else { return false }
            return path.status == .satisfied && path != previous
        }
    }
}
