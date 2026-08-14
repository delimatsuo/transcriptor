import CryptoKit
import Foundation

public enum ProtocolV2ValidationError: Error, Equatable {
    case invalid(String)
}

public enum V2Source: String, Sendable {
    case microphone
    case systemAudio = "system_audio"
}

private func v2ValidateIdentifier(_ name: String, _ value: String) throws {
    let bytes = Array(value.utf8)
    guard !bytes.isEmpty, bytes.count <= 128 else {
        throw ProtocolV2ValidationError.invalid("\(name) is not a valid identifier")
    }
    let first = bytes[0]
    guard (48...57).contains(first) || (65...90).contains(first) || (97...122).contains(first) else {
        throw ProtocolV2ValidationError.invalid("\(name) is not a valid identifier")
    }
    let punctuation = Set("._:-".utf8)
    guard bytes.dropFirst().allSatisfy({
        (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || punctuation.contains($0)
    }) else {
        throw ProtocolV2ValidationError.invalid("\(name) is not a valid identifier")
    }
}

private func v2ValidateString(_ name: String, _ value: String, identifier: Bool = false) throws {
    guard !value.contains("\0"), value == value.precomposedStringWithCanonicalMapping else {
        throw ProtocolV2ValidationError.invalid("\(name) must be NUL-free NFC")
    }
    guard UInt64(value.utf8.count) <= UInt64(UInt32.max) else {
        throw ProtocolV2ValidationError.invalid("\(name) exceeds uint32 byte length")
    }
    if identifier { try v2ValidateIdentifier(name, value) }
}

private func v2AppendUInt32(_ value: UInt32, to data: inout Data) {
    var big = value.bigEndian
    withUnsafeBytes(of: &big) { data.append(contentsOf: $0) }
}

private func v2AppendUInt64(_ value: UInt64, to data: inout Data) {
    var big = value.bigEndian
    withUnsafeBytes(of: &big) { data.append(contentsOf: $0) }
}

private func v2AppendLengthPrefixed(_ value: String, name: String, to data: inout Data) throws {
    try v2ValidateString(name, value)
    v2AppendUInt32(UInt32(value.utf8.count), to: &data)
    data.append(contentsOf: value.utf8)
}

private func v2IdentityPrefix(_ prefix: String, key: V2StreamKey) throws -> Data {
    let fields = [prefix, key.sessionId, key.streamId, String(key.captureGeneration), key.source.rawValue]
    for field in fields { try v2ValidateString("identity field", field) }
    return Data(fields.joined(separator: "\0").utf8)
}

private func v2Digest(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

public struct V2StreamKey: Equatable, Hashable, Sendable {
    public let sessionId: String
    public let streamId: String
    public let captureGeneration: UInt64
    public let source: V2Source

    public init(sessionId: String, streamId: String, captureGeneration: UInt64, source: V2Source) throws {
        try v2ValidateIdentifier("sessionId", sessionId)
        try v2ValidateIdentifier("streamId", streamId)
        self.sessionId = sessionId
        self.streamId = streamId
        self.captureGeneration = captureGeneration
        self.source = source
    }
}

public struct V2AtomicCoverage: Equatable, Hashable, Sendable {
    public let key: V2StreamKey
    public let sequence: UInt64
    public let firstSample: UInt64
    public let lastSampleExclusive: UInt64

    public init(key: V2StreamKey, sequence: UInt64, firstSample: UInt64, lastSampleExclusive: UInt64) throws {
        guard lastSampleExclusive > firstSample else {
            throw ProtocolV2ValidationError.invalid("atomic sample range is empty")
        }
        self.key = key
        self.sequence = sequence
        self.firstSample = firstSample
        self.lastSampleExclusive = lastSampleExclusive
    }

    public var coverageId: String {
        get throws {
            var data = try v2IdentityPrefix("tars-atomic-coverage-v2", key: key)
            data.append(0)
            data.append(contentsOf: Data("\(sequence)\0\(firstSample)\0\(lastSampleExclusive)".utf8))
            return "acov_" + v2Digest(data)
        }
    }
}

private func v2SortedAtomic(_ key: V2StreamKey, _ values: [V2AtomicCoverage]) throws -> [V2AtomicCoverage] {
    guard !values.isEmpty, values.allSatisfy({ $0.key == key }) else {
        throw ProtocolV2ValidationError.invalid("atomic coverage list is invalid")
    }
    let sorted = try values.sorted { left, right in
        let leftId = try left.coverageId
        let rightId = try right.coverageId
        return (left.sequence, left.firstSample, left.lastSampleExclusive, leftId) <
            (right.sequence, right.firstSample, right.lastSampleExclusive, rightId)
    }
    for leftIndex in sorted.indices {
        for rightIndex in sorted.indices where rightIndex > leftIndex {
            let left = sorted[leftIndex]
            let right = sorted[rightIndex]
            guard left.sequence != right.sequence,
                  left.lastSampleExclusive <= right.firstSample || right.lastSampleExclusive <= left.firstSample else {
                throw ProtocolV2ValidationError.invalid("atomic coverage overlaps")
            }
        }
    }
    return sorted
}

public func v2TerminalCoverageId(key: V2StreamKey, atomic: [V2AtomicCoverage]) throws -> String {
    let ordered = try v2SortedAtomic(key, atomic)
    var data = try v2IdentityPrefix("tars-terminal-coverage-v2", key: key)
    v2AppendUInt32(UInt32(ordered.count), to: &data)
    for item in ordered { try v2AppendLengthPrefixed(item.coverageId, name: "coverageId", to: &data) }
    return "covr_" + v2Digest(data)
}

public func v2TranscriptSegmentId(
    key: V2StreamKey,
    atomic: [V2AtomicCoverage],
    textFirstSample: UInt64,
    textLastSampleExclusive: UInt64,
    providerResultOrdinal: UInt64,
    providerName: String,
    providerResultId: String,
    sttAttemptGeneration: UInt64?
) throws -> String {
    guard textLastSampleExclusive > textFirstSample else {
        throw ProtocolV2ValidationError.invalid("segment sample range is empty")
    }
    let ordered = try v2SortedAtomic(key, atomic)
    try v2ValidateString("providerName", providerName, identifier: true)
    try v2ValidateString("providerResultId", providerResultId, identifier: true)
    var data = try v2IdentityPrefix("tars-transcript-segment-v2", key: key)
    v2AppendUInt32(UInt32(ordered.count), to: &data)
    for item in ordered { try v2AppendLengthPrefixed(item.coverageId, name: "coverageId", to: &data) }
    v2AppendUInt64(textFirstSample, to: &data)
    v2AppendUInt64(textLastSampleExclusive, to: &data)
    v2AppendUInt64(providerResultOrdinal, to: &data)
    try v2AppendLengthPrefixed(providerName, name: "providerName", to: &data)
    try v2AppendLengthPrefixed(providerResultId, name: "providerResultId", to: &data)
    if let sttAttemptGeneration {
        data.append(1)
        v2AppendUInt64(sttAttemptGeneration, to: &data)
    } else {
        data.append(0)
    }
    return "seg_" + v2Digest(data)
}
