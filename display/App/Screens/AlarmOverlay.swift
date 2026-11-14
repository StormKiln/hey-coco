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
                    (flash ? Color.orange : Color.red)
                        .ignoresSafeArea()
                        .animation(.easeInOut(duration: 0.3), value: flash)
                    VStack(spacing: 40) {
                        Image(systemName: "bell.and.waves.left.and.right.fill")
                            .font(.system(size: 120))
                            .symbolEffect(.bounce, options: .repeating, isActive: !reduceMotion)
                        Text(title)
                            .font(.system(size: 88, weight: .bold, design: .rounded))
                            .multilineTextAlignment(.center)
                            .minimumScaleFactor(0.4)
                            .lineLimit(2)
                        Text("Time's up")
                            .font(.system(size: 48, weight: .medium, design: .rounded))
                        Button {
                            Task { await timers.stopAlarms() }
                        } label: {
                            Text("Stop")
                                .font(.system(size: 96, weight: .heavy, design: .rounded))
                                .foregroundStyle(.red)
                                .frame(width: 520, height: 200)
                                .background(.white, in: RoundedRectangle(cornerRadius: 48, style: .continuous))
                        }
                        .buttonStyle(.plain)
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
