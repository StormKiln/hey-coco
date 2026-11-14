import CocoKit
import SwiftUI
import UIKit

@main
struct CocoDisplayApp: App {
    @State private var model = AppModel.live()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .task {
                    // The alarm tone lives in Library/Sounds so the notification backup can use it.
                    _ = try? AlarmTone.installNotificationSound()
                    model.start()
                }
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            // Kitchen display: keep the screen on whenever Coco is in front. Not on a Mac
            // ("Designed for iPad"), where the Mac's own energy settings decide (#25).
            if !ProcessInfo.processInfo.isiOSAppOnMac {
                UIApplication.shared.isIdleTimerDisabled = phase == .active
            }
            // Ring anything that came due while the app was suspended.
            if phase == .active { model.timerStore.fireDueAlarms() }
        }
    }
}
