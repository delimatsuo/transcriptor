// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "TarsNativeCompanion",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "TarsNativeCompanion", targets: ["TarsNativeCompanion"]),
        .executable(name: "tars-companion", targets: ["TarsCompanionCLI"]),
        .executable(name: "TarsCompanionApp", targets: ["TarsCompanionApp"])
    ],
    targets: [
        .target(
            name: "TarsRealtimeAudioBridge",
            path: "Sources/TarsRealtimeAudioBridge",
            publicHeadersPath: "include",
            cSettings: [
                .unsafeFlags(["-Werror=function-effects"])
            ],
            linkerSettings: [
                .linkedFramework("CoreAudio")
            ]
        ),
        .target(
            name: "TarsNativeCompanion",
            dependencies: ["TarsRealtimeAudioBridge"],
            path: "Sources/TarsNativeCompanion"
        ),
        .executableTarget(
            name: "TarsCompanionCLI",
            dependencies: ["TarsNativeCompanion"],
            path: "Sources/TarsCompanionCLI"
        ),
        .executableTarget(
            name: "TarsCompanionApp",
            dependencies: ["TarsNativeCompanion"],
            path: "Sources/TarsCompanionApp"
        ),
        .testTarget(
            name: "TarsNativeCompanionTests",
            dependencies: ["TarsNativeCompanion"],
            path: "Tests/TarsNativeCompanionTests"
        )
    ]
)
