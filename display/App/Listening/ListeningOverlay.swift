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
            // M3 scrim.
            Color.black.opacity(0.32)
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture(perform: dismiss)
            card
                .padding(48)
                .frame(maxWidth: 900)
                // An M3 dialog: extra-large corners on a high, tinted surface.
                .m3Surface(Theme.surfaceContainerHigh, shape: .extraLarge, elevation: .level3)
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
                    .m3Font(.titleSmall, weight: .medium)
                    .labelStyle(MaterialLabelStyle())
                    .foregroundStyle(Theme.primary)
                    .symbolEffect(.variableColor.iterative)
                Text(partial.isEmpty ? "Go ahead, I'm listening." : partial)
                    .m3Font(.headlineMedium)
                    .foregroundStyle(partial.isEmpty ? Theme.onSurfaceVariant : Theme.onSurface)
                    .multilineTextAlignment(.center)
                    .minimumScaleFactor(0.5)
            }
        case .thinking(let heard):
            VStack(spacing: 24) {
                Text(heard)
                    .m3Font(.headlineSmall)
                    .foregroundStyle(Theme.onSurface)
                    .multilineTextAlignment(.center)
                    .minimumScaleFactor(0.5)
                ProgressView()
                    .controlSize(.extraLarge)
                    .tint(Theme.primary)
                Text("Thinking…")
                    .m3Font(.bodyMedium)
                    .foregroundStyle(Theme.onSurfaceVariant)
            }
        case .speaking(let reply):
            Text(reply)
                .m3Font(.headlineMedium)
                .foregroundStyle(Theme.onSurface)
                .multilineTextAlignment(.center)
                .minimumScaleFactor(0.4)
        case .error(let message):
            Label(message, systemImage: "exclamationmark.triangle")
                .m3Font(.titleLarge)
                .labelStyle(MaterialLabelStyle())
                .foregroundStyle(Theme.error)
                .multilineTextAlignment(.center)
        case .permissionNeeded(let permission):
            VStack(spacing: 24) {
                Image(systemName: "mic.slash")
                    .font(.system(size: 56))
                    .foregroundStyle(Theme.onErrorContainer)
                    .frame(width: 112, height: 112)
                    .m3Surface(Theme.errorContainer, shape: nil)
                Text(Self.explanation(for: permission))
                    .m3Font(.titleMedium)
                    .foregroundStyle(Theme.onSurface)
                    .multilineTextAlignment(.center)
                if Self.settingsCanFix(permission), let url = URL(string: UIApplication.openSettingsURLString) {
                    Button("Open Settings") { openURL(url) }
                        .buttonStyle(MaterialButtonStyle(colors: .filled))
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
