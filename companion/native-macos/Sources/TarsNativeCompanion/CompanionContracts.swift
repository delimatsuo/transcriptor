import CryptoKit
import Foundation

public enum CompanionError: Error, Equatable, Sendable, CustomStringConvertible {
    case invalid(String)
    case staleGeneration
    case duplicateFrame
    case gapRequired(String)
    case custodyLimitExceeded
    case invalidTransition(String)
    case deletionInProgress
    case callbackFenced
    case nativeCaptureRequiresSeparateAuthorization

    public var description: String {
        switch self {
        case .invalid(let message): return message
        case .staleGeneration: return "stale capture generation"
        case .duplicateFrame: return "duplicate frame"
        case .gapRequired(let message): return "gap required: \(message)"
        case .custodyLimitExceeded: return "custody limit exceeded"
        case .invalidTransition(let message): return "invalid transition: \(message)"
        case .deletionInProgress: return "deletion is in progress"
        case .callbackFenced: return "callback is fenced"
        case .nativeCaptureRequiresSeparateAuthorization:
            return "native capture requires separate authorization"
        }
    }
}

public enum AudioSource: String, CaseIterable, Codable, Hashable, Sendable {
    case microphone
    case systemAudio = "system_audio"
}

public enum PermissionState: String, Codable, Sendable {
    case unknown
    case granted
    case denied
    case revoked
}

public enum RouteState: String, Codable, Sendable {
    case unknown
    case healthy
    case unavailable
    case ambiguous
    case changed
}

public enum InterruptionState: String, Codable, Sendable {
    case clear
    case interrupted
}

public enum SleepState: String, Codable, Sendable {
    case awake
    case sleeping
    case woke
}

public struct SourceHealth: Equatable, Codable, Sendable {
    public var permission: PermissionState
    public var route: RouteState
    public var interruption: InterruptionState
    public var sleep: SleepState
    public var overflowed: Bool
    public var deviceIdentity: String?

    public init(
        permission: PermissionState = .unknown,
        route: RouteState = .unknown,
        interruption: InterruptionState = .clear,
        sleep: SleepState = .awake,
        overflowed: Bool = false,
        deviceIdentity: String? = nil
    ) {
        self.permission = permission
        self.route = route
        self.interruption = interruption
        self.sleep = sleep
        self.overflowed = overflowed
        self.deviceIdentity = deviceIdentity
    }

    public var isHealthy: Bool {
        permission == .granted && route == .healthy && interruption == .clear && sleep != .sleeping && !overflowed
    }
}

public struct SourceIdentity: Equatable, Hashable, Codable, Sendable {
    public let sessionID: String
    public let streamID: String
    public let captureGeneration: UInt64
    public let source: AudioSource
    public let sampleRate: Int
    public let channelCount: Int

    public init(
        sessionID: String,
        streamID: String,
        captureGeneration: UInt64,
        source: AudioSource,
        sampleRate: Int,
        channelCount: Int
    ) throws {
        guard Self.isIdentifier(sessionID), Self.isIdentifier(streamID) else {
            throw CompanionError.invalid("session and stream identifiers must be ASCII identifiers")
        }
        guard (8_000...48_000).contains(sampleRate), (1...2).contains(channelCount) else {
            throw CompanionError.invalid("sample rate or channel count is outside the offline bounds")
        }
        self.sessionID = sessionID
        self.streamID = streamID
        self.captureGeneration = captureGeneration
        self.source = source
        self.sampleRate = sampleRate
        self.channelCount = channelCount
    }

    public var key: String {
        "\(sessionID):\(streamID):\(captureGeneration):\(source.rawValue)"
    }

    public static func isIdentifier(_ value: String) -> Bool {
        let bytes = Array(value.utf8)
        guard let first = bytes.first, bytes.count <= 128 else { return false }
        guard (48...57).contains(first) || (65...90).contains(first) || (97...122).contains(first) else {
            return false
        }
        return bytes.dropFirst().allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45, 46, 58, 95].contains($0)
        }
    }
}

public struct AudioFrame: Equatable, Sendable {
    public let identity: SourceIdentity
    public let sequence: UInt64
    public let firstSample: UInt64
    public let capturedAtMs: UInt64
    public var payload: Data

    public init(
        identity: SourceIdentity,
        sequence: UInt64,
        firstSample: UInt64,
        capturedAtMs: UInt64,
        payload: Data
    ) throws {
        let bytesPerSample = identity.channelCount * 2
        guard !payload.isEmpty, payload.count % bytesPerSample == 0 else {
            throw CompanionError.invalid("frame payload is not aligned to channel samples")
        }
        let sampleCount = payload.count / bytesPerSample
        let durationNumerator = sampleCount * 1_000
        guard (20...250).contains(durationNumerator / identity.sampleRate),
              durationNumerator % identity.sampleRate == 0 else {
            throw CompanionError.invalid("frame duration is outside the canonical 20-250 ms bounds")
        }
        self.identity = identity
        self.sequence = sequence
        self.firstSample = firstSample
        self.capturedAtMs = capturedAtMs
        self.payload = payload
    }

