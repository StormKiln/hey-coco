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
                    // Apple Music (#42) + Spotify wake (#5) through the Spotify iOS SDK (#116)
                    model.installLiveMusic(spotifyAppRemote: SpotifyAppRemoteBridge())
                    model.start()
                }
                // Spotify's redirect back to Coco after an SDK wake (#116).
                .onOpenURL { model.handleOpenURL($0) }
                // A new network path may mean the Hub has a new address: look for it again.
                .task {
                    for await _ in NetworkPathChanges.updates() { model.networkChanged() }
                }
                #if DEBUG
                // Simulator end-to-end runs (docs/simulator-testing.md). Not in release builds.
                .task { await DebugHarness.run(model: model) }
                #endif
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            // Kitchen display: keep the screen on whenever Coco is in front. Not on a Mac
            // ("Designed for iPad"), where the Mac's own energy settings decide (#25).
            if !ProcessInfo.processInfo.isiOSAppOnMac {
                UIApplication.shared.isIdleTimerDisabled = phase == .active
            }
            // Ring anything that came due while the app was suspended.
            if phase == .active {
                model.timerStore.fireDueAlarms()
                model.appBecameActive()
            }
        }
    }
}
