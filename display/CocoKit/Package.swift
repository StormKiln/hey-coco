// swift-tools-version:6.0
import PackageDescription

let package = Package(
    name: "CocoKit",
    platforms: [.iOS(.v18), .macOS(.v15)],
    products: [
        .library(name: "CocoKit", targets: ["CocoKit"]),
    ],
    targets: [
        .target(name: "CocoKit"),
        .testTarget(name: "CocoKitTests", dependencies: ["CocoKit"]),
    ]
)
