// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TarsNativeCompanion",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "TarsNativeCompanion", targets: ["TarsNativeCompanion"]),
        .executable(name: "tars-companion", targets: ["TarsCompanionCLI"])
    ],
    targets: [
        .target(
            name: "TarsNativeCompanion",
            path: "Sources/TarsNativeCompanion"
        ),
        .executableTarget(
            name: "TarsCompanionCLI",
            dependencies: ["TarsNativeCompanion"],
            path: "Sources/TarsCompanionCLI"
        ),
        .testTarget(
            name: "TarsNativeCompanionTests",
            dependencies: ["TarsNativeCompanion"],
            path: "Tests/TarsNativeCompanionTests"
        )
    ]
)
