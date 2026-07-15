import CryptoKit
import Foundation

public enum ProtocolValidationError: Error, Equatable {
    case invalid(String)
}

public enum Source: String, Codable, Sendable {
    case microphone
    case systemAudio = "system_audio"
}

public enum TerminalKind: String, Codable, Sendable {
    case transcript
    case gap
}

private func isASCIIAlphaNumeric(_ byte: UInt8) -> Bool {
    (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte)
}

private func validateIdentifier(_ name: String, _ value: String) throws {
    let bytes = Array(value.utf8)
    guard !bytes.isEmpty, bytes.count <= 128, isASCIIAlphaNumeric(bytes[0]) else {
        throw ProtocolValidationError.invalid("\(name) is not a valid protocol identifier")
    }
    let punctuation = Set("._:-".utf8)
    guard bytes.dropFirst().allSatisfy({ isASCIIAlphaNumeric($0) || punctuation.contains($0) }) else {
        throw ProtocolValidationError.invalid("\(name) is not a valid protocol identifier")
    }
}

private func validateNonNegative(_ name: String, _ value: Int) throws {
    guard value >= 0 else {
        throw ProtocolValidationError.invalid("\(name) must be non-negative")
    }
}

private func validateWallClock(_ value: String) throws {
    guard !value.isEmpty, value.utf8.count <= 40 else {
        throw ProtocolValidationError.invalid("capturedAtWallClock is invalid")
    }
    let formatter = ISO8601DateFormatter()
    guard formatter.date(from: value) != nil else {
        throw ProtocolValidationError.invalid("capturedAtWallClock is invalid")
    }
}

private func validateSHA256(_ name: String, _ value: String) throws {
    let bytes = Array(value.utf8)
    guard bytes.count == 64,
          bytes.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else {
        throw ProtocolValidationError.invalid("\(name) must be a lowercase SHA-256 digest")
    }
}

private func canonicalDigest(prefix: String, fields: [String]) throws -> String {
    let allFields = [prefix] + fields
    guard allFields.allSatisfy({ !$0.contains("\0") }) else {
        throw ProtocolValidationError.invalid("canonical identity fields may not contain NUL")
    }
    let payload = allFields.joined(separator: "\0")
    let digest = SHA256.hash(data: Data(payload.utf8))
    return digest.map { String(format: "%02x", $0) }.joined()
}

public struct StreamKey: Equatable, Hashable, Sendable {
    public let sessionId: String
    public let streamId: String
    public let captureGeneration: Int
    public let source: Source

    public init(
        sessionId: String,
        streamId: String,
        captureGeneration: Int,
        source: Source
    ) throws {
        try validateIdentifier("sessionId", sessionId)
        try validateIdentifier("streamId", streamId)
        try validateNonNegative("captureGeneration", captureGeneration)
        self.sessionId = sessionId
        self.streamId = streamId
        self.captureGeneration = captureGeneration
        self.source = source
    }
}

public struct KnownCoverage: Equatable, Sendable {
    public let key: StreamKey
    public let firstSequence: Int
    public let lastSequenceInclusive: Int
    public let firstSample: Int
    public let lastSampleExclusive: Int
    public let firstCapturedAtMonotonicNs: Int
    public let lastCapturedAtMonotonicNs: Int

    public init(
        key: StreamKey,
        firstSequence: Int,
        lastSequenceInclusive: Int,
        firstSample: Int,
        lastSampleExclusive: Int,
        firstCapturedAtMonotonicNs: Int,
        lastCapturedAtMonotonicNs: Int
    ) throws {
        try validateNonNegative("firstSequence", firstSequence)
        try validateNonNegative("lastSequenceInclusive", lastSequenceInclusive)
        try validateNonNegative("firstSample", firstSample)
        try validateNonNegative("firstCapturedAtMonotonicNs", firstCapturedAtMonotonicNs)
        try validateNonNegative("lastCapturedAtMonotonicNs", lastCapturedAtMonotonicNs)
        guard lastSequenceInclusive >= firstSequence else {
            throw ProtocolValidationError.invalid("sequence coverage is reversed")
        }
        guard lastSampleExclusive > firstSample else {
            throw ProtocolValidationError.invalid("sample coverage must be non-empty")
        }
        guard lastCapturedAtMonotonicNs >= firstCapturedAtMonotonicNs else {
            throw ProtocolValidationError.invalid("capture-time coverage is reversed")
        }
        self.key = key
        self.firstSequence = firstSequence
        self.lastSequenceInclusive = lastSequenceInclusive
        self.firstSample = firstSample
        self.lastSampleExclusive = lastSampleExclusive
        self.firstCapturedAtMonotonicNs = firstCapturedAtMonotonicNs
        self.lastCapturedAtMonotonicNs = lastCapturedAtMonotonicNs
    }

