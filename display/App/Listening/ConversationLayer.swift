import CocoKit
import SwiftUI

extension View {
    /// Adds tap-to-talk on top of the router: the mic button, the listening overlay, and the
    /// Answer screen's return to idle.
    func conversationLayer(model: AppModel) -> some View {
        modifier(ConversationLayer(model: model))
    }
}

struct ConversationLayer: ViewModifier {
    let model: AppModel
    @State private var conversation: ConversationController?
    @State private var autoReturn: AutoReturnTimer?

    func body(content: Content) -> some View {
        content
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
            .animation(.easeInOut(duration: 0.2), value: conversation?.state)
            .onAppear(perform: setUp)
            .onChange(of: model.currentView) { updateAutoReturn() }
            .onChange(of: conversation?.state) { updateAutoReturn() }
    }

    private func setUp() {
        guard conversation == nil else { return }
        conversation = .live(host: model)
        autoReturn = AutoReturnTimer { [model] in model.show(.idle) }
        updateAutoReturn()
    }

    private func updateAutoReturn() {
        autoReturn?.update(view: model.currentView, busy: conversation?.state.isActive ?? false)
    }
}
