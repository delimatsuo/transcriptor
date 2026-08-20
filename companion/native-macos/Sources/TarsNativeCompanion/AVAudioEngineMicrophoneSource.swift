import AVFoundation
import CoreAudio
import Foundation

@available(macOS 13.0, *)
public final class AVAudioEngineMicrophoneSource: CaptureSource, @unchecked Sendable {
    public let source: AudioSource = .microphone
    public let configuration: CaptureSourceConfiguration
    public private(set) var status: CaptureSourceStatus = .idle

    public var sink: CaptureFrameSink?

    private let liveCaptureEnabled: Bool
    private var engine: AVAudioEngine?
    private var sequenceNumber: UInt64 = 0
    private var sampleOffset: UInt64 = 0
    private var pcmAccumulator = Data()
    private let lock = NSLock()

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

        do {
            let audioEngine = AVAudioEngine()
            let inputNode = audioEngine.inputNode
            let inputFormat = inputNode.inputFormat(forBus: 0)

            guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
                status = .failed("invalid microphone audio format")
                throw CompanionError.invalid("invalid microphone audio format")
            }

            inputNode.removeTap(onBus: 0)
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
                self.lock.withLock {
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
                }

                for frame in readyFrames {
                    Task { [weak self, frame] in
                        guard let self else { return }
                        try? await self.sink?.receive(frame)
                    }
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
        lock.withLock {
            pcmAccumulator.removeAll(keepingCapacity: false)
        }
        status = .stopped(SourceHealth(permission: .granted, route: .unknown))
    }
}