    public var coverageId: String {
        get throws {
            "cov_" + (try canonicalDigest(
                prefix: "tars-coverage-v1",
                fields: [
                    key.sessionId,
                    key.streamId,
                    String(key.captureGeneration),
                    key.source.rawValue,
                    String(firstSequence),
                    String(lastSequenceInclusive),
                    String(firstSample),
                    String(lastSampleExclusive),
                ]
            ))
        }
    }
}

public struct UnknownEndCoverage: Equatable, Sendable {
    public let key: StreamKey
    public let firstSequence: Int
    public let firstSample: Int
    public let firstCapturedAtMonotonicNs: Int

    public init(
        key: StreamKey,
        firstSequence: Int,
        firstSample: Int,
        firstCapturedAtMonotonicNs: Int
    ) throws {
        try validateNonNegative("firstSequence", firstSequence)
        try validateNonNegative("firstSample", firstSample)
        try validateNonNegative("firstCapturedAtMonotonicNs", firstCapturedAtMonotonicNs)
        self.key = key
        self.firstSequence = firstSequence
        self.firstSample = firstSample
        self.firstCapturedAtMonotonicNs = firstCapturedAtMonotonicNs
    }

    public var identityToken: String {
        "unknown:\(key.sessionId):\(key.streamId):\(key.captureGeneration):\(key.source.rawValue):\(firstSequence):\(firstSample)"
    }
}

public func deterministicEventId(
    eventType: String,
    key: StreamKey,
    identity: String
) throws -> String {
    try validateIdentifier("eventType", eventType)
    guard eventType.utf8.count <= 64 else {
        throw ProtocolValidationError.invalid("eventType exceeds 64 characters")
    }
    try validateIdentifier("eventIdentity", identity)
    return "evt_" + (try canonicalDigest(
        prefix: "tars-event-v1",
        fields: [
            key.sessionId,
            key.streamId,
            String(key.captureGeneration),
            key.source.rawValue,
            eventType,
            identity,
        ]
    ))
}

public func deterministicTerminalId(
    kind: TerminalKind,
    coverageToken: String,
    resultOrdinal: Int
) throws -> String {
    try validateNonNegative("resultOrdinal", resultOrdinal)
    return "term_" + (try canonicalDigest(
        prefix: "tars-terminal-v1",
        fields: [kind.rawValue, coverageToken, String(resultOrdinal)]
    ))
}

public struct KnownCoverageMetadata: Codable, Equatable, Sendable {
    public let boundaryStatus: String
    public let coverageId: String
    public let source: Source
    public let firstSequence: Int
    public let lastSequenceInclusive: Int
    public let firstSample: Int
    public let lastSampleExclusive: Int
    public let firstCapturedAtMonotonicNs: Int
    public let lastCapturedAtMonotonicNs: Int

    public func validated(key: StreamKey) throws -> KnownCoverage {
        guard boundaryStatus == "known", source == key.source else {
            throw ProtocolValidationError.invalid("known coverage boundary or source is invalid")
        }
        let coverage = try KnownCoverage(
            key: key,
            firstSequence: firstSequence,
            lastSequenceInclusive: lastSequenceInclusive,
            firstSample: firstSample,
            lastSampleExclusive: lastSampleExclusive,
            firstCapturedAtMonotonicNs: firstCapturedAtMonotonicNs,
            lastCapturedAtMonotonicNs: lastCapturedAtMonotonicNs
        )
        guard try coverage.coverageId == coverageId else {
            throw ProtocolValidationError.invalid("coverageId does not match canonical identity")
        }
        return coverage
    }
}

public struct UnknownEndCoverageMetadata: Codable, Equatable, Sendable {
    public let boundaryStatus: String
    public let source: Source
    public let firstSequence: Int
    public let firstSample: Int
    public let firstCapturedAtMonotonicNs: Int

    public func validated(key: StreamKey) throws -> UnknownEndCoverage {
        guard boundaryStatus == "unknown_end", source == key.source else {
            throw ProtocolValidationError.invalid("unknown coverage boundary or source is invalid")
        }
        return try UnknownEndCoverage(
            key: key,
            firstSequence: firstSequence,
            firstSample: firstSample,
            firstCapturedAtMonotonicNs: firstCapturedAtMonotonicNs
        )
    }
}

public enum CoverageMetadata: Codable, Equatable, Sendable {
    case known(KnownCoverageMetadata)
    case unknownEnd(UnknownEndCoverageMetadata)

    private enum BoundaryKey: String, CodingKey {
        case boundaryStatus
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: BoundaryKey.self)
        switch try container.decode(String.self, forKey: .boundaryStatus) {
        case "known":
            self = .known(try KnownCoverageMetadata(from: decoder))
        case "unknown_end":
            self = .unknownEnd(try UnknownEndCoverageMetadata(from: decoder))
        default:
            throw ProtocolValidationError.invalid("coverage boundary status is invalid")
        }
    }

    public func encode(to encoder: Encoder) throws {
        switch self {
        case .known(let coverage):
            try coverage.encode(to: encoder)
        case .unknownEnd(let coverage):
            try coverage.encode(to: encoder)
        }
    }
}

