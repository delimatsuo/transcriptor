import CoreFoundation
import CryptoKit
import Foundation

public struct V2AudioFrameInput: Equatable, Sendable {
    public let key: V2StreamKey
    public let sequence: UInt64
    public let firstSample: UInt64
    public let lastSampleExclusive: UInt64
    public let sampleRateHertz: Int
    public let channelCount: Int
    public let durationMs: Int
    public let payload: Data

    public init(
        key: V2StreamKey,
        sequence: UInt64,
        firstSample: UInt64,
        lastSampleExclusive: UInt64,
        sampleRateHertz: Int,
        channelCount: Int,
        durationMs: Int,
        payload: Data
    ) throws {
        guard lastSampleExclusive > firstSample else {
            throw ProtocolV2ValidationError.invalid("audio sample range is empty")
        }
        guard (8_000...48_000).contains(sampleRateHertz), (1...2).contains(channelCount),
              (20...250).contains(durationMs) else {
            throw ProtocolV2ValidationError.invalid("audio format is outside v2 bounds")
        }
        let frames = lastSampleExclusive - firstSample
        guard frames <= UInt64(Int.max / (channelCount * 2)),
              payload.count == Int(frames) * channelCount * 2,
              payload.count > 0, payload.count <= 64_000,
              frames * 1_000 == UInt64(durationMs * sampleRateHertz),
              frames <= min(96_000, UInt64(2 * sampleRateHertz)) else {
            throw ProtocolV2ValidationError.invalid("audio payload does not match bounded format")
        }
        self.key = key
        self.sequence = sequence
        self.firstSample = firstSample
        self.lastSampleExclusive = lastSampleExclusive
        self.sampleRateHertz = sampleRateHertz
        self.channelCount = channelCount
        self.durationMs = durationMs
        self.payload = payload
    }
}

public struct V2ParsedAudioFrame: Equatable, Sendable {
    public let input: V2AudioFrameInput
    public let eventId: String
    public let canonicalMetadata: Data
}

private let v2AudioFields: Set<String> = [
    "protocolVersion", "eventType", "sessionId", "streamId", "source",
    "captureGeneration", "eventId", "sequence", "firstSample",
    "lastSampleExclusive", "sampleRateHertz", "channelCount", "durationMs",
    "payloadBytes", "payloadDigestSha256", "encoding",
]

