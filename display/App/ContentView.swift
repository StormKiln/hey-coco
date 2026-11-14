import CocoKit
import SwiftUI

/// The router: one screen per `ViewSpec`, a connection banner on top, Settings behind a gear
/// (or a long press anywhere).
struct ContentView: View {
    let model: AppModel
    @State private var showingSettings = false

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
    }
}

/// Placeholder screens; later issues replace each case with the real view.
struct ScreenView: View {
    let view: ViewSpec
    let idleInfo: IdleInfo?

    var body: some View {
        switch view {
        case .idle:
            IdleView(idleInfo: idleInfo)
        case .answer(let text):
            Text(text)
                .font(.system(size: 56, weight: .semibold, design: .rounded))
                .multilineTextAlignment(.center)
                .minimumScaleFactor(0.3)
                .padding(48)
        default:
            Text(view.type)
                .font(.system(size: 48, weight: .semibold, design: .rounded))
                .foregroundStyle(.secondary)
        }
    }
}

struct IdleView: View {
    let idleInfo: IdleInfo?

    var body: some View {
        VStack(spacing: 16) {
            TimelineView(.everyMinute) { context in
                Text(context.date, format: .dateTime.hour().minute())
                    .font(.system(size: 160, weight: .light, design: .rounded))
                    .monospacedDigit()
            }
            Text(subtitle)
                .font(.system(size: 40, design: .rounded))
                .foregroundStyle(.secondary)
        }
    }

    private var subtitle: String {
        guard let now = idleInfo?.weather?.current else { return "hey coco" }
        return "\(Int(now.temp.rounded()))°\(now.unit.rawValue) · \(now.description)"
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

struct SettingsView: View {
    let save: (HubSettings) throws -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var host: String
    @State private var port: String
    @State private var token: String
    @State private var error: String?

    init(settings: HubSettings, save: @escaping (HubSettings) throws -> Void) {
        self.save = save
        _host = State(initialValue: settings.manualHost ?? "")
        _port = State(initialValue: String(settings.port))
        _token = State(initialValue: settings.token ?? "")
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
                Section("Token") {
                    SecureField("Shared token from Hub settings", text: $token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Coco Hub")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save", action: submit)
                }
            }
        }
    }

    private func submit() {
        guard let portNumber = Int(port), (1...65535).contains(portNumber) else {
            error = "Port must be a number from 1 to 65535."
            return
        }
        do {
            try save(HubSettings(manualHost: host, port: portNumber, token: token))
            dismiss()
        } catch {
            self.error = "Couldn't save settings: \(error.localizedDescription)"
        }
    }
}
