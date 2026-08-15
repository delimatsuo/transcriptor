import Foundation

public struct GeneratedFixtureSource: Sendable {
    public let identity: SourceIdentity
    public let frameDurationMs: UInt64
    public let seed: UInt64

    public init(identity: SourceIdentity, frameDurationMs: UInt64 = 20, seed: UInt64 = 0x54415253) throws {
        guard frameDurationMs >= 20, frameDurationMs <= 250,
              UInt64(identity.sampleRate) * frameDurationMs % 1_000 == 0 else {
            throw CompanionError.invalid("fixture frame duration is not sample-rate aligned")
        }
        self.identity = identity
        self.frameDurationMs = frameDurationMs
        self.seed = seed
    }

    public func makeFrames(count: Int, startAtMs: UInt64 = 0) throws -> [AudioFrame] {
        guard (0...100).contains(count) else { throw CompanionError.invalid("fixture count exceeds offline bound") }
        return try (0..<count).map { try makeFrame(index: $0, startAtMs: startAtMs) }
    }

    public func makeFrame(index: Int, startAtMs: UInt64 = 0) throws -> AudioFrame {
        guard (0..<100).contains(index) else { throw CompanionError.invalid("fixture index exceeds offline bound") }
        let samplesPerFrame = identity.sampleRate * Int(frameDurationMs) / 1_000
        let payloadSize = samplesPerFrame * identity.channelCount * 2
        var generator = SplitMix64(seed: seed &+ UInt64(index))
        var payload = Data(capacity: payloadSize)
        for _ in 0..<(payloadSize / 2) {
            var value = UInt16(truncatingIfNeeded: generator.next())
            value = value == 0 ? 1 : value
            payload.append(UInt8(value & 0xff))
            payload.append(UInt8(value >> 8))
        }
        let firstSample = UInt64(index * samplesPerFrame)
        return try AudioFrame(
            identity: identity,
            sequence: UInt64(index),
            firstSample: firstSample,
            capturedAtMs: startAtMs + UInt64(index) * frameDurationMs,
            payload: payload
        )
    }
}

private struct SplitMix64: Sendable {
    private var state: UInt64

    init(seed: UInt64) { state = seed }

    mutating func next() -> UInt64 {
        state &+= 0x9E3779B97F4A7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
        return z ^ (z >> 31)
    }
}
