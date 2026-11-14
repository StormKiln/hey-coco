import CocoKit
import SwiftUI

extension View {
    /// Night mode (#24): dark theme and a dimming veil at bedtime, pure black when idle. Apply it
    /// below the alarm overlay so a ringing timer always shows on top.
    func nightMode(model: AppModel) -> some View {
        modifier(NightModeLayer(model: model))
    }
}

struct NightModeLayer: ViewModifier {
    let model: AppModel
    @Environment(\.scenePhase) private var scenePhase

    private var night: NightModeController { model.night }

    func body(content: Content) -> some View {
        content
            .preferredColorScheme(night.phase == .day ? nil : .dark)
            // Any touch counts as activity (doesn't steal the tap).
            .simultaneousGesture(TapGesture().onEnded { night.noteActivity() })
            .overlay { NightOverlay(phase: night.phase, wake: night.wake) }
            .animation(.easeInOut(duration: 0.6), value: night.phase)
            .onAppear { night.start() }
            .onChange(of: model.alarmRinging, initial: true) { _, ringing in
                night.setHold(.alarm, ringing)
            }
            .onChange(of: model.mediaPlaying != nil, initial: true) { _, playing in
                night.setHold(.media, playing)
            }
            .onChange(of: scenePhase) { _, phase in
                switch phase {
                case .active: night.didBecomeActive()
                case .background: night.didEnterBackground()
                default: break
                }
            }
    }
}

/// `.dim`: a veil that lowers contrast but lets touches through. `.dark`: pure black over
/// everything, and the whole screen is one tap target that wakes it.
struct NightOverlay: View {
    let phase: NightPhase
    let wake: () -> Void

    var body: some View {
        switch phase {
        case .day:
            EmptyView()
        case .dim:
            Color.black.opacity(0.45)
                .ignoresSafeArea()
                .allowsHitTesting(false)
                .transition(.opacity)
        case .dark:
            Color.black
                .ignoresSafeArea()
                .contentShape(Rectangle())
                .onTapGesture(perform: wake)
                .accessibilityElement()
                .accessibilityLabel("Screen off for the night")
                .accessibilityHint("Tap to wake")
                .accessibilityAddTraits(.isButton)
                .accessibilityAction { wake() }
                .transition(.opacity)
        }
    }
}
