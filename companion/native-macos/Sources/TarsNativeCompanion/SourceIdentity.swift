import Foundation

public struct SourceIdentityFactory: Sendable {
    public let sessionID: String
    public let captureGeneration: UInt64

    public init(sessionID: String, captureGeneration: UInt64) throws {
        guard SourceIdentity.isIdentifier(sessionID) else {
            throw CompanionError.invalid("session identifier is invalid")
        }
        self.sessionID = sessionID
        self.captureGeneration = captureGeneration
    }

    public func make(
        source: AudioSource,
        streamID: String,
        sampleRate: Int,
        channelCount: Int
    ) throws -> SourceIdentity {
        try SourceIdentity(
            sessionID: sessionID,
            streamID: streamID,
            captureGeneration: captureGeneration,
            source: source,
            sampleRate: sampleRate,
            channelCount: channelCount
        )
    }

    public func nextGeneration() throws -> SourceIdentityFactory {
        guard captureGeneration < UInt64.max else {
            throw CompanionError.invalid("capture generation overflow")
        }
        return try SourceIdentityFactory(sessionID: sessionID, captureGeneration: captureGeneration + 1)
    }
}

public struct SourceSequenceTracker: Sendable {
    private var nextSequence: [SourceIdentity: UInt64] = [:]
    private var nextSample: [SourceIdentity: UInt64] = [:]

    public init() {}

    public mutating func validate(_ frame: AudioFrame) throws {
        let expectedSequence = nextSequence[frame.identity] ?? 0
        let expectedSample = nextSample[frame.identity] ?? frame.firstSample
        guard frame.sequence == expectedSequence else {
            throw CompanionError.gapRequired("expected sequence \(expectedSequence), received \(frame.sequence)")
        }
        guard frame.firstSample == expectedSample else {
            throw CompanionError.gapRequired("sample range is not contiguous")
        }
        nextSequence[frame.identity] = frame.sequence + 1
        nextSample[frame.identity] = frame.lastSampleExclusive
    }

    public mutating func reset(identity: SourceIdentity) {
        nextSequence[identity] = 0
        nextSample[identity] = 0
    }

    public func expected(identity: SourceIdentity) -> (sequence: UInt64, firstSample: UInt64) {
        (nextSequence[identity] ?? 0, nextSample[identity] ?? 0)
    }

    mutating func advance(identity: SourceIdentity, sequence: UInt64, firstSample: UInt64) {
        nextSequence[identity] = sequence
        nextSample[identity] = firstSample
    }
}
