import Foundation

/// Commands that drive the app without touching the screen, for end-to-end runs in the simulator
/// (`docs/simulator-testing.md`). The app only reads them in DEBUG builds (see `DebugHarness` in
/// the app target); release and TestFlight builds compile none of the wiring.
///
/// One command per line in the inbox file:
///
///     say: set a pasta timer for 10 minutes   → sent to the Hub as if heard
///     pair FE3D-45AB-301C-3A25-BF52-AD89      → confirm pairing, only if this IS the fingerprint
///     pair-code FE3D-…-AD89 482913            → Enter code, only if this IS the fingerprint
///     pair-uri cocohub://pair?v=1&…           → as if this QR code was scanned
///     timer 300 eggs                          → a touch timer (seconds, optional name)
///     stop-alarm                              → as if the Stop button was tapped
///     show idle | show timers                 → navigate
///     night sleep | night wake                → night mode
///     forget                                  → Forget Hub (clears pin and token)
public enum DebugCommand: Equatable, Sendable {
    case say(String)
    case pair(fingerprint: String)
    case pairCode(fingerprint: String, code: String)
    case pairURI(String)
    case touchTimer(seconds: Int, name: String?)
    case stopAlarm
    case show(ViewType)
    case night(NightAction)
    case forgetHub

    public enum NightAction: String, Sendable { case sleep, wake }

    public static func parse(_ rawLine: String) -> DebugCommand? {
        let line = rawLine.trimmingCharacters(in: .whitespaces)
        if line.isEmpty || line.hasPrefix("#") { return nil }
        if line.lowercased().hasPrefix("say:") {
            let text = line.dropFirst(4).trimmingCharacters(in: .whitespaces)
            return text.isEmpty ? nil : .say(text)
        }
        let words = line.split(separator: " ").map(String.init)
        switch (words.first?.lowercased(), words.count) {
        case ("pair", 2):
            return .pair(fingerprint: words[1])
        case ("pair-code", 3):
            return .pairCode(fingerprint: words[1], code: words[2])
        case ("pair-uri", 2):
            return .pairURI(words[1])
        case ("timer", 2...):
            guard let seconds = Int(words[1]), seconds > 0 else { return nil }
            let name = words.count > 2 ? words.dropFirst(2).joined(separator: " ") : nil
            return .touchTimer(seconds: seconds, name: name)
        case ("stop-alarm", 1):
            return .stopAlarm
        case ("show", 2):
            return ViewType(rawValue: words[1].lowercased()).map(DebugCommand.show)
        case ("night", 2):
            return NightAction(rawValue: words[1].lowercased()).map(DebugCommand.night)
        case ("forget", 1):
            return .forgetHub
        default:
            return nil
        }
    }

    public static func parseAll(_ text: String) -> [DebugCommand] {
        text.split(separator: "\n", omittingEmptySubsequences: false).compactMap { parse(String($0)) }
    }

    /// Pairing still requires the fingerprint: the harness can say "pair", not "pair whatever".
    public static func fingerprintMatches(expected: String, candidate: String) -> Bool {
        !expected.isEmpty && expected.uppercased() == candidate.uppercased()
    }
}

/// Hub address and token from launch arguments (`-CocoDebugHost 127.0.0.1 -CocoDebugPort 18765
/// -CocoDebugToken …`), so a simulator run needs no typing. DEBUG builds only.
public struct DebugLaunchSettings: Equatable, Sendable {
    public var host: String
    public var port: Int
    public var token: String?

    public init?(host: String, port: Int, token: String?) {
        guard !host.isEmpty else { return nil }
        self.host = host
        self.port = port
        self.token = token
    }

    public init?(arguments: [String]) {
        func value(_ key: String) -> String? {
            guard let index = arguments.firstIndex(of: key), index + 1 < arguments.count else { return nil }
            return arguments[index + 1]
        }
        guard let host = value("-CocoDebugHost") else { return nil }
        let port = value("-CocoDebugPort").flatMap(Int.init) ?? HubEndpoint.defaultPort
        self.init(host: host, port: port, token: value("-CocoDebugToken"))
    }

    /// The settings to save: this host/port/token, keeping whatever Hub is already paired.
    public func applied(to current: HubSettings) -> HubSettings {
        HubSettings(manualHost: host, port: port, token: token ?? current.token, pin: current.pin)
    }
}
