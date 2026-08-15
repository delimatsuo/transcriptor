import CryptoKit
import Foundation

public enum ReducerEvent: Equatable, Sendable {
    case accepted(AudioFrame)
    case gap(CoverageGap)
}

public struct FrameReducer: Sendable {
    private var activeIdentity: [AudioSource: SourceIdentity] = [:]
    private var tracker = SourceSequenceTracker()
    private var seenEvents: Set<String> = []
    private var frames: [AudioFrame] = []
    private var gaps: [CoverageGap] = []

    public init() {}

    public var acceptedFrames: [AudioFrame] { frames }
    public var recordedGaps: [CoverageGap] { gaps }
    public var acceptedCount: Int { frames.count }

    public mutating func activate(_ identity: SourceIdentity) throws {
        if let current = activeIdentity[identity.source] {
            guard identity.captureGeneration >= current.captureGeneration else {
                throw CompanionError.staleGeneration
            }
            if identity.captureGeneration == current.captureGeneration {
                guard identity == current else { throw CompanionError.invalid("source identity changed within a generation") }
                return
            }
        }
        activeIdentity[identity.source] = identity
        tracker.reset(identity: identity)
    }

    public func identity(for source: AudioSource) -> SourceIdentity? {
        activeIdentity[source]
    }

    @discardableResult
    public mutating func ingest(_ frame: AudioFrame) throws -> ReducerEvent {
        guard let identity = activeIdentity[frame.identity.source] else {
            throw CompanionError.invalid("source generation is not active")
        }
        guard identity == frame.identity else {
            if frame.identity.captureGeneration < identity.captureGeneration { throw CompanionError.staleGeneration }
            throw CompanionError.invalid("frame identity does not match active source")
        }
        guard !seenEvents.contains(frame.eventID) else { throw CompanionError.duplicateFrame }
        try tracker.validate(frame)
        seenEvents.insert(frame.eventID)
        frames.append(frame)
        return .accepted(frame)
    }

    @discardableResult
    public mutating func recordGap(
        identity: SourceIdentity,
        firstSample: UInt64?,
        lastSampleExclusive: UInt64?,
        reason: GapReason
    ) throws -> CoverageGap {
        guard activeIdentity[identity.source] == identity else {
            throw CompanionError.staleGeneration
        }
        if let firstSample, let lastSampleExclusive {
            let expected = tracker.expected(identity: identity).firstSample
            guard firstSample == expected else {
                throw CompanionError.gapRequired("gap does not start at the next provable sample")
            }
            tracker.advance(identity: identity, sequence: nextSequenceAfterGap(identity), firstSample: lastSampleExclusive)
        }
        let gap = try CoverageGap(identity: identity, firstSample: firstSample, lastSampleExclusive: lastSampleExclusive, reason: reason)
        gaps.append(gap)
        return gap
    }

    private func nextSequenceAfterGap(_ identity: SourceIdentity) -> UInt64 {
        tracker.expected(identity: identity).sequence + 1
    }
}

public struct ParsedV2AudioFrame: Equatable, Sendable {
    public let frame: AudioFrame
    public let eventID: String
    public let metadata: Data
}

private let canonicalV2AudioFields: Set<String> = [
    "protocolVersion", "eventType", "sessionId", "streamId", "source",
    "captureGeneration", "eventId", "sequence", "firstSample",
    "lastSampleExclusive", "sampleRateHertz", "channelCount", "durationMs",
    "payloadBytes", "payloadDigestSha256", "encoding"
]