public struct AudioChunkMetadata: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let eventType: String
    public let sessionId: String
    public let streamId: String
    public let deviceId: String
    public let captureGeneration: Int
    public let eventId: String
    public let capturedAtMonotonicNs: Int
    public let capturedAtWallClock: String
    public let source: Source
    public let sequence: Int
    public let firstSample: Int
    public let lastSampleExclusive: Int
    public let sampleRateHertz: Int
    public let channelCount: Int
    public let encoding: String
    public let durationMs: Int
    public let payloadBytes: Int
    public let payloadDigestSha256: String

    public func validate() throws {
        guard protocolVersion == 1, eventType == "audio.chunk" else {
            throw ProtocolValidationError.invalid("audio protocol version or event type is invalid")
        }
        try validateIdentifier("deviceId", deviceId)
        try validateNonNegative("sequence", sequence)
        try validateNonNegative("firstSample", firstSample)
        try validateNonNegative("capturedAtMonotonicNs", capturedAtMonotonicNs)
        try validateWallClock(capturedAtWallClock)
        try validateSHA256("payloadDigestSha256", payloadDigestSha256)
        guard (8_000...48_000).contains(sampleRateHertz),
              (1...2).contains(channelCount),
              (20...1_000).contains(durationMs),
              (1...64_000).contains(payloadBytes),
              encoding == "pcm_s16le" else {
            throw ProtocolValidationError.invalid("audio bounds or encoding are invalid")
        }
        let bytesPerFrame = 2 * channelCount
        guard payloadBytes % bytesPerFrame == 0 else {
            throw ProtocolValidationError.invalid("payload frame alignment is invalid")
        }
        let frames = payloadBytes / bytesPerFrame
        guard lastSampleExclusive - firstSample == frames,
              frames * 1_000 == durationMs * sampleRateHertz else {
            throw ProtocolValidationError.invalid("sample and duration coverage is invalid")
        }
        let key = try StreamKey(
            sessionId: sessionId,
            streamId: streamId,
            captureGeneration: captureGeneration,
            source: source
        )
        guard try deterministicEventId(
            eventType: eventType,
            key: key,
            identity: String(sequence)
        ) == eventId else {
            throw ProtocolValidationError.invalid("audio eventId is not canonical")
        }
        guard try JSONEncoder().encode(self).count <= 65_536 else {
            throw ProtocolValidationError.invalid("audio metadata exceeds the control bound")
        }
    }
}

public struct TerminalOutcomeMetadata: Codable, Equatable, Sendable {
    public let protocolVersion: Int
    public let eventType: String
    public let sessionId: String
    public let streamId: String
    public let deviceId: String
    public let captureGeneration: Int
    public let eventId: String
    public let capturedAtMonotonicNs: Int
    public let capturedAtWallClock: String
    public let source: Source
    public let outcome: TerminalKind
    public let coverage: CoverageMetadata
    public let sttAttemptGeneration: Int
    public let resultOrdinal: Int
    public let reasonCode: String?

    public func validate() throws {
        guard protocolVersion == 1 else {
            throw ProtocolValidationError.invalid("terminal protocol version is invalid")
        }
        try validateIdentifier("deviceId", deviceId)
        try validateNonNegative("capturedAtMonotonicNs", capturedAtMonotonicNs)
        try validateNonNegative("sttAttemptGeneration", sttAttemptGeneration)
        try validateNonNegative("resultOrdinal", resultOrdinal)
        try validateWallClock(capturedAtWallClock)
        let key = try StreamKey(
            sessionId: sessionId,
            streamId: streamId,
            captureGeneration: captureGeneration,
            source: source
        )

        let token: String
        switch coverage {
        case .known(let metadata):
            let known = try metadata.validated(key: key)
            token = try known.coverageId
        case .unknownEnd(let metadata):
            token = try metadata.validated(key: key).identityToken
        }

        switch outcome {
        case .transcript:
            guard eventType == "transcript.final", reasonCode == nil else {
                throw ProtocolValidationError.invalid("transcript terminal semantics are invalid")
            }
            guard case .known = coverage else {
                throw ProtocolValidationError.invalid("transcript requires known coverage")
            }
        case .gap:
            guard eventType == "capture.gap", let reasonCode else {
                throw ProtocolValidationError.invalid("gap terminal semantics are invalid")
            }
            try validateIdentifier("reasonCode", reasonCode)
            guard reasonCode.utf8.count <= 64 else {
                throw ProtocolValidationError.invalid("reasonCode exceeds 64 characters")
            }
        }

        guard try deterministicTerminalId(
            kind: outcome,
            coverageToken: token,
            resultOrdinal: resultOrdinal
        ) == eventId else {
            throw ProtocolValidationError.invalid("terminal eventId is not canonical")
        }
        guard try JSONEncoder().encode(self).count <= 65_536 else {
            throw ProtocolValidationError.invalid("terminal metadata exceeds the control bound")
        }
    }
}
