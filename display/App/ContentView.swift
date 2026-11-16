import CocoKit
import SwiftUI

/// The router: one screen per `ViewSpec`, a connection banner on top, Settings behind a gear
/// (or a long press anywhere).
struct ContentView: View {
    let model: AppModel
    @State private var showingSettings = false
    @State private var showingDiagnostics = false
    /// The Hub waiting for the person to confirm its fingerprint (#54).
    @State private var pairing: PairingCandidate?

    var body: some View {
        ZStack {
            Color(.systemBackground).ignoresSafeArea()
            ScreenView(view: model.currentView, idleInfo: model.idleInfo)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .contentShape(Rectangle())
                .onLongPressGesture(minimumDuration: 1.5) { showingSettings = true }
        }
        .overlay(alignment: .top) {
            ConnectionBanner(state: model.connection, unreachableManualHost: model.unreachableManualHost) {
                if case .needsPairing(let candidate) = model.connection {
                    pairing = candidate
                } else {
                    showingSettings = true
                }
            }
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
            SettingsView(
                settings: model.settings,
                save: { settings in try model.saveSettings(settings) },
                forgetHub: { try model.forgetHub() },
                candidate: {
                    if case .needsPairing(let candidate) = model.connection { candidate } else { nil }
                }(),
                pairWithCode: { candidate, code throws(PairingError) in
                    try await model.pair(with: candidate, code: code)
                },
                pairScanned: { uri throws(PairingError) in try await model.pair(scanned: uri) })
        }
        .sheet(item: $pairing) { candidate in
            PairingView(
                candidate: candidate,
                pair: { try model.pair(with: candidate) },
                pairWithCode: { code throws(PairingError) in try await model.pair(with: candidate, code: code) },
                pairScanned: { uri throws(PairingError) in try await model.pair(scanned: uri) })
        }
        // Ask as soon as an unpaired Hub turns up (or Settings closes on one). "Not now" leaves
        // the banner, which asks again when tapped.
        .onChange(of: model.connection) { _, state in
            if case .needsPairing(let candidate) = state {
                if !showingSettings { pairing = candidate }
            } else {
                pairing = nil
            }
        }
        .onChange(of: showingSettings) { _, showing in
            if !showing, case .needsPairing(let candidate) = model.connection { pairing = candidate }
        }
        .sheet(isPresented: $showingDiagnostics) { DiagnosticsSheet() }
        .environment(\.openDiagnostics, OpenDiagnosticsAction { showingDiagnostics = true })
        .conversationLayer(model: model)
        // Below the alarm: a ringing timer shows (and wakes the screen) even at night (#24).
        .nightMode(model: model)
        // Topmost: a ringing timer covers every screen state (#18).
        .overlay { AlarmOverlay().animation(.default, value: model.timerStore.isAlarmRinging) }
        .onChange(of: model.timerStore.isAlarmRinging) { _, ringing in
            if ringing {
                showingSettings = false
                showingDiagnostics = false
                pairing = nil
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
        case .weather(let payload):
            WeatherScreen(payload: payload)
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
    /// Set while a manually entered host doesn't answer (`AppModel.unreachableManualHost`).
    var unreachableManualHost: String? = nil
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
        if let host = unreachableManualHost {
            return ("wifi.exclamationmark", ConnectionState.manualHostUnreachableMessage(host: host))
        }
        return switch state {
        case .connected: nil
        case .searching: ("magnifyingglass", "Looking for Coco Hub…")
        case .connecting: ("antenna.radiowaves.left.and.right", "Connecting to Coco Hub…")
        case .unreachable(let reason): ("wifi.exclamationmark", "Can't reach Coco Hub. \(reason)")
        case .unauthorized: ("lock", "Coco Hub needs a valid token. Tap to open Settings.")
        case .needsPairing: ("link", "Found Coco Hub. Tap to pair.")
        case .untrustedHub(let host):
            (
                "exclamationmark.shield",
                "Unknown Coco Hub at \(host). Ignoring it. To re-pair, tap and choose Forget Hub."
            )
        }
    }
}

/// "Pair with Coco Hub?" (#54, #89). `make hub-pair` on the Mac shows a QR code and a one-time
/// code. Scan QR code pairs with the Hub presenting the scanned certificate; Enter code sends the
/// code to the Hub whose fingerprint the person checked. "It matches. Pair" (fingerprint only,
/// then the token typed in Settings) stays as the last resort. Only after pairing is the Hub
/// pinned and the token sent to it.
struct PairingView: View {
    let candidate: PairingCandidate
    let pair: () throws -> Void
    let pairWithCode: (String) async throws(PairingError) -> Void
    let pairScanned: (PairingURI) async throws(PairingError) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var mode = Mode.choose
    @State private var error: String?

    private enum Mode { case choose, scan, code }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 28) {
                    Image(systemName: "lock.shield")
                        .font(.system(size: 64))
                        .foregroundStyle(.tint)
                    Text("Pair with Coco Hub?")
                        .font(.largeTitle.weight(.semibold))
                    switch mode {
                    case .choose: choose
                    case .scan: ScanPairingView(pairScanned: pairScanned) { dismiss() }
                    case .code:
                        EnterCodeView(fingerprint: candidate.pin.fingerprint, pairWithCode: pairWithCode) {
                            dismiss()
                        }
                    }
                }
                .padding(40)
                .frame(maxWidth: 640)
                .frame(maxWidth: .infinity)
            }
            .toolbar {
                if mode != .choose {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Back") { mode = .choose }
                    }
                }
            }
        }
    }

    private var choose: some View {
        VStack(spacing: 24) {
            Text("On the Mac, run `make hub-pair`, then scan its QR code or enter its 6-digit code.")
                .font(.title3)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            HStack(spacing: 20) {
                Button {
                    mode = .scan
                } label: {
                    Label("Scan QR code", systemImage: "qrcode.viewfinder")
                }
                .buttonStyle(.borderedProminent)
                Button {
                    mode = .code
                } label: {
                    Label("Enter code", systemImage: "number")
                }
                .buttonStyle(.bordered)
            }
            .controlSize(.large)
            Divider()
            Text("Or, with the token already entered in Settings: pair if `make hub-fingerprint` shows exactly")
                .font(.callout)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Text(candidate.pin.fingerprint)
                .font(.system(size: 28, weight: .semibold, design: .monospaced))
                .lineLimit(1)
                .minimumScaleFactor(0.5)
                .textSelection(.enabled)
                .accessibilityLabel(
                    "Fingerprint \(candidate.pin.fingerprint.map { String($0) }.joined(separator: " "))")
            Text("Hub at \(candidate.address.host), ID \(candidate.pin.hubID)")
                .font(.callout)
                .foregroundStyle(.secondary)
            if let error {
                Text(error).foregroundStyle(.red)
            }
            HStack(spacing: 20) {
                Button("Not now", role: .cancel) { dismiss() }
                    .buttonStyle(.bordered)
                Button("It matches. Pair") {
                    do {
                        try pair()
                        dismiss()
                    } catch {
                        self.error = "Couldn't save the pairing: \(error.localizedDescription)"
                    }
                }
                .buttonStyle(.bordered)
            }
            Text("If a fingerprint doesn't match, tap Not now: another device is pretending to be Coco Hub.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
    }
}

/// Never shows the saved token (anyone at the kitchen counter can open Settings): the field
/// starts empty, empty keeps the saved token, and changing the host or port requires re-entering
/// it so the saved token is never sent to a different Hub.
struct SettingsView: View {
    let saved: HubSettings
    let save: (HubSettings) throws -> Void
    let forgetHub: () throws -> Void
    /// An unpaired Hub Coco found: what Enter code pairs with (#89).
    let candidate: PairingCandidate?
    let pairWithCode: (PairingCandidate, String) async throws(PairingError) -> Void
    let pairScanned: (PairingURI) async throws(PairingError) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var host: String
    @State private var port: String
    @State private var newToken = ""
    @State private var error: String?
    @State private var confirmingForget = false

    init(
        settings: HubSettings, save: @escaping (HubSettings) throws -> Void,
        forgetHub: @escaping () throws -> Void,
        candidate: PairingCandidate?,
        pairWithCode: @escaping (PairingCandidate, String) async throws(PairingError) -> Void,
        pairScanned: @escaping (PairingURI) async throws(PairingError) -> Void
    ) {
        self.saved = settings
        self.save = save
        self.forgetHub = forgetHub
        self.candidate = candidate
        self.pairWithCode = pairWithCode
        self.pairScanned = pairScanned
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
                    NavigationLink {
                        ScanPairingView(pairScanned: pairScanned) { dismiss() }
                            .padding(24)
                            .navigationTitle("Scan QR code")
                    } label: {
                        Label("Scan QR code", systemImage: "qrcode.viewfinder")
                    }
                    if let candidate {
                        NavigationLink {
                            EnterCodeView(fingerprint: candidate.pin.fingerprint) { code throws(PairingError) in
                                try await pairWithCode(candidate, code)
                            } done: {
                                dismiss()
                            }
                            .padding(24)
                            .navigationTitle("Enter code")
                        } label: {
                            Label("Enter code", systemImage: "number")
                        }
                    } else {
                        Label("Enter code", systemImage: "number").foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Pair")
                } footer: {
                    Text("On the Mac, run make hub-pair. Enter code is available once Coco finds an unpaired Coco Hub (after Forget Hub, if this iPad was paired). Typing the token below is the last resort.")
                }
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
                Section {
                    if let pin = saved.pin {
                        LabeledContent("Fingerprint") {
                            Text(pin.fingerprint).font(.body.monospaced())
                        }
                        LabeledContent("Hub ID") { Text(pin.hubID).font(.body.monospaced()) }
                    } else {
                        Text("Not paired. Coco asks you to pair when it finds Coco Hub, or use Scan QR code above.")
                            .foregroundStyle(.secondary)
                    }
                    Button("Forget Hub and Re-pair…", role: .destructive) { confirmingForget = true }
                        .disabled(saved.pin == nil && !saved.hasToken)
                } header: {
                    Text("Paired Hub")
                } footer: {
                    Text("Use this after reinstalling Coco Hub or moving it to another Mac, then pair again with make hub-pair.")
                }
                if let error {
                    Section { Text(error).foregroundStyle(.red) }
                }
                Section {
                    NavigationLink("Diagnostics…") { DiagnosticsScreen() }
                }
            }
            .navigationTitle("Coco Hub")
            .confirmationDialog(
                "Forget this Coco Hub?", isPresented: $confirmingForget, titleVisibility: .visible
            ) {
                Button("Forget Hub", role: .destructive) {
                    do {
                        try forgetHub()
                        dismiss()
                    } catch {
                        self.error = "Couldn't forget the Hub: \(error.localizedDescription)"
                    }
                }
            } message: {
                Text("Coco stops trusting it and deletes the saved token. Then pair again: run make hub-pair on the Mac and scan its QR code or enter its code.")
            }
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