    public var sampleCount: UInt64 { UInt64(payload.count / (identity.channelCount * 2)) }
    public var lastSampleExclusive: UInt64 { firstSample + sampleCount }
    public var durationMs: UInt64 { sampleCount * 1_000 / UInt64(identity.sampleRate) }

    public var eventID: String {
        let fields = [
            "tars-audio-event-v2", identity.sessionID, identity.streamID,
            String(identity.captureGeneration), identity.source.rawValue,
            String(sequence), String(firstSample), String(lastSampleExclusive)
        ]
        let data = Data(fields.joined(separator: "\0").utf8)
        return "aevt_" + SHA256.hash(data: data).hexString
    }
}

public struct CoverageRange: Equatable, Hashable, Sendable {
    public let identity: SourceIdentity
    public let sequence: UInt64
    public let firstSample: UInt64
    public let lastSampleExclusive: UInt64

    public init(frame: AudioFrame) {
        self.identity = frame.identity
        self.sequence = frame.sequence
        self.firstSample = frame.firstSample
        self.lastSampleExclusive = frame.lastSampleExclusive
    }

    public var coverageID: String {
        var data = Data("tars-atomic-coverage-v2\0".utf8)
        data.append(contentsOf: identity.key.utf8)
        data.append(0)
        appendUInt64(sequence, to: &data)
        appendUInt64(firstSample, to: &data)
        appendUInt64(lastSampleExclusive, to: &data)
        return "acov_" + SHA256.hash(data: data).hexString
    }
}

public enum GapReason: String, Codable, Sendable {
    case overflow
    case routeLoss = "route_loss"
    case permissionRevoked = "permission_revoked"
    case forcedTermination = "forced_termination"
    case localPrivacyDiscard = "local_privacy_discard"
    case privacyTimeout = "privacy_timeout"
    case unknownEnd = "unknown_end"
    case staleGeneration = "stale_generation"
}

public struct CoverageGap: Equatable, Sendable {
    public let identity: SourceIdentity
    public let firstSample: UInt64?
    public let lastSampleExclusive: UInt64?
    public let reason: GapReason

    public init(identity: SourceIdentity, firstSample: UInt64?, lastSampleExclusive: UInt64?, reason: GapReason) throws {
        if let firstSample, let lastSampleExclusive {
            guard lastSampleExclusive > firstSample else {
                throw CompanionError.invalid("gap range is empty")
            }
        }
        self.identity = identity
        self.firstSample = firstSample
        self.lastSampleExclusive = lastSampleExclusive
        self.reason = reason
    }
}

public enum PhysicalCaptureState: String, Codable, Sendable {
    case setupRequired = "setup_required"
    case checkingPermissionsAndDevices = "checking_permissions_and_devices"
    case readyBothSources = "ready_both_sources"
    case starting
    case capturing
    case paused
    case stopping
    case stopped
    case degraded
    case deleting
}

public enum TransportState: String, Codable, Sendable {
    case disconnected
    case connecting
    case open
    case draining
    case closed
}

public enum CoverageState: String, Codable, Sendable {
    case notStarted = "not_started"
    case open
    case finalizing
    case completed
    case completedWithGaps = "completed_with_gaps"
    case deleteQuiescing = "delete_quiescing"
    case deleted
    case deletionFailed = "deletion_failed"
}

public struct CompanionSnapshot: Equatable, Sendable {
    public let physical: PhysicalCaptureState
    public let transport: TransportState
    public let coverage: CoverageState
    public let sourceHealth: [AudioSource: SourceHealth]
    public let acceptedFrames: Int
    public let pendingCallbacks: Int
}

public struct DiagnosticEvent: Equatable, Sendable {
    public let code: String
    public let source: AudioSource?
    public let generation: UInt64?
    public let sequence: UInt64?

    public init(code: String, source: AudioSource? = nil, generation: UInt64? = nil, sequence: UInt64? = nil) {
        self.code = code
        self.source = source
        self.generation = generation
        self.sequence = sequence
    }
}

private func appendUInt64(_ value: UInt64, to data: inout Data) {
    var big = value.bigEndian
    withUnsafeBytes(of: &big) { data.append(contentsOf: $0) }
}

private extension SHA256.Digest {
    var hexString: String {
        map { String(format: "%02x", $0) }.joined()
    }
}