private func v2FramingDigest(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

private func v2AppendFrameUInt32(_ value: UInt32, to data: inout Data) {
    var big = value.bigEndian
    withUnsafeBytes(of: &big) { data.append(contentsOf: $0) }
}

private func v2CanonicalUInt64(_ name: String, _ value: Any?) throws -> UInt64 {
    guard let text = value as? String, !text.isEmpty,
          text == "0" || (text.first != "0" && text.allSatisfy({ $0.isASCII && $0.isNumber })),
          let parsed = UInt64(text), String(parsed) == text else {
        throw ProtocolV2ValidationError.invalid("\(name) is not canonical uint64")
    }
    return parsed
}

private func v2CheckedJSONInteger(_ name: String, _ value: Any?, range: ClosedRange<Int>) throws -> Int {
    guard let number = value as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID(),
          number.doubleValue.rounded(.towardZero) == number.doubleValue,
          number.int64Value >= Int64(range.lowerBound),
          number.int64Value <= Int64(range.upperBound) else {
        throw ProtocolV2ValidationError.invalid("\(name) is outside its checked integer domain")
    }
    return Int(number.int64Value)
}

private func v2FramingIdentifier(_ name: String, _ value: Any?, maxBytes: Int = 128) throws -> String {
    guard let text = value as? String, !text.utf8.isEmpty, text.utf8.count <= maxBytes,
          text == text.precomposedStringWithCanonicalMapping, !text.contains("\0") else {
        throw ProtocolV2ValidationError.invalid("\(name) is not a canonical identifier")
    }
    let bytes = Array(text.utf8)
    func alphanumeric(_ byte: UInt8) -> Bool {
        (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte)
    }
    let punctuation = Set("._:-".utf8)
    guard alphanumeric(bytes[0]), bytes.dropFirst().allSatisfy({ alphanumeric($0) || punctuation.contains($0) }) else {
        throw ProtocolV2ValidationError.invalid("\(name) is not a canonical identifier")
    }
    return text
}

public func v2AudioEventId(
    key: V2StreamKey,
    sequence: UInt64,
    firstSample: UInt64,
    lastSampleExclusive: UInt64
) throws -> String {
    guard lastSampleExclusive > firstSample else {
        throw ProtocolV2ValidationError.invalid("audio event sample range is empty")
    }
    let fields = [
        "tars-audio-event-v2", key.sessionId, key.streamId,
        String(key.captureGeneration), key.source.rawValue, String(sequence),
        String(firstSample), String(lastSampleExclusive),
    ]
    return "aevt_" + v2FramingDigest(Data(fields.joined(separator: "\0").utf8))
}

public func v2CanonicalAudioMetadata(_ input: V2AudioFrameInput) throws -> Data {
    let eventId = try v2AudioEventId(
        key: input.key,
        sequence: input.sequence,
        firstSample: input.firstSample,
        lastSampleExclusive: input.lastSampleExclusive
    )
    let payloadDigest = v2FramingDigest(input.payload)
    let text = "{\"captureGeneration\":\"\(input.key.captureGeneration)\"," +
        "\"channelCount\":\(input.channelCount)," +
        "\"durationMs\":\(input.durationMs)," +
        "\"encoding\":\"pcm_s16le\"," +
        "\"eventId\":\"\(eventId)\"," +
        "\"eventType\":\"audio.chunk\"," +
        "\"firstSample\":\"\(input.firstSample)\"," +
        "\"lastSampleExclusive\":\"\(input.lastSampleExclusive)\"," +
        "\"payloadBytes\":\(input.payload.count)," +
        "\"payloadDigestSha256\":\"\(payloadDigest)\"," +
        "\"protocolVersion\":2," +
        "\"sampleRateHertz\":\(input.sampleRateHertz)," +
        "\"sequence\":\"\(input.sequence)\"," +
        "\"sessionId\":\"\(input.key.sessionId)\"," +
        "\"source\":\"\(input.key.source.rawValue)\"," +
        "\"streamId\":\"\(input.key.streamId)\"}"
    let metadata = Data(text.utf8)
    guard metadata.count <= 4_096 else {
        throw ProtocolV2ValidationError.invalid("audio metadata exceeds 4096 bytes")
    }
    return metadata
}

public func v2EncodeAudioFrame(_ input: V2AudioFrameInput) throws -> Data {
    let metadata = try v2CanonicalAudioMetadata(input)
    var frame = Data()
    v2AppendFrameUInt32(UInt32(metadata.count), to: &frame)
    frame.append(metadata)
    frame.append(input.payload)
    guard frame.count <= 68_100 else {
        throw ProtocolV2ValidationError.invalid("audio frame exceeds 68100 bytes")
    }
    return frame
}

public func v2ParseAudioFrame(_ frame: Data) throws -> V2ParsedAudioFrame {
    guard frame.count >= 4, frame.count <= 68_100 else {
        throw ProtocolV2ValidationError.invalid("audio frame size is invalid")
    }
    let metadataLength = frame.prefix(4).reduce(UInt32(0)) { ($0 << 8) | UInt32($1) }
    guard metadataLength > 0, metadataLength <= 4_096,
          4 + Int(metadataLength) <= frame.count else {
        throw ProtocolV2ValidationError.invalid("declared metadata length is invalid")
    }
    let metadata = frame.subdata(in: 4..<(4 + Int(metadataLength)))
    let object: Any
    do {
        object = try JSONSerialization.jsonObject(with: metadata)
    } catch {
        throw ProtocolV2ValidationError.invalid("metadata is not valid JSON")
    }
    guard let values = object as? [String: Any], Set(values.keys) == v2AudioFields else {
        throw ProtocolV2ValidationError.invalid("audio metadata fields are not exact")
    }
    guard try v2CheckedJSONInteger("protocolVersion", values["protocolVersion"], range: 2...2) == 2,
          try v2FramingIdentifier("eventType", values["eventType"], maxBytes: 64) == "audio.chunk",
          let sourceText = values["source"] as? String,
          let source = V2Source(rawValue: sourceText),
          values["encoding"] as? String == "pcm_s16le" else {
        throw ProtocolV2ValidationError.invalid("audio metadata type is invalid")
    }
    let sessionId = try v2FramingIdentifier("sessionId", values["sessionId"])
    let streamId = try v2FramingIdentifier("streamId", values["streamId"])
    let eventId = try v2FramingIdentifier("eventId", values["eventId"])
    let captureGeneration = try v2CanonicalUInt64("captureGeneration", values["captureGeneration"])
    let sequence = try v2CanonicalUInt64("sequence", values["sequence"])
    let firstSample = try v2CanonicalUInt64("firstSample", values["firstSample"])
    let lastSampleExclusive = try v2CanonicalUInt64("lastSampleExclusive", values["lastSampleExclusive"])
    let sampleRate = try v2CheckedJSONInteger("sampleRateHertz", values["sampleRateHertz"], range: 8_000...48_000)
    let channelCount = try v2CheckedJSONInteger("channelCount", values["channelCount"], range: 1...2)
    let durationMs = try v2CheckedJSONInteger("durationMs", values["durationMs"], range: 20...250)
    let payloadBytes = try v2CheckedJSONInteger("payloadBytes", values["payloadBytes"], range: 1...64_000)
    guard let digest = values["payloadDigestSha256"] as? String,
          digest.count == 64,
          digest.allSatisfy({ $0.isASCII && ($0.isNumber || ("a"..."f").contains(String($0))) }) else {
        throw ProtocolV2ValidationError.invalid("payload digest is invalid")
    }
    let payload = frame.suffix(from: 4 + Int(metadataLength))
    guard payload.count == payloadBytes, v2FramingDigest(payload) == digest else {
        throw ProtocolV2ValidationError.invalid("payload length or digest mismatch")
    }
    let key = try V2StreamKey(
        sessionId: sessionId,
        streamId: streamId,
        captureGeneration: captureGeneration,
        source: source
    )
    let input = try V2AudioFrameInput(
        key: key,
        sequence: sequence,
        firstSample: firstSample,
        lastSampleExclusive: lastSampleExclusive,
        sampleRateHertz: sampleRate,
        channelCount: channelCount,
        durationMs: durationMs,
        payload: Data(payload)
    )
    let expectedEventId = try v2AudioEventId(
        key: key,
        sequence: sequence,
        firstSample: firstSample,
        lastSampleExclusive: lastSampleExclusive
    )
    guard eventId == expectedEventId, try v2CanonicalAudioMetadata(input) == metadata else {
        throw ProtocolV2ValidationError.invalid("metadata is not the canonical typed encoding")
    }
    return V2ParsedAudioFrame(input: input, eventId: eventId, canonicalMetadata: metadata)
}

public func v2RetryCommitment(sessionKey: Data, metadata: Data, payload: Data) throws -> Data {
    guard sessionKey.count >= 32, metadata.count <= 4_096, payload.count <= 64_000 else {
        throw ProtocolV2ValidationError.invalid("retry commitment input is outside bounds")
    }
    var message = Data("tars-retry-v2\0".utf8)
    v2AppendFrameUInt32(UInt32(metadata.count), to: &message)
    message.append(metadata)
    v2AppendFrameUInt32(UInt32(payload.count), to: &message)
    message.append(payload)
    let authentication = HMAC<SHA256>.authenticationCode(for: message, using: SymmetricKey(data: sessionKey))
    return Data(authentication)
}
