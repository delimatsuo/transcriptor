import AVFoundation
import CoreAudio
import Foundation

@available(macOS 13.0, *)
public final class AVAudioEngineMicrophoneSource: CaptureSource, @unchecked Sendable {
    public let source: AudioSource = .microphone
    public let configuration: CaptureSourceConfiguration
    public private(set) var status: CaptureSourceStatus = .idle

    /// Must be set before `start()`: the ordered relay that preserves frame
    /// order is bound to this sink when capture starts.
    public var sink: CaptureFrameSink?

    private let liveCaptureEnabled: Bool
    private var engine: AVAudioEngine?
    private var sequenceNumber: UInt64 = 0
    private var sampleOffset: UInt64 = 0
    private var pcmAccumulator = Data()
    private let lock = NSLock()
    /// Frames reach the sink through this relay rather than through one
    /// unstructured `Task` each, which would let consecutive 50 ms frames
    /// arrive out of order.
    private var relay: OrderedFrameRelay?

    public init(configuration: CaptureSourceConfiguration, liveCaptureEnabled: Bool = true, sink: CaptureFrameSink? = nil) {
        self.configuration = configuration
        self.liveCaptureEnabled = liveCaptureEnabled
        self.sink = sink
    }

    public func start() async throws {
        guard liveCaptureEnabled else {
            status = .failed(CompanionError.nativeCaptureRequiresSeparateAuthorization.description)
            throw CompanionError.nativeCaptureRequiresSeparateAuthorization
        }
        guard configuration.identity.source == .microphone else {
            status = .failed("microphone device identity is unavailable")
            throw CompanionError.invalid("microphone device identity is unavailable")
        }

        if let sink {
            let newRelay = OrderedFrameRelay(sink: sink)
            lock.withLock { relay = newRelay }
        }

        do {
            let audioEngine = AVAudioEngine()
            let inputNode = audioEngine.inputNode
            let inputFormat = inputNode.inputFormat(forBus: 0)

            guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
                status = .failed("invalid microphone audio format")
                throw CompanionError.invalid("invalid microphone audio format")
            }

            inputNode.removeTap(onBus: 0)
            // KNOWN DEFECT (dormant): buffers arrive here at the hardware's
            // native inputFormat.sampleRate, but are packed below and labeled
            // as configuration.identity.sampleRate (16 kHz) with no
            // resampling in between. Harmless today only because this source
            // has been off the default capture path since --sources
            // system_audio; fix the resampling before re-enabling mic capture.
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] (buffer: AVAudioPCMBuffer, time: AVAudioTime) in
                guard let self else { return }
                guard let channelData = buffer.floatChannelData else { return }

                let frameCount = Int(buffer.frameLength)
                guard frameCount > 0 else { return }

                let targetChannels = self.configuration.identity.channelCount
                let targetSampleRate = self.configuration.identity.sampleRate
                let sourceChannels = Int(buffer.format.channelCount)

                var int16Data = Data(capacity: frameCount * targetChannels * MemoryLayout<Int16>.size)
                for frameIndex in 0..<frameCount {
                    for ch in 0..<targetChannels {
                        let sampleChannel = min(ch, sourceChannels - 1)
                        let sample = channelData[sampleChannel][frameIndex]
                        let clamped = max(-1.0, min(1.0, sample))
                        var intSample = Int16(clamped * 32767.0)
                        withUnsafeBytes(of: &intSample) { int16Data.append(contentsOf: $0) }
                    }
                }

                let samplesPer50ms = (targetSampleRate * 50) / 1000
                let bytesPer50ms = samplesPer50ms * targetChannels * 2

                var readyFrames: [AudioFrame] = []
                let activeRelay = self.lock.withLock { () -> OrderedFrameRelay? in
                    self.pcmAccumulator.append(int16Data)
                    while self.pcmAccumulator.count >= bytesPer50ms {
                        let framePayload = self.pcmAccumulator.prefix(bytesPer50ms)
                        self.pcmAccumulator.removeFirst(bytesPer50ms)
                        self.sequenceNumber += 1
                        let firstSample = self.sampleOffset
                        self.sampleOffset += UInt64(samplesPer50ms)
                        let capturedAtMs = UInt64(Date().timeIntervalSince1970 * 1000.0)

                        if let frame = try? AudioFrame(
                            identity: self.configuration.identity,
                            sequence: self.sequenceNumber,
                            firstSample: firstSample,
                            capturedAtMs: capturedAtMs,
                            payload: Data(framePayload)
                        ) {
                            readyFrames.append(frame)
                        }
                    }
                    return self.relay
                }

                // The input tap fires serially, so yielding outside the lock
                // (the rule: never call out to foreign code while holding one)
                // still hands frames over in capture order.
                for frame in readyFrames {
                    activeRelay?.yield(frame)
                }
            }

            try audioEngine.start()
            self.engine = audioEngine
            self.status = .running(SourceHealth(
                permission: .granted,
                route: .healthy,
                interruption: .clear,
                sleep: .awake,
                overflowed: false,
                deviceIdentity: configuration.deviceIdentity ?? "AVAudioEngine.DefaultMic"
            ))
        } catch {
            // A failed start must not leave the relay's forwarder task alive.
            let orphan = lock.withLock { () -> OrderedFrameRelay? in
                let current = relay
                relay = nil
                return current
            }
            await orphan?.finish()
            status = .failed("AVAudioEngine start failed: \(error.localizedDescription)")
            throw error
        }
    }

    public func stop() async {
        if let engine = engine {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
        }
        engine = nil
        let pendingRelay = lock.withLock { () -> OrderedFrameRelay? in
            pcmAccumulator.removeAll(keepingCapacity: false)
            let current = relay
            relay = nil
            return current
        }
        // Drains what capture already produced before reporting stopped.
        await pendingRelay?.finish()
        status = .stopped(SourceHealth(permission: .granted, route: .unknown))
    }
}
