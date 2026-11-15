import CocoKit
import SwiftUI
import UIKit

/// "Voice test" in Diagnostics (#6): start/stop the `VoiceTestRecorder`, live counters, and the
/// plain-text report (copy or share). Off by default; no audio is recorded or stored.
/// Procedure: `docs/spikes/voice-test-procedure.md`.
struct VoiceTestSection: View {
    let recorder: VoiceTestRecorder
    let wakeWord: VoiceLoop.WakeWordStatus
    let device: String
    @State private var copied = false

    var body: some View {
        Section {
            Button {
                if recorder.isRecording {
                    recorder.stop()
                } else {
                    recorder.start(
                        onDeviceRecognition: AppleSpeechRecognizer.supportsOnDeviceRecognition(),
                        wakeWord: wakeWord, device: device)
                }
            } label: {
                Label(
                    recorder.isRecording ? "Stop voice test" : "Start voice test",
                    systemImage: recorder.isRecording ? "stop.circle" : "record.circle")
            }
            if recorder.startedAt != nil {
                // Redraw once a second so the duration and listening time tick.
                TimelineView(.periodic(from: .now, by: 1)) { _ in counters }
                // Also on the timeline, so the shared report has the current duration.
                TimelineView(.periodic(from: .now, by: 1)) { _ in actions }
            }
        } header: {
            Text("Voice test")
        } footer: {
            Text(
                "Counts wake-word detections, probable false wakes, latency, listening time and "
                    + "thermal/battery every minute while on. No audio or transcripts are kept.")
        }
        .onChange(of: recorder.isRecording) { copied = false }
    }

    private var actions: some View {
        HStack {
            Button {
                UIPasteboard.general.string = recorder.report()
                copied = true
            } label: {
                Label(copied ? "Copied" : "Copy report", systemImage: "doc.on.doc")
            }
            .buttonStyle(.borderless)
            Spacer()
            ShareLink(item: recorder.report()) {
                Label("Share report…", systemImage: "square.and.arrow.up")
            }
            .buttonStyle(.borderless)
        }
    }

    @ViewBuilder private var counters: some View {
        let s = recorder.summary
        row("Status", recorder.isRecording ? "Recording" : "Stopped")
        row("Duration", DiagnosticsFormat.uptime(seconds: Int(s.duration)))
        row("On-device recognition", recorder.onDeviceRecognition.map { $0 ? "Supported" : "Not supported" } ?? "Unknown")
        row("Wake detections", "\(s.wakeDetections)")
        row("Followed by a request", "\(s.wakesWithRequest)")
        row("Probable false wakes", "\(s.probableFalseWakes)")
        row("Listening (armed)", percent(s.armed, of: s.duration))
        row("Speech end → transcript", latency(recorder.medianSpeechEndToFinal))
        row("Transcript → Hub reply", latency(recorder.medianFinalToReply))
        if let last = recorder.samples.last {
            row("Thermal", last.conditions.thermal.map(String.init(describing:)) ?? "Unknown")
            row(
                "Battery",
                last.conditions.batteryLevel.map { "\(Int(($0 * 100).rounded()))% \(last.conditions.batteryState.rawValue)" }
                    ?? "n/a")
        }
    }

    private func row(_ title: String, _ value: String) -> some View {
        LabeledContent(title) { Text(value).monospacedDigit() }
    }

    private func percent(_ part: TimeInterval, of total: TimeInterval) -> String {
        guard total > 0 else { return "—" }
        return String(format: "%.1f%%", part / total * 100)
    }

    private func latency(_ seconds: TimeInterval?) -> String {
        guard let seconds else { return "—" }
        return "median " + DiagnosticsFormat.latency(ms: Int((seconds * 1000).rounded()))
    }
}
