import CocoKit
import SwiftUI
import UIKit

/// Opens the hidden diagnostics screen (#45), like `OpenURLAction`.
struct OpenDiagnosticsAction {
    var action: @MainActor () -> Void = {}
    @MainActor func callAsFunction() { action() }
}

extension EnvironmentValues {
    /// Set by `ContentView`.
    @Entry var openDiagnostics = OpenDiagnosticsAction()
}

/// Hidden diagnostics (#45): this iPad (versions, Hub address and connection, wake word, last
/// error) and the Hub's `/v1/diagnostics` (version, uptime, service health, recent utterances).
/// Opened by tapping the idle clock 5 times, or from Settings. Never shows the Hub token.
struct DiagnosticsScreen: View {
    @Environment(AppModel.self) private var model
    @Environment(VoiceLoop.self) private var voice: VoiceLoop?
    @State private var diagnostics = DiagnosticsModel()

    var body: some View {
        List {
            displaySection
            if let voice { VoiceTestSection(recorder: voice.voiceTest, wakeWord: voice.wakeWord, device: deviceLine) }
            hubSection
            if !diagnostics.healthRows.isEmpty { healthSection }
            if diagnostics.diagnostics != nil { utterancesSection }
        }
        .navigationTitle("Diagnostics")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await diagnostics.load(using: model.hubClient) }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(diagnostics.state == .loading)
            }
        }
        .task { await diagnostics.load(using: model.hubClient) }
    }

    // MARK: - This iPad

    private var displaySection: some View {
        Section("Coco Display") {
            row("App", "\(Self.appVersion) (\(Self.appBuild))")
            row("iPadOS", UIDevice.current.systemVersion)
            row("Device", Self.deviceModel)
            row("Hub address", DiagnosticsFormat.address(model.endpoint))
            row("Connection", DiagnosticsFormat.connection(model.connection))
            row("Wake word", DiagnosticsFormat.wakeWord(voice?.wakeWord))
            row("Last error", model.lastError.map(DiagnosticsFormat.error) ?? "None")
        }
    }

    // MARK: - Hub

    private var hubSection: some View {
        Section {
            switch diagnostics.state {
            case .loading where diagnostics.diagnostics == nil, .idle:
                HStack { ProgressView(); Text("Loading…").foregroundStyle(.secondary) }
            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle").foregroundStyle(.red)
            default:
                EmptyView()
            }
            if let d = diagnostics.diagnostics {
                row("Version", diagnostics.versionLine ?? d.version)
                row("Uptime", diagnostics.uptime ?? "—")
                row("Model", d.settings.qwen.enabled ? d.settings.qwen.model : "Off")
                row("Timezone", d.settings.timezone)
                row("Transcripts logged", d.settings.logTranscripts ? "Yes" : "No")
            }
        } header: {
            Text("Coco Hub")
        }
    }

    private var healthSection: some View {
        Section("Health") {
            ForEach(diagnostics.healthRows) { health in
                HStack(spacing: 14) {
                    Circle().fill(Self.color(health.status)).frame(width: 14, height: 14)
                    Text(health.name).font(.headline).frame(width: 110, alignment: .leading)
                    Text(health.status.rawValue)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Self.color(health.status))
                        .frame(width: 70, alignment: .leading)
                    Text(health.summary).foregroundStyle(.secondary)
                }
            }
        }
    }

    private var utterancesSection: some View {
        Section("Recent utterances") {
            if diagnostics.utteranceRows.isEmpty {
                Text("Nothing heard yet").foregroundStyle(.secondary)
            }
            ForEach(diagnostics.utteranceRows) { u in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(u.text).font(.headline)
                        Spacer()
                        Text(u.when).foregroundStyle(.secondary)
                    }
                    Text("\(u.source) · \(u.intent) · \(u.matchedBy) · \(u.latency)")
                        .font(.subheadline.monospaced())
                        .foregroundStyle(.secondary)
                    if let error = u.error {
                        Text(error).font(.subheadline).foregroundStyle(.red)
                    }
                }
                .padding(.vertical, 2)
            }
        }
    }

    // MARK: - Helpers

    private func row(_ title: String, _ value: String) -> some View {
        LabeledContent(title) {
            Text(value).textSelection(.enabled).multilineTextAlignment(.trailing)
        }
    }

    private static func color(_ status: CheckStatus) -> Color {
        switch status {
        case .ok: .green
        case .warn: .orange
        case .error: .red
        case .off, .unknown: .gray
        }
    }

    private static var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
    }

    private static var appBuild: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
    }

    /// For the voice test report, e.g. "iPad (iPad7,11), iPadOS 18.6, app 1.0 (12)".
    private var deviceLine: String {
        "\(Self.deviceModel), iPadOS \(UIDevice.current.systemVersion), app \(Self.appVersion) (\(Self.appBuild))"
    }

    /// The hardware identifier, e.g. "iPad7,11" (the simulator reports its host's).
    private static var deviceModel: String {
        var info = utsname()
        uname(&info)
        let identifier = withUnsafeBytes(of: &info.machine) { buffer in
            String(decoding: buffer.prefix { $0 != 0 }, as: UTF8.self)
        }
        return "\(UIDevice.current.model) (\(identifier))"
    }
}

/// `DiagnosticsScreen` on its own (from the idle clock), with a Done button.
struct DiagnosticsSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            DiagnosticsScreen()
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Done") { dismiss() }
                    }
                }
        }
    }
}
