import CoreMedia
import Foundation
import ScreenCaptureKit

@available(macOS 13.0, *)
public final class ScreenCaptureKitSystemAudioSource: NSObject, CaptureSource, SCStreamDelegate, SCStreamOutput, @unchecked Sendable {
    public let source: AudioSource = .systemAudio
    public let configuration: CaptureSourceConfiguration
    public private(set) var status: CaptureSourceStatus = .idle

    public var sink: CaptureFrameSink?

    private let liveCaptureEnabled: Bool
    private var stream: SCStream?
    private let audioQueue = DispatchQueue(label: "com.tars.companion.screencapturekit.audio", qos: .userInteractive)
    private var sequenceNumber: UInt64 = 0
    private var sampleOffset: UInt64 = 0
    private var pcmAccumulator = Data()
    private let lock = NSLock()

    public init(configuration: CaptureSourceConfiguration, liveCaptureEnabled: Bool = true, sink: CaptureFrameSink? = nil) {
        self.configuration = configuration
        self.liveCaptureEnabled = liveCaptureEnabled
        self.sink = sink
        super.init()
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
            status = .failed(CompanionError.nativeCaptureRequiresSeparateAuthorization.description)
            throw CompanionError.nativeCaptureRequiresSeparateAuthorization
        }
        guard configuration.identity.source == .systemAudio else {
            status = .failed("system-audio identity is invalid")
            throw CompanionError.invalid("system-audio identity is invalid")
        }

        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let display = content.displays.first else {
                status = .failed("no display found for system audio capture")
                throw CompanionError.invalid("no display found for system audio capture")
            }

            let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            let streamConfig = makeAudioOnlyConfiguration()

            let newStream = SCStream(filter: filter, configuration: streamConfig, delegate: self)
            try newStream.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
            try await newStream.startCapture()

            self.stream = newStream
            self.status = .running(SourceHealth(
                permission: .granted,
                route: .healthy,
                interruption: .clear,
                sleep: .awake,
                overflowed: false,
                deviceIdentity: "ScreenCaptureKit.SystemAudio"
            ))
        } catch {
            status = .failed("ScreenCaptureKit capture failed: \(error.localizedDescription)")
            throw error
        }
    }

    public func stop() async {
        if let activeStream = stream {
            try? await activeStream.stopCapture()
        }
        stream = nil
        lock.withLock {
            pcmAccumulator.removeAll(keepingCapacity: false)
        }
        status = .stopped(SourceHealth(permission: .granted, route: .unknown))
    }

    public func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid else { return }

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
        let targetChannels = configuration.identity.channelCount
        let targetSampleRate = configuration.identity.sampleRate

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

        // Chunk into 50ms frames: 50ms * targetSampleRate / 1000 samples
        let samplesPer50ms = (targetSampleRate * 50) / 1000
        let bytesPer50ms = samplesPer50ms * targetChannels * 2

        var readyFrames: [AudioFrame] = []
        lock.withLock {
            pcmAccumulator.append(int16Data)
            while pcmAccumulator.count >= bytesPer50ms {
                let framePayload = pcmAccumulator.prefix(bytesPer50ms)
                pcmAccumulator.removeFirst(bytesPer50ms)
                sequenceNumber += 1
                let firstSample = sampleOffset
                sampleOffset += UInt64(samplesPer50ms)
                let capturedAtMs = UInt64(Date().timeIntervalSince1970 * 1000.0)

                if let frame = try? AudioFrame(
                    identity: configuration.identity,
                    sequence: sequenceNumber,
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

    public func stream(_ stream: SCStream, didStopWithError error: Error) {
        status = .failed("ScreenCaptureKit stream error: \(error.localizedDescription)")
    }
}
