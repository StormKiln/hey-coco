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
                .task { model.start() }
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            // Kitchen display: keep the screen on whenever Coco is in front.
            UIApplication.shared.isIdleTimerDisabled = phase == .active
        }
    }
}
