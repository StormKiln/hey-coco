import Foundation

/// `AppModel` drives `ConversationController`: it supplies the on-screen view, talks to the Hub
/// and applies the reply.
extension AppModel: ConversationHost {
    public func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action {
        guard let client else { throw .unreachable("Not connected to Coco Hub.") }
        return try await client.postUtterance(request)
    }

    /// True while any timer rings or the alarm sound plays; sent as `context.alarm_ringing` so the
    /// Hub lets a bare "stop" dismiss it.
    public var alarmRinging: Bool { timerStore.isAlarmRinging || timerStore.player.isRinging }

    /// Display-side commands. Media, volume and screen commands arrive with the features that
    /// own them.
    public func perform(_ command: Command) {
        switch command {
        case .alarmStop:
            // Silence now; the Hub has already dismissed its timers.
            timerStore.player.stop()
            Task { await timerStore.stopAlarms() }
        default:
            break
        }
    }
}
