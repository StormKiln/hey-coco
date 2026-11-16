import CocoKit
import SwiftUI

/// Scan the QR code `make hub-pair` prints, then pair with the Hub presenting that certificate
/// (#89). No fingerprint to compare: the QR code carries the full certificate hash.
struct ScanPairingView: View {
    let pairScanned: (PairingURI) async throws(PairingError) -> Void
    let done: () -> Void
    @State private var attempt = 0
    @State private var working = false
    @State private var error: String?

    var body: some View {
        VStack(spacing: 20) {
            Text("On the Mac, run `make hub-pair` and point the camera at the QR code it shows.")
                .font(.title3)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            ZStack {
                QRScannerView { text in Task { await scanned(text) } }
                    .id(attempt)
                if working {
                    ProgressView("Pairing…")
                        .padding(24)
                        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
                }
            }
            .frame(maxWidth: .infinity, minHeight: 320, maxHeight: 480)
            .clipShape(RoundedRectangle(cornerRadius: 20))
            if let error {
                Text(error).foregroundStyle(.red).multilineTextAlignment(.center)
                Button("Scan again") {
                    self.error = nil
                    attempt += 1
                }
                .buttonStyle(.bordered)
            }
        }
    }

    private func scanned(_ text: String) async {
        let uri: PairingURI
        do {
            uri = try PairingURI.parse(text)
        } catch {
            self.error = error.message
            return
        }
        working = true
        defer { working = false }
        do {
            try await pairScanned(uri)
            done()
        } catch {
            self.error = error.message
        }
    }
}

/// Type the one-time 6-digit code `make hub-pair` shows, after checking the fingerprint (#89).
struct EnterCodeView: View {
    let fingerprint: String
    let pairWithCode: (String) async throws(PairingError) -> Void
    let done: () -> Void
    @State private var code = ""
    @State private var working = false
    @State private var error: String?
    @FocusState private var focused: Bool

    private var valid: Bool { PairingCode.normalize(code) != nil }

    var body: some View {
        VStack(spacing: 20) {
            Text("On the Mac, run `make hub-pair`. Check it shows this fingerprint:")
                .font(.title3)
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Text(fingerprint)
                .font(.system(size: 32, weight: .semibold, design: .monospaced))
                .lineLimit(1)
                .minimumScaleFactor(0.5)
                .accessibilityLabel("Fingerprint \(fingerprint.map { String($0) }.joined(separator: " "))")
            Text("If it matches, type the 6-digit code it shows:")
                .font(.title3)
                .foregroundStyle(.secondary)
            TextField("123 456", text: $code)
                .keyboardType(.numberPad)
                .textContentType(.oneTimeCode)
                .font(.system(size: 44, weight: .semibold, design: .monospaced))
                .multilineTextAlignment(.center)
                .frame(maxWidth: 280)
                .textFieldStyle(.roundedBorder)
                .focused($focused)
                .onChange(of: code) { _, typed in
                    let digits = String(typed.filter { ("0"..."9").contains($0) }.prefix(6))
                    if digits != typed { code = digits }
                }
                .onSubmit { if valid { Task { await submit() } } }
            if let error {
                Text(error).foregroundStyle(.red).multilineTextAlignment(.center)
            }
            Button {
                Task { await submit() }
            } label: {
                if working { ProgressView() } else { Text("Pair") }
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(!valid || working)
            Text("If the fingerprint doesn't match, don't enter the code: another device is pretending to be Coco Hub.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
        }
        .onAppear { focused = true }
    }

    private func submit() async {
        working = true
        defer { working = false }
        error = nil
        do {
            try await pairWithCode(code)
            done()
        } catch {
            self.error = error.message
            code = ""
        }
    }
}
