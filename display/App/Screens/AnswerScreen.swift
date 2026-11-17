import SwiftUI

/// `ViewSpec.answer`: the spoken reply, large enough to read across the kitchen. Returning to
/// idle after a quiet spell is handled by `ConversationLayer` (CocoKit `AutoReturnTimer`).
struct AnswerScreen: View {
    let text: String

    var body: some View {
        ScrollView {
            Text(text)
                .m3Font(.headlineLarge)
                .foregroundStyle(Theme.onSurface)
                .multilineTextAlignment(.center)
                .minimumScaleFactor(0.4)
                .padding(.horizontal, 56)
                .padding(.vertical, 48)
                .frame(maxWidth: 1000)
                // An M3 card: the reply on a raised, tinted surface.
                .m3Surface(Theme.surfaceContainerLow, shape: .extraLarge, elevation: .level1)
                .padding(.horizontal, 64)
                // Below the Close button (top right: 84pt down, 112pt tall).
                .padding(.top, 212)
                // Leave room for the mic button.
                .padding(.bottom, 200)
                .frame(maxWidth: .infinity)
        }
        .scrollBounceBehavior(.basedOnSize)
        .accessibilityLabel(text)
        .closeButton()
    }
}

#Preview {
    AnswerScreen(text: "Paris is the capital of France.")
}
