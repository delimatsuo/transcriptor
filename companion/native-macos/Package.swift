// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TarsNativeCompanion",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "TarsNativeCompanion", targets: ["TarsNativeCompanion"])
    ],
    targets: [
        .target(
            name: "TarsNativeCompanion",
            path: "Sources/TarsNativeCompanion"
        ),
        .testTarget(
            name: "TarsNativeCompanionTests",
            dependencies: ["TarsNativeCompanion"],
            path: "Tests/TarsNativeCompanionTests"
        )
    ]
)
