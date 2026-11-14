import CocoKit
import SwiftUI
import UIKit

/// Shown over the current screen while a conversation is in progress: the live transcript,
/// a thinking indicator, the reply while it's spoken, or why Coco can't listen.
/// Tapping outside the card cancels.
struct ListeningOverlay: View {
    let state: ConversationState
    let dismiss: () -> Void
    @Environment(\.openURL) private var openURL

    var body: some View {
        ZStack {
            Color.black.opacity(0.35)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture(perform: dismiss)
            card
                .padding(40)
                .frame(maxWidth: 900)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 32, style: .continuous))
                .padding(.horizontal, 48)
                // Keep clear of the mic button.
                .padding(.bottom, 160)
        }
    }

    @ViewBuilder
    private var card: some View {
        switch state {
        case .idle:
            EmptyView()
        case .listening(let partial):
            VStack(spacing: 20) {
                Label("Listening…", systemImage: "waveform")
                    .font(.system(size: 32, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
                    .symbolEffect(.variableColor.iterative)
                Text(partial.isEmpty ? "Go ahead, I'm listening." : partial)
                    .font(.system(size: 52, weight: .semibold, design: .rounded))
                    .foregroundStyle(partial.isEmpty ? .secondary : .primary)
                    .multilineTextAlignment(.center)
                    .minimumScaleFactor(0.5)
            }
        case .thinking(let heard):
            VStack(spacing: 24) {
                Text(heard)
                    .font(.system(size: 44, weight: .semibold, design: .rounded))
                    .multilineTextAlignment(.center)
                    .minimumScaleFactor(0.5)
                ProgressView()
                    .controlSize(.extraLarge)
                Text("Thinking…")
                    .font(.system(size: 28, design: .rounded))
                    .foregroundStyle(.secondary)
            }
        case .speaking(let reply):
            Text(reply)
                .font(.system(size: 52, weight: .semibold, design: .rounded))
                .multilineTextAlignment(.center)
                .minimumScaleFactor(0.4)
        case .error(let message):
            Label(message, systemImage: "exclamationmark.triangle")
                .font(.system(size: 40, weight: .semibold, design: .rounded))
                .multilineTextAlignment(.center)
        case .permissionNeeded(let permission):
            VStack(spacing: 24) {
                Image(systemName: "mic.slash")
                    .font(.system(size: 64))
                    .foregroundStyle(.secondary)
                Text(Self.explanation(for: permission))
                    .font(.system(size: 34, weight: .semibold, design: .rounded))
                    .multilineTextAlignment(.center)
                if Self.settingsCanFix(permission), let url = URL(string: UIApplication.openSettingsURLString) {
                    Button("Open Settings") { openURL(url) }
                        .font(.system(size: 30, weight: .semibold, design: .rounded))
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                }
            }
        }
    }

    static func explanation(for permission: SpeechPermission) -> String {
        switch permission {
        case .granted: ""
        case .microphoneDenied:
            "Coco needs the microphone to hear you. Turn on Microphone for Coco in Settings."
        case .speechRecognitionDenied:
            "Coco needs Speech Recognition to understand you. Turn it on for Coco in Settings."
        case .restricted:
            "Speech recognition is restricted on this iPad (Screen Time or device management)."
        case .recognizerUnavailable:
            "Speech recognition isn't available right now. Check the network and try again."
        }
    }

    static func settingsCanFix(_ permission: SpeechPermission) -> Bool {
        permission == .microphoneDenied || permission == .speechRecognitionDenied
    }
}
