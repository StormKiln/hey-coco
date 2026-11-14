import CocoKit
import SwiftUI

/// The big tap-to-talk button, bottom-centre. Tap to talk; tap again to cancel.
struct MicButton: View {
    let state: ConversationState
    let action: () -> Void

    static let size: CGFloat = 112

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(fill)
                    .shadow(color: .black.opacity(0.25), radius: 12, y: 4)
                if case .listening = state {
                    PulseRing()
                }
                Image(systemName: symbol)
                    .font(.system(size: 48, weight: .semibold))
                    .foregroundStyle(.white)
                    .contentTransition(.symbolEffect(.replace))
            }
            .frame(width: Self.size, height: Self.size)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(state.isActive ? "Stop" : "Talk to Coco")
    }

    private var symbol: String {
        switch state {
        case .listening: "waveform"
        case .thinking, .speaking: "stop.fill"
        case .idle, .error, .permissionNeeded: "mic.fill"
        }
    }

    private var fill: Color {
        switch state {
        case .listening: .red
        case .thinking, .speaking: .gray
        case .idle, .error, .permissionNeeded: .accentColor
        }
    }
}

private struct PulseRing: View {
    @State private var expanded = false

    var body: some View {
        Circle()
            .stroke(Color.red.opacity(0.6), lineWidth: 6)
            .scaleEffect(expanded ? 1.5 : 1)
            .opacity(expanded ? 0 : 1)
            .onAppear {
                withAnimation(.easeOut(duration: 1.2).repeatForever(autoreverses: false)) {
                    expanded = true
                }
            }
    }
}
