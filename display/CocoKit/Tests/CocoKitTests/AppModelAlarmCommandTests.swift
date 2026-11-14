import Foundation
import Testing
@testable import CocoKit

@MainActor
private func model(_ store: TimerStore) -> AppModel {
    AppModel(
        config: UserDefaultsHubConfigStore(
            defaults: UserDefaults(suiteName: "coco-alarm-\(UUID().uuidString)")!,
            tokens: InMemoryTokenStore(), pins: InMemoryTokenStore()),
        discovery: FakeDiscovery(results: []), probe: FakeHubProbe(),
        makeClient: { _ in FakeHubAPI() },
        makeEvents: { _ in AsyncStream { _ in } },
        timerStore: store)
}

@MainActor
@Suite struct AppModelAlarmCommandTests {
    @Test func alarmRingingReflectsTimerStore() {
        let player = SilentAlarmPlayer()
        let model = model(TimerStore(player: player))
        #expect(model.alarmRinging == false)
        player.start()
        #expect(model.alarmRinging == true)
    }

    @Test func alarmStopCommandStopsTheAlarm() async {
        let player = SilentAlarmPlayer()
        let model = model(TimerStore(player: player))
        player.start()
        model.perform(.alarmStop)
        #expect(await waitUntil { !player.isRinging })
    }
}
