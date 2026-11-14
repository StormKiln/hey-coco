import Foundation

/// `AppModel` drives `ConversationController`: it supplies the on-screen view, talks to the Hub
/// and applies the reply.
extension AppModel: ConversationHost {
    public func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action {
        guard let client else { throw .unreachable("Not connected to Coco Hub.") }
        return try await client.postUtterance(request)
    }

    /// Display-side commands (alarm, media, volume, screen) arrive with the features that own them.
    public func perform(_ command: Command) {}
}
