import Foundation

public struct OfflineSimulationTrace: Equatable, Sendable {
    public let frames: [AudioFrame]
    public let gaps: [CoverageGap]
    public let diagnostics: [DiagnosticEvent]
    public let snapshot: CompanionSnapshot
}

public struct OfflineCompanionSimulator: Sendable {
    public let microphone: SourceIdentity
    public let systemAudio: SourceIdentity
    public private(set) var lifecycle: LifecycleCoordinator
    public private(set) var microphoneReducer = FrameReducer()
    public private(set) var systemAudioReducer = FrameReducer()
    public private(set) var microphoneCustody: CustodyRing
    public private(set) var systemAudioCustody: CustodyRing
    public private(set) var deletion = DeletionCoordinator()
    public private(set) var diagnostics = Diagnostics()

    public init(
        sessionID: String = "fixture-session",
        captureGeneration: UInt64 = 1,
        microphoneSampleRate: Int = 16_000,
        systemAudioSampleRate: Int = 48_000
    ) throws {
        let factory = try SourceIdentityFactory(sessionID: sessionID, captureGeneration: captureGeneration)
        self.microphone = try factory.make(source: .microphone, streamID: "fixture-microphone", sampleRate: microphoneSampleRate, channelCount: 1)
        self.systemAudio = try factory.make(source: .systemAudio, streamID: "fixture-system-audio", sampleRate: systemAudioSampleRate, channelCount: 2)
        self.microphoneCustody = CustodyRing(limits: try CustodyLimits(sampleRate: microphoneSampleRate, channelCount: 1))
        self.systemAudioCustody = CustodyRing(limits: try CustodyLimits(sampleRate: systemAudioSampleRate, channelCount: 2))
        self.lifecycle = LifecycleCoordinator()
    }

    public mutating func start() throws {
        try microphoneReducer.activate(microphone)
        try systemAudioReducer.activate(systemAudio)
        try lifecycle.beginPermissionAndDeviceCheck()
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "fixture-microphone"), for: .microphone)
        try lifecycle.updateHealth(SourceHealth(permission: .granted, route: .healthy, deviceIdentity: "fixture-system-audio"), for: .systemAudio)
        try lifecycle.openTransport()
        try lifecycle.startCapture()
    }

    public mutating func run(frameCount: Int = 10) throws -> OfflineSimulationTrace {
        if lifecycle.physical != .capturing { try start() }
        let microphoneFrames = try GeneratedFixtureSource(identity: microphone, seed: 0x4D4943).makeFrames(count: frameCount)
        let systemFrames = try GeneratedFixtureSource(identity: systemAudio, seed: 0x535953).makeFrames(count: frameCount)
        for index in 0..<frameCount {
            try ingest(microphoneFrames[index])
            try ingest(systemFrames[index])
        }
        try lifecycle.stopCapture()
        try lifecycle.beginTransportDrain()
        try lifecycle.closeTransport()
        return trace()
    }

    public mutating func ingest(_ frame: AudioFrame) throws {
        guard lifecycle.physical == .capturing || lifecycle.physical == .paused else {
            throw CompanionError.invalidTransition("ingest frame")
        }
        switch frame.identity.source {
        case .microphone:
            _ = try microphoneReducer.ingest(frame)
            _ = try microphoneCustody.reserve(CustodyEntry(frame: frame))
        case .systemAudio:
            _ = try systemAudioReducer.ingest(frame)
            _ = try systemAudioCustody.reserve(CustodyEntry(frame: frame))
        }
        try lifecycle.recordAcceptedFrame()
    }

    public mutating func recordGap(for source: AudioSource, reason: GapReason) throws {
        let identity = source == .microphone ? microphone : systemAudio
        let expected = source == .microphone
            ? microphoneReducer.acceptedFrames.last?.lastSampleExclusive ?? 0
            : systemAudioReducer.acceptedFrames.last?.lastSampleExclusive ?? 0
        let gap = try CoverageGap(identity: identity, firstSample: expected, lastSampleExclusive: nil, reason: reason)
        if source == .microphone {
            _ = try microphoneReducer.recordGap(identity: identity, firstSample: expected, lastSampleExclusive: nil, reason: reason)
        } else {
            _ = try systemAudioReducer.recordGap(identity: identity, firstSample: expected, lastSampleExclusive: nil, reason: reason)
        }
        diagnostics.record(DiagnosticEvent(code: "gap_recorded", source: source, generation: identity.captureGeneration))
        _ = gap
    }

    public mutating func beginDeletion() throws -> DeletionFence {
        let fence = try DeletionFence(sessionID: microphone.sessionID, generation: microphone.captureGeneration)
        try lifecycle.beginDeletion()
        try deletion.begin(fence: fence)
        try microphoneCustody.advanceClock(microphoneCustody.lastClockMs, clockCertain: false)
        try systemAudioCustody.advanceClock(systemAudioCustody.lastClockMs, clockCertain: false)
        try deletion.markLocalZeroized(fence)
        try deletion.awaitGatewayAcknowledgement(fence)
        return fence
    }

    public mutating func acknowledgeDeletion(_ fence: DeletionFence, success: Bool) throws {
        try deletion.gatewayAcknowledged(fence, deleted: success)
        try lifecycle.finishDeletion(success: success)
    }

    public func trace() -> OfflineSimulationTrace {
        OfflineSimulationTrace(
            frames: microphoneReducer.acceptedFrames + systemAudioReducer.acceptedFrames,
            gaps: microphoneReducer.recordedGaps + systemAudioReducer.recordedGaps,
            diagnostics: diagnostics.snapshot,
            snapshot: lifecycle.snapshot()
        )
    }
}
