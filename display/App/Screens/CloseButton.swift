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
                .overlay(Circle().strokeBorder(.white.opacity(0.9), lineWidth: 4).frame(width: 112, height: 112))
        }
        // M3 inverse surface (dark by day, light at night), fixed dark here so it reads on photos.
        .buttonStyle(
            MaterialIconButtonStyle(
                colors: MaterialColors(container: Color(Theme.light.inverseSurface).opacity(0.8), content: .white),
                diameter: 112, elevation: .level2))
        .accessibilityLabel("Close")
    }
}

extension View {
    /// The Close (X) button top-right, under the Settings gear (top-left holds the wake-word
    /// indicator, bottom-centre the mic button).
    func closeButton() -> some View {
        overlay(alignment: .topTrailing) {
            CloseButton()
                .padding(.top, 84)
                .padding(.trailing, 28)
        }
    }
}

#Preview {
    Color.gray.closeButton()
}
