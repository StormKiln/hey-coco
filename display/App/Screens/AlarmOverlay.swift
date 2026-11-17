import CocoKit
import SwiftUI

/// Full-screen, flashing "time's up" over everything (keep it the topmost layer, above any
/// future night mode). The huge Stop button silences the sound at once and dismisses every
/// ringing timer (on the Hub too, or queued if it is down).
struct AlarmOverlay: View {
    @Environment(TimerStore.self) private var timers
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        if timers.isAlarmRinging {
            TimelineView(.periodic(from: .now, by: 0.6)) { context in
                let flash = !reduceMotion && Int(context.date.timeIntervalSinceReferenceDate / 0.6) % 2 == 0
                ZStack {
                    (flash ? Theme.alarmOrange : Theme.alarmRed)
                        .ignoresSafeArea()
                        .animation(.easeInOut(duration: 0.3), value: flash)
                    VStack(spacing: 40) {
                        Image(systemName: "bell.and.waves.left.and.right.fill")
                            .font(.system(size: 120))
                            .symbolEffect(.bounce, options: .repeating, isActive: !reduceMotion)
                        Text(title)
                            .m3Font(.displaySmall, weight: .bold)
                            .multilineTextAlignment(.center)
                            .minimumScaleFactor(0.4)
                            .lineLimit(2)
                        Text("Time's up")
                            .m3Font(.headlineMedium, weight: .medium)
                        Button {
                            Task { await timers.stopAlarms() }
                        } label: {
                            Label("Stop", systemImage: "stop")
                                .font(.system(size: 96, weight: .heavy, design: .rounded))
                                .labelStyle(MaterialLabelStyle())
                                .frame(width: 520, height: 200)
                        }
                        // An extra-large M3 filled button: white on the flashing red/orange.
                        .buttonStyle(
                            MaterialButtonStyle(
                                colors: MaterialColors(container: .white, content: Theme.alarmRed), elevation: .level3))
                        .accessibilityLabel("Stop alarm")
                    }
                    .foregroundStyle(.white)
                    .padding(48)
                }
            }
            .transition(.opacity)
        }
    }

    private var title: String {
        timers.ringingTimers.map(\.displayName).joined(separator: ", ")
    }
}
