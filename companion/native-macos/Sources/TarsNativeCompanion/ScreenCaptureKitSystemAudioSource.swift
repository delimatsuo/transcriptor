import CoreMedia
import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
public final class ScreenCaptureKitSystemAudioSource: NSObject, CaptureSource, SCStreamDelegate, SCStreamOutput, @unchecked Sendable {
    public let source: AudioSource = .systemAudio
    public let configuration: CaptureSourceConfiguration
    public var status: CaptureSourceStatus {
        lock.withLock { currentStatus }
    }

    /// Must be set before `start()`: the ordered relay that preserves frame
    /// order is bound to this sink when capture starts.
    public var sink: CaptureFrameSink? {
        get { lock.withLock { configuredSink } }
        set { lock.withLock { configuredSink = newValue } }
    }

    private let liveCaptureEnabled: Bool
    private var stream: SCStream?
    private var startingStream: SCStream?
    // The callback queue may deliver a sample from a stream that was retired
    // just before a replacement stream became current.  Keep the identity
    // under the same lock as the accumulator/cursor so a stale callback can
    // never append bytes or advance the replacement relay.
    private var currentStreamOwnerID: ObjectIdentifier?
    private var configuredSink: CaptureFrameSink?
    private let audioQueue = DispatchQueue(label: "com.tars.companion.screencapturekit.audio", qos: .userInteractive)
    private var sequenceNumber: UInt64 = 0
    private var sampleOffset: UInt64 = 0
    private var pcmAccumulator = Data()
    private let lock = NSLock()
    private var currentStatus: CaptureSourceStatus = .idle
    private var lifecycleEpoch: UInt64 = 0
    private var startingEpoch: UInt64?
    private var healthObservers: [CaptureSourceObserverToken: CaptureSourceHealthObserver] = [:]
    /// Frames reach the sink through this relay rather than through one
    /// unstructured `Task` each, which would let consecutive 50 ms frames
    /// arrive out of order.
    private var relay: OrderedFrameRelay?

    public init(configuration: CaptureSourceConfiguration, liveCaptureEnabled: Bool = true, sink: CaptureFrameSink? = nil) {
        self.configuration = configuration
        self.liveCaptureEnabled = liveCaptureEnabled
        self.configuredSink = sink
        super.init()
    }

    public func installHealthObserver(_ observer: @escaping CaptureSourceHealthObserver) -> CaptureSourceObserverToken {
        let token = CaptureSourceObserverToken()
        let update = lock.withLock { () -> CaptureSourceHealthUpdate in
            healthObservers[token] = observer
            return CaptureSourceHealthUpdate(source: source, generation: configuration.identity.captureGeneration, status: currentStatus)
        }
        observer(update)
        return token
    }

    public func removeHealthObserver(_ token: CaptureSourceObserverToken) {
        _ = lock.withLock { healthObservers.removeValue(forKey: token) }
    }

    public func makeAudioOnlyConfiguration() -> SCStreamConfiguration {
        let streamConfiguration = SCStreamConfiguration()
        streamConfiguration.capturesAudio = true
        streamConfiguration.excludesCurrentProcessAudio = true
        streamConfiguration.sampleRate = configuration.identity.sampleRate
        streamConfiguration.channelCount = configuration.identity.channelCount
        streamConfiguration.width = 2
        streamConfiguration.height = 2
        streamConfiguration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        return streamConfiguration
    }

