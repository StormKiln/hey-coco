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
                .foregroundStyle(.white)
                .frame(width: 112, height: 112)
                // Dark disc with a white ring: reads on a photo and on the light screens.
                .background(.black.opacity(0.6), in: Circle())
                .overlay(Circle().strokeBorder(.white.opacity(0.9), lineWidth: 4))
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
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
