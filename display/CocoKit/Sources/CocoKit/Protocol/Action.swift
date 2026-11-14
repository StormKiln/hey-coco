import Foundation

/// The Hub's reply to an utterance (`POST /v1/utterance`).
public struct Action: Codable, Sendable, Equatable {
    public enum MatchedBy: String, Codable, Sendable, Equatable {
        case rules, qwen, none
    }

    /// Text to speak and show; `nil` for silent actions.
    public var say: String?
    /// What to show; `nil` keeps the current screen.
    public var view: ViewSpec?
    /// Applied in order, before `say`.
    public var commands: [Command]
    /// Dotted intent name, e.g. `timer.create`.
    public var intent: String
    public var matchedBy: MatchedBy

    enum CodingKeys: String, CodingKey {
        case say, view, commands, intent
        case matchedBy = "matched_by"
    }
}
