#if os(iOS)
import UIKit

/// `UIScreen.main.brightness` on an iPad. On a Mac ("Designed for iPad") the app cannot set the
/// display's brightness, so it reads as full and writes are ignored: night mode there is the black
/// UI only.
@MainActor
public final class UIKitScreenBrightness: ScreenBrightness {
    private let isMac = ProcessInfo.processInfo.isiOSAppOnMac

    public init() {}

    public var level: Double {
        get { isMac ? 1 : Double(UIScreen.main.brightness) }
        set {
            guard !isMac else { return }
            UIScreen.main.brightness = CGFloat(min(1, max(0, newValue)))
        }
    }
}
#endif
