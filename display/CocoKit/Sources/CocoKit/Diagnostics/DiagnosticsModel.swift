import Foundation
import Observation

/// Text for the hidden diagnostics screen (#45). Never formats the Hub token.
public enum DiagnosticsFormat {
    /// "42 s", "5 m", "12 h 5 m", "3 d 4 h".
    public static func uptime(seconds: Int) -> String {
        let s = max(0, seconds)
        let days = s / 86_400
        let hours = s % 86_400 / 3600
        let minutes = s % 3600 / 60
        if days > 0 { return "\(days) d \(hours) h" }
        if hours > 0 { return "\(hours) h \(minutes) m" }
        if minutes > 0 { return "\(minutes) m" }
        return "\(s) s"
    }

    /// "just now", "9 s ago", "45 min ago", "2 h ago", "2 d ago". Future times are "just now".
    public static func relative(_ date: Date, now: Date) -> String {
        let s = Int(now.timeIntervalSince(date))
        if s < 5 { return "just now" }
        if s < 60 { return "\(s) s ago" }
        if s < 3600 { return "\(s / 60) min ago" }
        if s < 86_400 { return "\(s / 3600) h ago" }
        return "\(s / 86_400) d ago"
    }

    /// "312 ms", "1.8 s".
    public static func latency(ms: Int) -> String {
        guard ms >= 1000 else { return "\(ms) ms" }
        return String(format: "%.1f s", Double(ms) / 1000)
    }

    public static func connection(_ state: ConnectionState) -> String {
        switch state {
        case .searching: "Searching (Bonjour)"
        case .connecting: "Connecting"
        case .connected: "Connected"
        case .unreachable(let reason): "Unreachable: \(reason)"
        case .unauthorized: "Token rejected"
        case .needsPairing(let candidate):
            "Waiting to pair with \(candidate.address.host) (\(candidate.pin.fingerprint))"
        case .untrustedHub(let host): "Unknown Coco Hub at \(host) (not the paired Hub)"
        }
    }

    public static func error(_ error: HubError) -> String {
        switch error {
        case .unauthorized: "Unauthorized (check the token)"
        case .unreachable(let reason): reason
        case .server(let code, let message): "\(code): \(message)"
        case .decoding(let detail): "Unexpected reply: \(detail)"
        case .untrustedHub(let host): "Not the paired Coco Hub: \(host)"
        }
    }

    @MainActor
    public static func wakeWord(_ status: VoiceLoop.WakeWordStatus?) -> String {
        switch status {
        case nil: "Not started"
        case .armed?: "Armed (listening for “hey coco”)"
        case .paused?: "Paused"
        case .unavailable(let reason)?: "Unavailable: \(reason)"
        case .retrying(let after)?: "Retrying in \(after.components.seconds) s"
        }
    }

    /// "host:port"; the token is deliberately left out.
    public static func address(_ endpoint: HubEndpoint?) -> String {
        guard let endpoint else { return "Not found yet" }
        return "\(endpoint.host):\(endpoint.port)"
    }
}

/// One row of the Hub health table.
public struct DiagnosticsHealthRow: Sendable, Equatable, Identifiable {
    public var name: String
    public var status: CheckStatus
    public var summary: String
    public var id: String { name }
}

/// One recent utterance, formatted.
public struct DiagnosticsUtteranceRow: Sendable, Equatable, Identifiable {
    public var id: Int
    public var when: String
    public var text: String
    public var source: String
    public var intent: String
    public var matchedBy: String
    public var latency: String
    public var error: String?
}

/// Loads `GET /v1/diagnostics` and formats it for the diagnostics screen.
@MainActor
@Observable
public final class DiagnosticsModel {
    public enum State: Sendable, Equatable {
        case idle, loading, loaded
        case failed(String)
    }

    public private(set) var state: State = .idle
    /// The last good reply; kept when a refresh fails.
    public private(set) var diagnostics: HubDiagnostics?

    public init() {}

    /// `api` is `nil` while no Hub is connected.
    public func load(using api: (any HubAPI)?) async {
        guard let api else {
            state = .failed("Not connected to Coco Hub.")
            return
        }
        state = .loading
        do throws(HubError) {
            diagnostics = try await api.diagnostics()
            state = .loaded
        } catch {
            state = .failed(DiagnosticsFormat.error(error))
        }
    }

    public var uptime: String? {
        diagnostics.map { DiagnosticsFormat.uptime(seconds: $0.uptimeS) }
    }

    /// "0.1.0 (3e190cd)".
    public var versionLine: String? {
        diagnostics.map { d in d.gitSha.map { "\(d.version) (\($0))" } ?? d.version }
    }

    public var healthRows: [DiagnosticsHealthRow] {
        guard let d = diagnostics else { return [] }
        let h = d.health
        let now = d.serverTime

        var ollama: [String] = []
        if let detail = h.ollama.detail { ollama.append(detail) }
        if h.ollama.status != .off {
            ollama.append(h.ollama.reachable ? "reachable" : "unreachable")
            if h.ollama.reachable {
                ollama.append(
                    h.ollama.modelLoaded ? "model loaded"
                        : h.ollama.modelPresent ? "model not loaded" : "model missing")
            }
        }

        var weather: [String] = []
        if let detail = h.weather.detail { weather.append(detail) }
        if h.weather.stale { weather.append("stale") }
        if let fetched = h.weather.fetchedAt {
            weather.append("fetched \(DiagnosticsFormat.relative(fetched, now: now))")
        }

        var youtube = ["quota \(h.youtube.quotaUsed) / \(h.youtube.quotaLimit)"]
        if let detail = h.youtube.detail { youtube.append(detail) }

        var spotify: [String] = []
        let device = h.spotify.deviceName ?? "Device"
        spotify.append(h.spotify.deviceVisible ? "\(device) visible" : "\(device) not visible")
        if let detail = h.spotify.detail { spotify.append(detail) }

        return [
            DiagnosticsHealthRow(name: "Ollama", status: h.ollama.status, summary: ollama.joined(separator: " · ")),
            DiagnosticsHealthRow(name: "Weather", status: h.weather.status, summary: weather.joined(separator: " · ")),
            DiagnosticsHealthRow(name: "YouTube", status: h.youtube.status, summary: youtube.joined(separator: " · ")),
            DiagnosticsHealthRow(name: "Spotify", status: h.spotify.status, summary: spotify.joined(separator: " · ")),
        ]
    }

    /// Newest first. Times are relative to the Hub's `server_time`, so the iPad's clock skew
    /// doesn't matter.
    public var utteranceRows: [DiagnosticsUtteranceRow] {
        guard let d = diagnostics else { return [] }
        return d.utterances.enumerated().map { index, u in
            DiagnosticsUtteranceRow(
                id: index,
                when: DiagnosticsFormat.relative(u.ts, now: d.serverTime),
                text: u.text,
                source: u.source.rawValue,
                intent: u.intent ?? "—",
                matchedBy: u.matchedBy?.rawValue ?? "—",
                latency: DiagnosticsFormat.latency(ms: u.latencyMs),
                error: u.error)
        }
    }
}

extension AppModel {
    /// The connected Hub client, for the diagnostics screen; `nil` until a Hub is found.
    public var hubClient: (any HubAPI)? { client }
}
