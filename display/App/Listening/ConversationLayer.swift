import CocoKit
import SwiftUI

extension View {
    /// Adds voice on top of the router: the wake-word listening loop, tap-to-talk (mic button and
    /// listening overlay), the bare "stop" while an alarm rings, and the Answer screen's return to
    /// idle. Screens can read the loop (e.g. `ducking`) with `@Environment(VoiceLoop.self)`.
    func conversationLayer(model: AppModel) -> some View {
        modifier(ConversationLayer(model: model))
    }
}

struct ConversationLayer: ViewModifier {
    let model: AppModel
    @State private var voice: VoiceLoop?
    @State private var autoReturn: AutoReturnTimer?
    @Environment(\.scenePhase) private var scenePhase

    private var conversation: ConversationController? { voice?.conversation }

    func body(content: Content) -> some View {
        content
            .environment(voice)
            // Any touch on the screen counts as interaction (doesn't steal the tap).
            .simultaneousGesture(TapGesture().onEnded { autoReturn?.noteInteraction() })
            .overlay {
                if let conversation, conversation.state != .idle {
                    ListeningOverlay(state: conversation.state, dismiss: conversation.cancel)
                        .transition(.opacity)
                }
            }
            .overlay(alignment: .bottom) {
                if let conversation {
                    MicButton(state: conversation.state, action: conversation.micTapped)
                        .padding(.bottom, 36)
                }
            }
            .overlay(alignment: .bottomLeading) {
                if let voice {
                    WakeWordIndicator(status: voice.wakeWord)
                        .padding(24)
                }
            }
            .animation(.easeInOut(duration: 0.2), value: conversation?.state)
            .onAppear(perform: setUp)
            .onChange(of: model.currentView) { updateAutoReturn() }
            .onChange(of: conversation?.state) { updateAutoReturn() }
            // Coco's own media (sounds, YouTube) drops in volume while it listens and replies.
            .onChange(of: voice?.ducking ?? false) { _, ducked in model.media.setDucked(ducked) }
            .onChange(of: model.alarmRinging, initial: true) { _, ringing in
                voice?.alarmRingingChanged(ringing)
            }
            .onChange(of: scenePhase) { _, phase in
                guard phase == .active, let voice else { return }
                Task { await voice.resume() }
            }
    }

    private func setUp() {
        guard voice == nil else { return }
        let voice = VoiceLoop.live(host: model, wakeWord: .bundled())
        self.voice = voice
        autoReturn = AutoReturnTimer { [model] in model.show(.idle) }
        updateAutoReturn()
        Task {
            await voice.start()
            voice.alarmRingingChanged(model.alarmRinging)
        }
    }

    private func updateAutoReturn() {
        autoReturn?.update(view: model.currentView, busy: conversation?.state.isActive ?? false)
    }
}

/// A faint hint, bottom-left, when "hey coco" isn't listening (tap-to-talk still works). Hidden
/// while armed or briefly paused for a conversation or alarm.
struct WakeWordIndicator: View {
    let status: VoiceLoop.WakeWordStatus

    var body: some View {
        if let text {
            Label(text, systemImage: "ear.trianglebadge.exclamationmark")
                .font(.footnote)
                .foregroundStyle(.tertiary)
                .accessibilityLabel(text)
        }
    }

    private var text: String? {
        switch status {
        case .armed, .paused: nil
        case .unavailable: "Wake word off"
        case .retrying: "Wake word restarting…"
        }
    }
}
