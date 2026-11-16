import AVFoundation
import SwiftUI
import UIKit

/// Camera QR scanner for pairing (#89), on AVFoundation's `AVCaptureMetadataOutput`. Not
/// VisionKit's DataScannerViewController: that needs an A12 or later, and the kitchen iPad (7th
/// generation) has an A10. Calls `found` once with the first QR code's text.
///
/// No camera (the simulator) or no permission: says so and points to Enter code. It never
/// changes any setting itself.
struct QRScannerView: View {
    let found: (String) -> Void

    enum Access: Equatable { case checking, granted, denied, unavailable }

    @State private var access: Access = .checking
    @State private var position: AVCaptureDevice.Position = .back

    var body: some View {
        Group {
            switch access {
            case .checking:
                ProgressView()
            case .granted:
                QRCameraPreview(position: position, found: found)
                    .id(position)
                    .overlay(alignment: .bottomTrailing) {
                        if Self.camera(at: position.flipped) != nil {
                            Button {
                                position = position.flipped
                            } label: {
                                Label("Switch camera", systemImage: "arrow.triangle.2.circlepath.camera")
                                    .labelStyle(.iconOnly)
                                    .font(.title2)
                                    .padding(14)
                                    .background(.thinMaterial, in: Circle())
                            }
                            .padding(16)
                        }
                    }
            case .denied:
                unavailable(
                    "Camera access is off",
                    "To scan, allow the camera for Coco in the Settings app (Privacy & Security, Camera). Or use Enter code instead.")
            case .unavailable:
                unavailable("No camera available", "Use Enter code instead.")
            }
        }
        .task { access = await Self.checkAccess() }
    }

    private func unavailable(_ title: String, _ detail: String) -> some View {
        ContentUnavailableView(title, systemImage: "camera.badge.ellipsis", description: Text(detail))
    }

    static func camera(at position: AVCaptureDevice.Position) -> AVCaptureDevice? {
        AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: position)
    }

    private static func checkAccess() async -> Access {
        guard AVCaptureDevice.default(for: .video) != nil else { return .unavailable }
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return .granted
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .video) ? .granted : .denied
        default: return .denied
        }
    }
}

extension AVCaptureDevice.Position {
    fileprivate var flipped: Self { self == .front ? .back : .front }
}

private struct QRCameraPreview: UIViewControllerRepresentable {
    let position: AVCaptureDevice.Position
    let found: (String) -> Void

    func makeUIViewController(context: Context) -> QRScannerController {
        QRScannerController(position: position, found: found)
    }

    func updateUIViewController(_ controller: QRScannerController, context: Context) {}

    static func dismantleUIViewController(_ controller: QRScannerController, coordinator: ()) {
        controller.stop()
    }
}

/// Owns the capture session; reports the first QR code on the main queue.
final class QRScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    private let position: AVCaptureDevice.Position
    private let found: (String) -> Void
    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "coco.qr-scanner")
    private var preview: AVCaptureVideoPreviewLayer?
    private var rotation: AVCaptureDevice.RotationCoordinator?
    private var reported = false

    init(position: AVCaptureDevice.Position, found: @escaping (String) -> Void) {
        self.position = position
        self.found = found
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        guard let device = QRScannerView.camera(at: position) ?? AVCaptureDevice.default(for: .video),
            let input = try? AVCaptureDeviceInput(device: device), session.canAddInput(input)
        else { return }
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        if output.availableMetadataObjectTypes.contains(.qr) {
            output.metadataObjectTypes = [.qr]
        }
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        preview = layer
        rotation = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: layer)
        nonisolated(unsafe) let session = session
        sessionQueue.async { session.startRunning() }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview?.frame = view.bounds
        if let rotation, let connection = preview?.connection {
            let angle = rotation.videoRotationAngleForHorizonLevelPreview
            if connection.isVideoRotationAngleSupported(angle) { connection.videoRotationAngle = angle }
        }
    }

    func stop() {
        nonisolated(unsafe) let session = session
        sessionQueue.async { session.stopRunning() }
    }

    nonisolated func metadataOutput(
        _ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
        from connection: AVCaptureConnection
    ) {
        let text = metadataObjects
            .compactMap { ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }
            .first
        guard let text else { return }
        // The delegate queue is .main.
        MainActor.assumeIsolated {
            guard !reported else { return }
            reported = true
            stop()
            found(text)
        }
    }
}