private func v2Digest(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func appendUInt32(_ value: UInt32, to data: inout Data) {
    var big = value.bigEndian
    withUnsafeBytes(of: &big) { data.append(contentsOf: $0) }
}

private func canonicalUInt64(_ name: String, _ value: Any?) throws -> UInt64 {
    guard let text = value as? String, !text.isEmpty,
          text == "0" || (text.first != "0" && text.allSatisfy { $0.isASCII && $0.isNumber }),
          let parsed = UInt64(text), String(parsed) == text else {
        throw CompanionError.invalid("\(name) is not a canonical uint64")
    }
    return parsed
}

private func checkedInteger(_ name: String, _ value: Any?, range: ClosedRange<Int>) throws -> Int {
    guard let number = value as? NSNumber,
          number.doubleValue.rounded(.towardZero) == number.doubleValue,
          number.int64Value >= Int64(range.lowerBound),
          number.int64Value <= Int64(range.upperBound) else {
        throw CompanionError.invalid("\(name) is outside its checked integer domain")
    }
    return Int(number.int64Value)
}

private func canonicalIdentifier(_ name: String, _ value: Any?, maxBytes: Int = 128) throws -> String {
    guard let text = value as? String, !text.isEmpty, text.utf8.count <= maxBytes,
          text == text.precomposedStringWithCanonicalMapping,
          SourceIdentity.isIdentifier(text) else {
        throw CompanionError.invalid("\(name) is not a canonical identifier")
    }
    return text
}

public func v2CanonicalAudioMetadata(_ frame: AudioFrame) throws -> Data {
    guard frame.payload.count <= 64_000 else { throw CompanionError.invalid("audio payload exceeds 64000 bytes") }
    let payloadDigest = v2Digest(frame.payload)
    let text = "{\"captureGeneration\":\"\(frame.identity.captureGeneration)\"," +
        "\"channelCount\":\(frame.identity.channelCount)," +
        "\"durationMs\":\(frame.durationMs)," +
        "\"encoding\":\"pcm_s16le\"," +
        "\"eventId\":\"\(frame.eventID)\"," +
        "\"eventType\":\"audio.chunk\"," +
        "\"firstSample\":\"\(frame.firstSample)\"," +
        "\"lastSampleExclusive\":\"\(frame.lastSampleExclusive)\"," +
        "\"payloadBytes\":\(frame.payload.count)," +
        "\"payloadDigestSha256\":\"\(payloadDigest)\"," +
        "\"protocolVersion\":2," +
        "\"sampleRateHertz\":\(frame.identity.sampleRate)," +
        "\"sequence\":\"\(frame.sequence)\"," +
        "\"sessionId\":\"\(frame.identity.sessionID)\"," +
        "\"source\":\"\(frame.identity.source.rawValue)\"," +
        "\"streamId\":\"\(frame.identity.streamID)\"}"
    let metadata = Data(text.utf8)
    guard metadata.count <= 4_096 else { throw CompanionError.invalid("audio metadata exceeds 4096 bytes") }
    return metadata
}

public func v2EncodeAudioFrame(_ frame: AudioFrame) throws -> Data {
    let metadata = try v2CanonicalAudioMetadata(frame)
    var encoded = Data()
    appendUInt32(UInt32(metadata.count), to: &encoded)
    encoded.append(metadata)
    encoded.append(frame.payload)
    guard encoded.count <= 68_100 else { throw CompanionError.invalid("audio frame exceeds 68100 bytes") }
    return encoded
}

public func v2ParseAudioFrame(_ encoded: Data) throws -> ParsedV2AudioFrame {
    guard encoded.count >= 4, encoded.count <= 68_100 else { throw CompanionError.invalid("audio frame size is invalid") }
    let metadataLength = encoded.prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
    guard metadataLength > 0, metadataLength <= 4_096,
          4 + Int(metadataLength) <= encoded.count else {
        throw CompanionError.invalid("declared metadata length is invalid")
    }
    let metadata = encoded.subdata(in: 4..<(4 + Int(metadataLength)))
    guard let object = try? JSONSerialization.jsonObject(with: metadata),
          let values = object as? [String: Any], Set(values.keys) == canonicalV2AudioFields else {
        throw CompanionError.invalid("audio metadata fields are not exact")
    }
    guard try checkedInteger("protocolVersion", values["protocolVersion"], range: 2...2) == 2,
          try canonicalIdentifier("eventType", values["eventType"], maxBytes: 64) == "audio.chunk",
          values["source"] as? String != nil,
          values["encoding"] as? String == "pcm_s16le" else {
        throw CompanionError.invalid("audio metadata type is invalid")
    }
    let sessionID = try canonicalIdentifier("sessionId", values["sessionId"])
    let streamID = try canonicalIdentifier("streamId", values["streamId"])
    let eventID = try canonicalIdentifier("eventId", values["eventId"])
    let generation = try canonicalUInt64("captureGeneration", values["captureGeneration"])
    let sequence = try canonicalUInt64("sequence", values["sequence"])
    let firstSample = try canonicalUInt64("firstSample", values["firstSample"])
    let lastSample = try canonicalUInt64("lastSampleExclusive", values["lastSampleExclusive"])
    let sampleRate = try checkedInteger("sampleRateHertz", values["sampleRateHertz"], range: 8_000...48_000)
    let channels = try checkedInteger("channelCount", values["channelCount"], range: 1...2)
    let duration = try checkedInteger("durationMs", values["durationMs"], range: 20...250)
    let payloadBytes = try checkedInteger("payloadBytes", values["payloadBytes"], range: 1...64_000)
    guard let sourceText = values["source"] as? String, let source = AudioSource(rawValue: sourceText),
          let digest = values["payloadDigestSha256"] as? String,
          digest.count == 64, digest.allSatisfy({ $0.isASCII && ($0.isNumber || ("a"..."f").contains(String($0))) }) else {
        throw CompanionError.invalid("audio metadata digest or source is invalid")
    }
    let payload = Data(encoded.suffix(from: 4 + Int(metadataLength)))
    guard payload.count == payloadBytes, v2Digest(payload) == digest else {
        throw CompanionError.invalid("payload length or digest mismatch")
    }
    let identity = try SourceIdentity(sessionID: sessionID, streamID: streamID, captureGeneration: generation, source: source, sampleRate: sampleRate, channelCount: channels)
    let frame = try AudioFrame(identity: identity, sequence: sequence, firstSample: firstSample, capturedAtMs: 0, payload: payload)
    guard frame.lastSampleExclusive == lastSample,
          frame.durationMs == UInt64(duration), eventID == frame.eventID,
          try v2CanonicalAudioMetadata(frame) == metadata else {
        throw CompanionError.invalid("metadata is not the canonical typed encoding")
    }
    return ParsedV2AudioFrame(frame: frame, eventID: eventID, metadata: metadata)
}

public func v2RetryCommitment(sessionKey: Data, metadata: Data, payload: Data) throws -> Data {
    guard sessionKey.count >= 32, metadata.count <= 4_096, payload.count <= 64_000 else {
        throw CompanionError.invalid("retry commitment input is outside bounds")
    }
    var canonicalFrame = Data()
    appendUInt32(UInt32(metadata.count), to: &canonicalFrame)
    canonicalFrame.append(metadata)
    canonicalFrame.append(payload)
    _ = try v2ParseAudioFrame(canonicalFrame)
    var message = Data("tars-retry-v2\0".utf8)
    appendUInt32(UInt32(metadata.count), to: &message)
    message.append(metadata)
    appendUInt32(UInt32(payload.count), to: &message)
    message.append(payload)
    return Data(HMAC<SHA256>.authenticationCode(for: message, using: SymmetricKey(data: sessionKey)))
}
