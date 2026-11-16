import Foundation

// MARK: - Close (X) button (#92)

extension ViewSpec {
    /// Full-screen results whose only other way out is waiting for a timeout get a big Close
    /// (X) button. Idle is home; weather, timers and now playing have a Home button; YouTube
    /// has its own close button.
    public var showsCloseButton: Bool {
        switch self {
        case .answer, .picture, .sound: true
        default: false
        }
    }
}

extension AppModel {
    /// The Close (X) button: stop what this screen is playing, then go back to idle.
    public func closeScreen() {
        switch currentView {
        case .sound: media.stop(.sound)
        case .youtube: media.stop(.youtube)
        default: break
        }
        show(.idle)
    }
}