    public func start() async throws {
        guard liveCaptureEnabled else {
            _ = publishStatus(.failed(CompanionError.nativeCaptureRequiresSeparateAuthorization.description))
            throw CompanionError.nativeCaptureRequiresSeparateAuthorization
        }
        guard configuration.identity.source == .systemAudio else {
            _ = publishStatus(.failed("system-audio identity is invalid"))
            throw CompanionError.invalid("system-audio identity is invalid")
        }

        let operation: UInt64 = lock.withLock {
            lifecycleEpoch &+= 1
            let operation = lifecycleEpoch
            startingEpoch = operation
            startingStream = nil
            stream = nil
            currentStreamOwnerID = nil
            sequenceNumber = 0
            sampleOffset = 0
            pcmAccumulator.removeAll(keepingCapacity: false)
            relay = configuredSink.map { OrderedFrameRelay(sink: $0) }
            return operation
        }

        var candidateStream: SCStream?
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard ownsStarting(operation) else { throw CompanionError.callbackFenced }
            guard let display = content.displays.first else {
                _ = publishStatus(.failed("no display found for system audio capture"), operation: operation)
                throw CompanionError.invalid("no display found for system audio capture")
            }

            let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            let streamConfig = makeAudioOnlyConfiguration()

            let newStream = SCStream(filter: filter, configuration: streamConfig, delegate: self)
            candidateStream = newStream
            guard claimStartingStream(newStream, operation: operation) else {
                throw CompanionError.callbackFenced
            }
            try newStream.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
            guard ownsStarting(operation) else { throw CompanionError.callbackFenced }
            try await newStream.startCapture()
            guard ownsStarting(operation) else { throw CompanionError.callbackFenced }

            let runningStatus = CaptureSourceStatus.running(SourceHealth(
                permission: .granted,
                route: .healthy,
                interruption: .clear,
                sleep: .awake,
                overflowed: false,
                deviceIdentity: "ScreenCaptureKit.SystemAudio"
            ))
            let committed = lock.withLock {
                guard lifecycleEpoch == operation, startingEpoch == operation else { return false }
                stream = newStream
                startingStream = nil
                startingEpoch = nil
                return true
            }
            guard committed else { throw CompanionError.callbackFenced }
            _ = publishStatus(runningStatus, operation: operation)
        } catch {
            // A failed start must not leave the relay's forwarder task alive.
            let orphan: OrderedFrameRelay? = lock.withLock {
                guard lifecycleEpoch == operation else { return nil }
                startingEpoch = nil
                startingStream = nil
                stream = nil
                currentStreamOwnerID = nil
                let current = relay
                relay = nil
                return current
            }
            try? await candidateStream?.stopCapture()
            await orphan?.finish()
            _ = publishStatus(.failed("ScreenCaptureKit capture failed: \(error.localizedDescription)"), operation: operation)
            throw error
        }
    }

    public func stop() async {
        let stopPlan: (operation: UInt64, activeStream: SCStream?, startingStream: SCStream?, relay: OrderedFrameRelay?) = lock.withLock {
            lifecycleEpoch &+= 1
            let operation = lifecycleEpoch
            let activeStream = stream
            let pendingStartingStream = startingStream
            stream = nil
            startingStream = nil
            startingEpoch = nil
            currentStreamOwnerID = nil
            pcmAccumulator.removeAll(keepingCapacity: false)
            let current = relay
            relay = nil
            return (operation, activeStream, pendingStartingStream, current)
        }
        if let activeStream = stopPlan.activeStream {
            try? await activeStream.stopCapture()
        }
        if let startingStream = stopPlan.startingStream,
           startingStream !== stopPlan.activeStream {
            try? await startingStream.stopCapture()
        }
        // Drains what capture already produced before reporting stopped.
        await stopPlan.relay?.finish()
        _ = publishStatus(
            .stopped(SourceHealth(permission: .granted, route: .unknown)),
            operation: stopPlan.operation
        )
    }

    public func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid, ownsCurrentStream(stream) else { return }

        guard let formatDesc = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDesc) else {
            return
        }

        var blockBuffer: CMBlockBuffer?
        var audioBufferList = AudioBufferList()
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: &audioBufferList,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: 0,
            blockBufferOut: &blockBuffer
        )

        guard status == noErr, let mData = audioBufferList.mBuffers.mData else { return }
        let dataSize = Int(audioBufferList.mBuffers.mDataByteSize)
        guard dataSize > 0 else { return }

        let isFloat = (asbd.pointee.mFormatFlags & kAudioFormatFlagIsFloat) != 0

        // Convert incoming samples to 16-bit linear PCM
        var int16Data = Data()
        if isFloat {
            let floatCount = dataSize / MemoryLayout<Float32>.size
            let floatPtr = mData.bindMemory(to: Float32.self, capacity: floatCount)
            int16Data.reserveCapacity(floatCount * MemoryLayout<Int16>.size)
            for i in 0..<floatCount {
                let clamped = max(-1.0, min(1.0, floatPtr[i]))
                var intSample = Int16(clamped * 32767.0)
                withUnsafeBytes(of: &intSample) { int16Data.append(contentsOf: $0) }
            }
        } else {
            int16Data = Data(bytes: mData, count: dataSize)
        }

        let converted = appendConvertedPCM(int16Data, ownerID: ObjectIdentifier(stream))

        // The sample handler queue is serial, so yielding outside the lock (the
        // rule: never call out to foreign code while holding one) still hands
        // frames over in capture order.
        for frame in converted.frames {
            converted.relay?.yield(frame)
        }
    }

    /// Shared accumulator/cursor edge for the real ScreenCaptureKit callback
    /// and the deterministic identity fixture.  The owner check is repeated
    /// inside the lock after conversion, because stop/restart can retire the
    /// stream while a callback is decoding its sample buffer.
    private func appendConvertedPCM(
        _ int16Data: Data,
        ownerID: ObjectIdentifier
    ) -> (relay: OrderedFrameRelay?, frames: [AudioFrame]) {
        lock.withLock {
            guard currentStreamOwnerID == ownerID else { return (nil, []) }
            let targetChannels = configuration.identity.channelCount
            let targetSampleRate = configuration.identity.sampleRate
            let samplesPer50ms = (targetSampleRate * 50) / 1000
            let bytesPer50ms = samplesPer50ms * targetChannels * 2
            var readyFrames: [AudioFrame] = []
            pcmAccumulator.append(int16Data)
            while pcmAccumulator.count >= bytesPer50ms {
                let framePayload = pcmAccumulator.prefix(bytesPer50ms)
                pcmAccumulator.removeFirst(bytesPer50ms)
                let sequence = sequenceNumber
                sequenceNumber += 1
                let firstSample = sampleOffset
                sampleOffset += UInt64(samplesPer50ms)
                let capturedAtMs = UInt64(Date().timeIntervalSince1970 * 1000.0)

                if let frame = try? AudioFrame(
                    identity: configuration.identity,
                    sequence: sequence,
                    firstSample: firstSample,
                    capturedAtMs: capturedAtMs,
                    payload: Data(framePayload)
                ) {
                    readyFrames.append(frame)
                }
            }
            return (relay, readyFrames)
        }
    }

    public func stream(_ stream: SCStream, didStopWithError error: Error) {
        let publication: ([CaptureSourceHealthObserver], CaptureSourceStatus)? = lock.withLock {
            guard self.stream === stream || self.startingStream === stream else { return nil }
            if self.startingStream === stream {
                // Invalidate the acquisition owner before publishing.  A
                // delegate callback that wins this edge cannot be followed by
                // a stale `.running` publication from start().
                startingEpoch = nil
                startingStream = nil
                lifecycleEpoch &+= 1
            } else {
                // A stream that has reported a terminal error is retired
                // immediately.  Late samples from that stream must not reach
                // a relay that a later start may install.
                self.stream = nil
            }
            currentStreamOwnerID = nil
            let failed = CaptureSourceStatus.failed("ScreenCaptureKit stream error: \(error.localizedDescription)")
            currentStatus = failed
            return (Array(healthObservers.values), failed)
        }
        guard let publication else { return }
        let update = CaptureSourceHealthUpdate(
            source: source,
            generation: configuration.identity.captureGeneration,
            status: publication.1
        )
        publication.0.forEach { $0(update) }
    }

    private func notifyHealthObservers() {
        let snapshot: (CaptureSourceStatus, [CaptureSourceHealthObserver]) = lock.withLock {
            (currentStatus, Array(healthObservers.values))
        }
        let update = CaptureSourceHealthUpdate(
            source: source,
            generation: configuration.identity.captureGeneration,
            status: snapshot.0
        )
        snapshot.1.forEach { $0(update) }
    }

    private func ownsStarting(_ operation: UInt64) -> Bool {
        lock.withLock { lifecycleEpoch == operation && startingEpoch == operation }
    }

    private func claimStartingStream(_ stream: SCStream, operation: UInt64) -> Bool {
        lock.withLock {
            guard lifecycleEpoch == operation, startingEpoch == operation else { return false }
            startingStream = stream
            currentStreamOwnerID = ObjectIdentifier(stream)
            return true
        }
    }

    private func ownsCurrentStream(_ stream: SCStream) -> Bool {
        lock.withLock { currentStreamOwnerID == ObjectIdentifier(stream) }
    }

    // MARK: - Deterministic stream-identity fixture

    /// Test-only owner transition that exercises the same locked
    /// accumulator/cursor edge without requiring a live ScreenCaptureKit
    /// display or provider permission.
    func installTestingStreamOwner(_ owner: AnyObject) {
        lock.withLock {
            currentStreamOwnerID = ObjectIdentifier(owner)
            sequenceNumber = 0
            sampleOffset = 0
            pcmAccumulator.removeAll(keepingCapacity: false)
            relay = configuredSink.map { OrderedFrameRelay(sink: $0) }
        }
    }

    /// Test-only equivalent of one converted audio callback.  Returned frames
    /// are also yielded through the current relay, matching the production
    /// callback's outside-lock sink handoff.
    @discardableResult
    func submitPCMForTesting(from owner: AnyObject, data: Data) -> [AudioFrame] {
        let converted = appendConvertedPCM(data, ownerID: ObjectIdentifier(owner))
        for frame in converted.frames {
            converted.relay?.yield(frame)
        }
        return converted.frames
    }

    @discardableResult
    private func publishStatus(_ newStatus: CaptureSourceStatus, operation: UInt64? = nil) -> Bool {
        let observers: [CaptureSourceHealthObserver]? = lock.withLock {
            guard operation.map({ lifecycleEpoch == $0 }) ?? true else { return nil }
            currentStatus = newStatus
            return Array(healthObservers.values)
        }
        guard let observers else { return false }
        let update = CaptureSourceHealthUpdate(
            source: source,
            generation: configuration.identity.captureGeneration,
            status: newStatus
        )
        observers.forEach { $0(update) }
        return true
    }
}
