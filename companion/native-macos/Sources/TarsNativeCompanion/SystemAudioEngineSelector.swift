import Foundation

/// The user-facing choice for the signed menu-bar application.  The
/// standalone `tars-companion` executable intentionally does not consume this
/// value and remains on ScreenCaptureKit.
public enum SystemAudioEnginePreference: String, Equatable, Sendable {
    case automatic
    case processTap = "process-tap"
    case screenCaptureKit = "screen-capture-kit"

    public static func preference(fromLaunchArguments arguments: [String]) throws -> Self {
        try SystemAudioEngineSelector.preference(fromLaunchArguments: arguments)
    }
}

public enum ResolvedSystemAudioEngine: String, Equatable, Sendable {
    case processTap = "process-tap"
    case screenCaptureKit = "screen-capture-kit"
}

public enum SystemAudioEngineSelectionError: Error, Equatable, Sendable, CustomStringConvertible {
    case unsupportedOperatingSystem(OperatingSystemVersion)
    case processTapRequiresMacOS14_2(OperatingSystemVersion)

    public var description: String {
        switch self {
        case .unsupportedOperatingSystem(let version):
            return "O TarsCompanion requer macOS 13.0 ou posterior (encontrado macOS \(version.majorVersion).\(version.minorVersion).\(version.patchVersion))."
        case .processTapRequiresMacOS14_2(let version):
            return "O mecanismo Process Tap requer macOS 14.2 ou posterior (encontrado macOS \(version.majorVersion).\(version.minorVersion).\(version.patchVersion)); selecione ScreenCaptureKit ou atualize o sistema."
        }
    }

    public static func == (lhs: Self, rhs: Self) -> Bool {
        switch (lhs, rhs) {
        case let (.unsupportedOperatingSystem(left), .unsupportedOperatingSystem(right)),
             let (.processTapRequiresMacOS14_2(left), .processTapRequiresMacOS14_2(right)):
            return Self.versionKey(left) == Self.versionKey(right)
        default: return false
        }
    }

    private static func versionKey(_ version: OperatingSystemVersion) -> [Int] {
        [version.majorVersion, version.minorVersion, version.patchVersion]
    }
}

public enum SystemAudioEngineLaunchArgumentError: Error, Equatable, Sendable, CustomStringConvertible {
    case missingValue
    case invalidValue(String)

    public var description: String {
        switch self {
        case .missingValue:
            return "Valor ausente para --system-audio-engine. Use auto, process-tap ou screen-capture-kit."
        case .invalidValue(let value):
            return "Valor inválido para --system-audio-engine: '\(value)'. Use auto, process-tap ou screen-capture-kit."
        }
    }
}

/// Pure policy.  Core Audio is deliberately absent from this type so every
/// supported OS boundary can be tested without touching the HAL or TCC.
public struct SystemAudioEngineSelector: Sendable {
    public let operatingSystemVersion: OperatingSystemVersion

    public init(operatingSystemVersion: OperatingSystemVersion = ProcessInfo.processInfo.operatingSystemVersion) {
        self.operatingSystemVersion = operatingSystemVersion
    }

    public func resolve(_ preference: SystemAudioEnginePreference) throws -> ResolvedSystemAudioEngine {
        try Self.resolve(preference: preference, operatingSystemVersion: operatingSystemVersion)
    }

    public static func resolve(
        preference: SystemAudioEnginePreference,
        operatingSystemVersion: OperatingSystemVersion
    ) throws -> ResolvedSystemAudioEngine {
        guard isAtLeast(operatingSystemVersion, 13, 0, 0) else {
            throw SystemAudioEngineSelectionError.unsupportedOperatingSystem(operatingSystemVersion)
        }

        switch preference {
        case .automatic:
            // The public API starts at 14.2, but product policy waits for
            // 14.4.  There is intentionally no runtime fallback after a tap
            // side effect or failure.
            return isAtLeast(operatingSystemVersion, 14, 4, 0)
                ? .processTap
                : .screenCaptureKit
        case .processTap:
            guard isAtLeast(operatingSystemVersion, 14, 2, 0) else {
                throw SystemAudioEngineSelectionError.processTapRequiresMacOS14_2(operatingSystemVersion)
            }
            return .processTap
        case .screenCaptureKit:
            return .screenCaptureKit
        }
    }

    private static func isAtLeast(_ version: OperatingSystemVersion, _ major: Int, _ minor: Int, _ patch: Int) -> Bool {
        if version.majorVersion != major { return version.majorVersion > major }
        if version.minorVersion != minor { return version.minorVersion > minor }
        return version.patchVersion >= patch
    }

    /// Reads only the signed app's launch arguments.  A missing flag means
    /// automatic selection; a present flag is never silently ignored.
    public static func preference(fromLaunchArguments arguments: [String]) throws -> SystemAudioEnginePreference {
        // Accept both ProcessInfo-style arguments (with an executable at
        // index 0) and the compact arrays used by dependency-injected tests.
        var index = 0
        while index < arguments.count {
            guard arguments[index] == "--system-audio-engine" else {
                index += 1
                continue
            }
            guard index + 1 < arguments.count else {
                throw SystemAudioEngineLaunchArgumentError.missingValue
            }
            let rawValue = arguments[index + 1]
            switch rawValue {
            case "auto": return .automatic
            case "process-tap": return .processTap
            case "screen-capture-kit": return .screenCaptureKit
            default: throw SystemAudioEngineLaunchArgumentError.invalidValue(rawValue)
            }
        }
        return .automatic
    }

    public static func parseLaunchArgument(_ arguments: [String]) throws -> SystemAudioEnginePreference {
        try preference(fromLaunchArguments: arguments)
    }
}
