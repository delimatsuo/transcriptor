// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "TarsPhase1A",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "TarsPhase1A", targets: ["TarsPhase1A"]),
    ],
    targets: [
        .target(name: "TarsPhase1A"),
        .testTarget(name: "TarsPhase1ATests", dependencies: ["TarsPhase1A"]),
    ]
)
