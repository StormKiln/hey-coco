import SwiftUI

/// `ViewSpec.answer`: the spoken reply, large enough to read across the kitchen. Returning to
/// idle after a quiet spell is handled by `ConversationLayer` (CocoKit `AutoReturnTimer`).
struct AnswerScreen: View {
    let text: String

    var body: some View {
        ScrollView {
            Text(text)
                .font(.system(size: 64, weight: .semibold, design: .rounded))
                .multilineTextAlignment(.center)
                .minimumScaleFactor(0.4)
                .frame(maxWidth: 1000)
                .padding(.horizontal, 64)
                .padding(.top, 96)
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
