#if DEBUG
import CocoKit
import Foundation

/// Drives the app from outside for end-to-end runs in the simulator, without taps
/// (`docs/simulator-testing.md`). DEBUG builds only: release and TestFlight builds compile none of
/// this, so a shipped app has no file inbox and takes no Hub settings from launch arguments.
///
/// - Launch arguments `-CocoDebugHost/-CocoDebugPort/-CocoDebugToken` set the Hub (see
///   `DebugLaunchSettings`). Pairing is NOT automatic: it still needs `pair <fingerprint>`.
/// - Commands (`DebugCommand`) are read from `Documents/coco-debug-commands.txt` in the app's
///   container, which is then deleted. After each command the resulting state is appended to
///   `Documents/coco-debug-log.txt`.
@MainActor
enum DebugHarness {
    static func run(model: AppModel) async {
        if let launch = DebugLaunchSettings(arguments: ProcessInfo.processInfo.arguments) {
            try? model.saveSettings(launch.applied(to: model.settings))
            log("launch settings applied: \(launch.host):\(launch.port)", model: model)
        }
        let inbox = documents.appendingPathComponent("coco-debug-commands.txt")
        while !Task.isCancelled {
            if let text = try? String(contentsOf: inbox, encoding: .utf8) {
                try? FileManager.default.removeItem(at: inbox)
                for command in DebugCommand.parseAll(text) {
                    await execute(command, model: model)
                }
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
    }

    private static func execute(_ command: DebugCommand, model: AppModel) async {
        var note = ""
        switch command {
        case .say(let text):
            await model.submit(text: text, source: .touch)
        case .pair(let fingerprint):
            if case .needsPairing(let candidate) = model.connection,
                DebugCommand.fingerprintMatches(expected: fingerprint, candidate: candidate.pin.fingerprint)
            {
                do { try model.pair(with: candidate) } catch { note = "pair failed: \(error)" }
            } else {
                note = "not paired: connection is \(model.connection)"
            }
        case .touchTimer(let seconds, let name):
            await model.timerStore.create(durationS: seconds, name: name)
        case .stopAlarm:
            model.perform(.alarmStop)
        case .show(let type):
            switch type {
            case .idle: model.show(.idle)
            case .timers: model.show(.timers)
            default: note = "show \(type.rawValue) needs a Hub reply; use say:"
            }
        case .night(.sleep):
            model.perform(.screenSleep)
        case .night(.wake):
            model.perform(.screenWake)
        case .forgetHub:
            do { try model.forgetHub() } catch { note = "forget failed: \(error)" }
        }
        // Let the reply land (views, events) before recording the state.
        try? await Task.sleep(nanoseconds: 300_000_000)
        log("\(command)\(note.isEmpty ? "" : " — \(note)")", model: model)
    }

    private static var documents: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    private static func log(_ what: String, model: AppModel) {
        let timers = model.timerStore.timers
            .map { "\($0.name ?? "timer")\($0.isRinging ? "(ringing)" : "")" }.joined(separator: ",")
        let line = [
            ISO8601DateFormatter().string(from: Date()), what,
            "connection=\(model.connection)", "view=\(model.currentView.viewType?.rawValue ?? "?")",
            "say=\(model.lastSay ?? "-")", "error=\(model.lastError.map { "\($0)" } ?? "-")",
            "timers=[\(timers)]", "alarm=\(model.alarmRinging)", "night=\(model.night.phase)",
        ].joined(separator: " | ")
        let url = documents.appendingPathComponent("coco-debug-log.txt")
        let data = Data((line + "\n").utf8)
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(data)
            try? handle.close()
        } else {
            try? data.write(to: url)
        }
    }
}
#endif
