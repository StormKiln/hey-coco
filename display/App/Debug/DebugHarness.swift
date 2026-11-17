#if DEBUG
import CocoKit
import Foundation
import UIKit

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
        case .pairCode(let fingerprint, let code):
            if case .needsPairing(let candidate) = model.connection,
                DebugCommand.fingerprintMatches(expected: fingerprint, candidate: candidate.pin.fingerprint)
            {
                do { try await model.pair(with: candidate, code: code) } catch { note = "pair-code failed: \(error.message)" }
            } else {
                note = "not paired: connection is \(model.connection)"
            }
        case .pairURI(let text):
            do {
                try await model.pair(scanned: try PairingURI.parse(text))
            } catch let error as PairingURIError {
                note = "pair-uri failed: \(error.message)"
            } catch let error as PairingError {
                note = "pair-uri failed: \(error.message)"
            } catch {
                note = "pair-uri failed: \(error)"
            }
        case .touchTimer(let seconds, let name):
            await model.timerStore.create(durationS: seconds, name: name)
        case .stopAlarm:
            model.perform(.alarmStop)
        case .show(let type):
            switch type {
            case .idle: model.show(.idle)
            case .timers: model.show(.timers)
            case .nowPlaying: model.show(.nowPlaying)
            default: note = "show \(type.rawValue) needs a Hub reply; use say:"
            }
        case .night(.sleep):
            model.perform(.screenSleep)
        case .night(.wake):
            model.perform(.screenWake)
        case .forgetHub:
            do { try model.forgetHub() } catch { note = "forget failed: \(error)" }
        case .nowPlaying(let nowPlaying):
            model.media.nowPlayingChanged(nowPlaying)
        case .rotate(let orientation):
            let mask: UIInterfaceOrientationMask = orientation == .portrait ? .portrait : .landscapeLeft
            for scene in UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }) {
                scene.requestGeometryUpdate(.iOS(interfaceOrientations: mask)) { error in
                    print("rotate failed: \(error)")
                }
            }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        case .view(let name):
            do {
                let data = try Data(contentsOf: documents.appendingPathComponent("\(name).json"))
                model.show(try CocoJSON.decoder.decode(ViewSpec.self, from: data))
            } catch {
                note = "view failed: \(error)"
            }
        case .idle(let name):
            // Only sticks while no Hub is connected: the next real fetch replaces it.
            do {
                let data = try Data(contentsOf: documents.appendingPathComponent("\(name).json"))
                let info = try CocoJSON.decoder.decode(IdleInfo.self, from: data)
                await model.idleModel.refresh(using: RecordedIdleHub(info: info))
            } catch {
                note = "idle failed: \(error)"
            }
        case .screenshot(let name):
            // Let animations settle before drawing.
            try? await Task.sleep(nanoseconds: 600_000_000)
            note = screenshot(to: documents.appendingPathComponent("\(name).png"))
        }
        // Let the reply land (views, events) before recording the state.
        try? await Task.sleep(nanoseconds: 300_000_000)
        // A pairing URI holds the token: never write it to the log.
        let label = if case .pairURI = command { "pairURI(<redacted>)" } else { "\(command)" }
        log("\(label)\(note.isEmpty ? "" : " — \(note)")", model: model)
    }

    /// Draws the app's own windows, bottom to top, so a system permission alert (which the
    /// simulator can't dismiss without a tap) never covers the screen being checked.
    private static func screenshot(to url: URL) -> String {
        let windows = UIApplication.shared.connectedScenes
            .compactMap { $0 as? UIWindowScene }
            .flatMap(\.windows)
            .filter { !$0.isHidden }
            .sorted { $0.windowLevel < $1.windowLevel }
        guard let bounds = windows.first?.screen.bounds else { return "screenshot failed: no window" }
        let image = UIGraphicsImageRenderer(bounds: bounds).image { _ in
            for window in windows {
                window.drawHierarchy(in: window.frame, afterScreenUpdates: true)
            }
        }
        do {
            try image.pngData()?.write(to: url)
            return "screenshot \(url.lastPathComponent)"
        } catch {
            return "screenshot failed: \(error)"
        }
    }

    private static var documents: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }

    private static func log(_ what: String, model: AppModel) {
        let timers = model.timerStore.timers
            .map { "\($0.name ?? "timer")\($0.isRinging ? "(ringing)" : "")" }.joined(separator: ",")
        let line = [
            ISO8601DateFormatter().string(from: Date()), what,
            "connection=\(model.connection)",
            "hub=\(model.endpoint.map { "\($0.host):\($0.port)" } ?? "-")",
            "searchTakingLong=\(model.searchTakingLong)", "view=\(model.currentView.viewType?.rawValue ?? "?")",
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

/// Answers `/v1/idle` with a recorded `IdleInfo` (the `idle <name>` command); nothing else.
private struct RecordedIdleHub: HubAPI {
    let info: IdleInfo

    func idle() async throws(HubError) -> IdleInfo { info }
    func postUtterance(_ request: UtteranceRequest) async throws(HubError) -> Action { throw unsupported }
    func timers() async throws(HubError) -> TimersList { throw unsupported }
    func createTimer(_ request: TimerCreateRequest) async throws(HubError) -> TimerInfo { throw unsupported }
    func dismissTimer(id: String) async throws(HubError) -> TimerInfo { throw unsupported }
    func cancelTimer(id: String) async throws(HubError) -> TimerInfo { throw unsupported }

    private var unsupported: HubError { .server(code: "unsupported", message: "Recorded idle data only.") }
}
#endif
