import CocoKit
import SwiftUI

/// A big, kid-friendly X that leaves a full-screen result for the home screen (#92):
/// `AppModel.closeScreen()` stops what the screen plays and shows idle. Which screens get it is
/// `ViewSpec.showsCloseButton` (CocoKit).
struct CloseButton: View {
    @Environment(AppModel.self) private var model: AppModel?

    var body: some View {
        if model?.currentView.showsCloseButton ?? true {
            button
        }
    }

    private var button: some View {
        Button {
            model?.closeScreen()
        } label: {
            Image(systemName: "xmark")
                .font(.system(size: 46, weight: .heavy))
                // A white ring: reads on a photo and on the light screens.
                .overlay(
                    Circle().strokeBorder(.white.opacity(0.9), lineWidth: 4)
                        .frame(width: ScreenChrome.closeDiameter, height: ScreenChrome.closeDiameter))
        }
        // M3 inverse surface (dark by day, light at night), fixed dark here so it reads on photos.
        .buttonStyle(
            MaterialIconButtonStyle(
                colors: MaterialColors(container: Color(Theme.light.inverseSurface).opacity(0.8), content: .white),
                diameter: ScreenChrome.closeDiameter, elevation: .level2))
        .accessibilityLabel("Close")
    }
}

/// Where the app's own chrome sits over every screen, so content can stay out from under it:
/// the wake-word hint top-left, the Settings gear top-right with the Close (X) button under it,
/// and the mic button bottom-centre.
enum ScreenChrome {
    /// The top row (wake-word hint, gear): content starts below it.
    static let top: CGFloat = 96
    /// Clear of the mic button (`MicButton.size`, 112pt, 36pt up from the bottom edge) with a gap.
    static let bottom: CGFloat = 36 + 112 + 12
    static let closeDiameter: CGFloat = 112
    static let closeTop: CGFloat = 84
    static let closeTrailing: CGFloat = 28
    /// The Close button's column at the right edge. Content that starts at `top` stays this far
    /// in from the right (and, to stay centred, from the left).
    static let closeColumn: CGFloat = closeTrailing + closeDiameter + closeTrailing
    /// The bottom of the Close button, plus a gap: content this far down is clear of it anywhere.
    static let belowClose: CGFloat = closeTop + closeDiameter + 16
}

extension View {
    /// The Close (X) button top-right, under the Settings gear (top-left holds the wake-word
    /// indicator, bottom-centre the mic button).
    func closeButton() -> some View {
        overlay(alignment: .topTrailing) {
            CloseButton()
                .padding(.top, ScreenChrome.closeTop)
                .padding(.trailing, ScreenChrome.closeTrailing)
        }
    }
}

#Preview {
    Color.gray.closeButton()
}
