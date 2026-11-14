import CocoKit
import SwiftUI

/// The router: one screen per `ViewSpec`, a connection banner on top, Settings behind a gear
/// (or a long press anywhere).
struct ContentView: View {
    let model: AppModel
    @State private var showingSettings = false
    @State private var showingDiagnostics = false

    var body: some View {
        ZStack {
            Color(.systemBackground).ignoresSafeArea()
            ScreenView(view: model.currentView, idleInfo: model.idleInfo)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .contentShape(Rectangle())
                .onLongPressGesture(minimumDuration: 1.5) { showingSettings = true }
        }
        .overlay(alignment: .top) {
            ConnectionBanner(state: model.connection) { showingSettings = true }
                .padding(.top, 12)
                .animation(.default, value: model.connection)
        }
        .overlay(alignment: .topTrailing) {
            Button {
                showingSettings = true
            } label: {
                Image(systemName: "gearshape")
                    .font(.title2)
                    .padding(20)
                    .foregroundStyle(.secondary)
            }
            .accessibilityLabel("Settings")
        }
        .sheet(isPresented: $showingSettings) {
            SettingsView(settings: model.settings) { settings in
                try model.saveSettings(settings)
            }
        }
        .sheet(isPresented: $showingDiagnostics) { DiagnosticsSheet() }
        .environment(\.openDiagnostics, OpenDiagnosticsAction { showingDiagnostics = true })
        .conversationLayer(model: model)
        // Topmost: a ringing timer covers every screen state (#18).
        .overlay { AlarmOverlay().animation(.default, value: model.timerStore.isAlarmRinging) }
        .onChange(of: model.timerStore.isAlarmRinging) { _, ringing in
            if ringing {
                showingSettings = false
                showingDiagnostics = false
            }
        }
        .environment(model)
        .environment(model.timerStore)
        .environment(model.idleModel)
    }
}

/// Placeholder screens; later issues replace each case with the real view.
struct ScreenView: View {
    let view: ViewSpec
    let idleInfo: IdleInfo?

    var body: some View {
        switch view {
        case .idle:
            IdleScreen()
        case .timers:
            TimersScreen()
        case .answer(let text):
            AnswerScreen(text: text)
        case .picture(let payload):
            PictureScreen(payload: payload)
        case .sound(let payload):
            SoundScreen(payload: payload)
        case .youtube(let videoID, let title):
            YouTubeScreen(videoID: videoID, title: title)
        case .nowPlaying:
            NowPlayingScreen()
        default:
            Text(view.type)
                .font(.system(size: 48, weight: .semibold, design: .rounded))
                .foregroundStyle(.secondary)
        }
    }
}

/// Non-blocking status pill: hidden when connected, tappable to open Settings otherwise.
struct ConnectionBanner: View {
    let state: ConnectionState
    let openSettings: () -> Void

    var body: some View {
        if let message {
            Button(action: openSettings) {
                Label(message.text, systemImage: message.icon)
                    .font(.title3.weight(.medium))
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    .background(.thinMaterial, in: Capsule())
            }
            .buttonStyle(.plain)
            .transition(.move(edge: .top).combined(with: .opacity))
        }
    }

    private var message: (icon: String, text: String)? {
        switch state {
        case .connected: nil
        case .searching: ("magnifyingglass", "Looking for Coco Hub…")
        case .connecting: ("antenna.radiowaves.left.and.right", "Connecting to Coco Hub…")
        case .unreachable(let reason): ("wifi.exclamationmark", "Can't reach Coco Hub. \(reason)")
        case .unauthorized: ("lock", "Coco Hub needs a valid token. Tap to open Settings.")
        }
    }
}

/// Never shows the saved token (anyone at the kitchen counter can open Settings): the field
/// starts empty, empty keeps the saved token, and changing the host or port requires re-entering
/// it so the saved token is never sent to a different Hub.
struct SettingsView: View {
    let saved: HubSettings
    let save: (HubSettings) throws -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var host: String
    @State private var port: String
    @State private var newToken = ""
    @State private var error: String?

    init(settings: HubSettings, save: @escaping (HubSettings) throws -> Void) {
        self.saved = settings
        self.save = save
        _host = State(initialValue: settings.manualHost ?? "")
        _port = State(initialValue: String(settings.port))
    }

    private var portNumber: Int? {
        Int(port).flatMap { (1...65535).contains($0) ? $0 : nil }
    }

    /// `nil` while the port is invalid.
    private var edit: Result<HubSettings, SettingsEditError>? {
        portNumber.map { saved.applyingEdit(host: host, port: $0, newToken: newToken) }
    }

    private var tokenRequired: Bool {
        if case .failure(.tokenRequired)? = edit { true } else { false }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Find automatically", text: $host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    TextField("Port", text: $port)
                        .keyboardType(.numberPad)
                } header: {
                    Text("Hub host")
                } footer: {
                    Text("Leave the host empty to find Coco Hub with Bonjour. The port is used with a manual host.")
                }
                Section {
                    SecureField(
                        saved.hasToken ? "Enter a new token to replace it" : "Shared token from Hub settings",
                        text: $newToken
                    )
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                } header: {
                    Text("Token")
                } footer: {
                    if tokenRequired {
                        Text("You changed the Hub, so enter its token again.")
                            .foregroundStyle(.red)
                    } else if saved.hasToken {
                        Label("Token saved. Leave empty to keep it.", systemImage: "checkmark.seal")
                    }
                }
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
                Section {
                    NavigationLink("Diagnostics…") { DiagnosticsScreen() }
                }
            }
            .navigationTitle("Coco Hub")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save", action: submit)
                        .disabled(tokenRequired)
                }
            }
        }
    }

    private func submit() {
        guard let edit else {
            error = "Port must be a number from 1 to 65535."
            return
        }
        guard case .success(let settings) = edit else {
            error = "Enter the token for the new Hub."
            return
        }
        do {
            try save(settings)
            dismiss()
        } catch {
            self.error = "Couldn't save settings: \(error.localizedDescription)"
        }
    }
}
