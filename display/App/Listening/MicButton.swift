import CocoKit
import SwiftUI

/// The big tap-to-talk button, bottom-centre. Tap to talk; tap again to cancel. An M3 large FAB:
/// a rounded square in the primary colour (the error colour while it listens).
struct MicButton: View {
    let state: ConversationState
    let action: () -> Void

    static let size: CGFloat = 112
    static let cornerRadius: CGFloat = 32

    var body: some View {
        Button(action: action) {
            ZStack {
                if case .listening = state {
                    PulseRing()
                }
                Image(systemName: symbol)
                    .font(.system(size: 48, weight: .semibold))
                    .contentTransition(.symbolEffect(.replace))
            }
        }
        .buttonStyle(FABStyle(colors: colors))
        .accessibilityLabel(state.isActive ? "Stop" : "Talk to Coco")
    }

    private var symbol: String {
        switch state {
        case .listening: "waveform"
        case .thinking, .speaking: "stop.fill"
        case .idle, .error, .permissionNeeded: "mic.fill"
        }
    }

    private var colors: MaterialColors {
        switch state {
        case .listening: .danger
        case .thinking, .speaking: .tonal
        case .idle, .error, .permissionNeeded: .filled
        }
    }
}

private struct FABStyle: ButtonStyle {
    let colors: MaterialColors

    func makeBody(configuration: Configuration) -> some View {
        let shape = RoundedRectangle(cornerRadius: MicButton.cornerRadius, style: .continuous)
        return configuration.label
            .foregroundStyle(colors.content)
            .frame(width: MicButton.size, height: MicButton.size)
            .background {
                shape.fill(colors.container)
                    .shadow(color: .black.opacity(configuration.isPressed ? 0.12 : 0.2), radius: 14, y: 6)
            }
            .overlay(shape.fill(colors.content.opacity(configuration.isPressed ? 0.12 : 0)))
            .contentShape(shape)
            .animation(.easeOut(duration: 0.15), value: configuration.isPressed)
    }
}

private struct PulseRing: View {
    @State private var expanded = false

    var body: some View {
        RoundedRectangle(cornerRadius: MicButton.cornerRadius, style: .continuous)
            .stroke(Theme.error.opacity(0.6), lineWidth: 6)
            .frame(width: MicButton.size, height: MicButton.size)
            .scaleEffect(expanded ? 1.5 : 1)
            .opacity(expanded ? 0 : 1)
            .onAppear {
                withAnimation(.easeOut(duration: 1.2).repeatForever(autoreverses: false)) {
                    expanded = true
                }
            }
    }
}
