import AVFoundation
import CoreAudio
import Foundation
import TarsRealtimeAudioBridge

public enum CanonicalSystemAudioConverterError: Error, Equatable, Sendable, CustomStringConvertible {
    case unsupportedFormat(String)
    case malformedBuffer(String)
    case arithmeticOverflow

    public var description: String {
        switch self {
        case .unsupportedFormat(let message): return "Formato de áudio do Process Tap não suportado: \(message)"
        case .malformedBuffer(let message): return "Buffer de áudio do Process Tap inválido: \(message)"
        case .arithmeticOverflow: return "Overflow ao converter o buffer de áudio do Process Tap"
        }
    }
}

/// A value copy of the tap's ASBD.  It intentionally contains no pointer or
/// Core Audio object, so it can safely cross from the C ring drain to Swift.
public struct ProcessTapPCMFormat: Equatable, Sendable {
    public let sampleRate: Double
    public let channelCount: Int
    public let isFloat: Bool
    public let isInterleaved: Bool
    public let bitsPerChannel: Int
    public let bytesPerFrame: Int
    public let bytesPerPacket: Int
    public let framesPerPacket: Int
    public let formatID: UInt32
    public let formatFlags: UInt32
    private let formatFlagsWereExplicit: Bool

    public init(
        sampleRate: Double,
        channelCount: Int,
        isFloat: Bool = true,
        isInterleaved: Bool = true,
        bitsPerChannel: Int = 32,
        bytesPerFrame: Int? = nil,
        bytesPerPacket: Int? = nil,
        framesPerPacket: Int = 1,
        formatID: UInt32 = 0x6C70636D, // kAudioFormatLinearPCM
        formatFlags: UInt32? = nil
    ) {
        self.sampleRate = sampleRate
        self.channelCount = channelCount
        self.isFloat = isFloat
        self.isInterleaved = isInterleaved
        self.bitsPerChannel = bitsPerChannel
        let bytesPerSample = max(1, bitsPerChannel / 8)
        self.bytesPerFrame = bytesPerFrame ?? (isInterleaved ? channelCount * bytesPerSample : bytesPerSample)
        let defaultPacket = self.bytesPerFrame.multipliedReportingOverflow(by: max(1, framesPerPacket))
        self.bytesPerPacket = bytesPerPacket ?? (defaultPacket.overflow ? Int.max : defaultPacket.partialValue)
        self.framesPerPacket = framesPerPacket
        self.formatID = formatID
        if let formatFlags {
            self.formatFlags = formatFlags
            self.formatFlagsWereExplicit = true
        } else {
            var inferredFlags: UInt32 = 0
            if isFloat { inferredFlags |= UInt32(kAudioFormatFlagIsFloat) }
            if !isFloat { inferredFlags |= UInt32(kAudioFormatFlagIsSignedInteger) }
            if !isInterleaved { inferredFlags |= UInt32(kAudioFormatFlagIsNonInterleaved) }
            self.formatFlags = inferredFlags
            self.formatFlagsWereExplicit = false
        }
    }

    public init(asbd: TarsRealtimeASBDSnapshot) {
        let floatFlag = (asbd.formatFlags & UInt32(kAudioFormatFlagIsFloat)) != 0
        self.init(
            sampleRate: asbd.sampleRate,
            channelCount: Int(asbd.channelsPerFrame),
            isFloat: floatFlag,
            isInterleaved: asbd.isInterleaved != 0,
            bitsPerChannel: Int(asbd.bitsPerChannel),
            bytesPerFrame: Int(asbd.bytesPerFrame),
            bytesPerPacket: Int(asbd.bytesPerPacket),
            framesPerPacket: Int(asbd.framesPerPacket),
            formatID: asbd.formatID,
            formatFlags: asbd.formatFlags
        )
    }

    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.sampleRate == rhs.sampleRate &&
            lhs.channelCount == rhs.channelCount &&
            lhs.isFloat == rhs.isFloat &&
            lhs.isInterleaved == rhs.isInterleaved &&
            lhs.bitsPerChannel == rhs.bitsPerChannel &&
            lhs.bytesPerFrame == rhs.bytesPerFrame &&
            lhs.bytesPerPacket == rhs.bytesPerPacket &&
            lhs.framesPerPacket == rhs.framesPerPacket &&
            lhs.formatID == rhs.formatID &&
            lhs.formatFlags == rhs.formatFlags
    }

    public func validate() throws {
        guard sampleRate == 44_100 || sampleRate == 48_000 else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("sample rate must be 44.1 or 48 kHz")
        }
        guard (1...2).contains(channelCount) else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("only mono and stereo are accepted")
        }
        guard formatID == 0x6C70636D else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("format is not linear PCM")
        }
        guard bytesPerPacket > 0, framesPerPacket > 0 else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("packet geometry is invalid")
        }
        guard bytesPerFrame <= Int(UInt32.max), bytesPerPacket <= Int(UInt32.max), framesPerPacket <= Int(UInt32.max) else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("packet geometry exceeds the realtime descriptor range")
        }
        if formatFlagsWereExplicit {
            guard (formatFlags & UInt32(kAudioFormatFlagIsBigEndian)) == 0 else {
                throw CanonicalSystemAudioConverterError.unsupportedFormat("big-endian PCM is not accepted")
            }
            let hasNonInterleavedFlag = (formatFlags & UInt32(kAudioFormatFlagIsNonInterleaved)) != 0
            guard hasNonInterleavedFlag == !isInterleaved else {
                throw CanonicalSystemAudioConverterError.unsupportedFormat("channel-layout flags do not match the tap descriptor")
            }
            if isFloat {
                guard (formatFlags & UInt32(kAudioFormatFlagIsFloat)) != 0 else {
                    throw CanonicalSystemAudioConverterError.unsupportedFormat("non-float flags do not match Float32 PCM")
                }
            } else {
                guard (formatFlags & UInt32(kAudioFormatFlagIsFloat)) == 0,
                      (formatFlags & UInt32(kAudioFormatFlagIsSignedInteger)) != 0 else {
                    throw CanonicalSystemAudioConverterError.unsupportedFormat("PCM16 must be signed integer")
                }
            }
        }
        guard (isFloat && bitsPerChannel == 32) || (!isFloat && bitsPerChannel == 16) else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("only Float32 and signed PCM16 are accepted")
        }
        let bytesPerSample = bitsPerChannel / 8
        guard bytesPerSample > 0, bytesPerFrame == channelCount * bytesPerSample || !isInterleaved else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("bytes-per-frame does not match the channel layout")
        }
        let expectedBytesPerPacket = bytesPerFrame.multipliedReportingOverflow(by: framesPerPacket)
        guard !expectedBytesPerPacket.overflow, bytesPerPacket == expectedBytesPerPacket.partialValue else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("bytes-per-packet does not match the frame geometry")
        }
        if !isInterleaved {
            guard bytesPerFrame == bytesPerSample else {
                throw CanonicalSystemAudioConverterError.unsupportedFormat("planar bytes-per-frame is invalid")
            }
        }
    }
}

/// A copied ring item.  `buffers` is owned by this value and never aliases an
/// `AudioBufferList` supplied to the realtime callback.
public struct ProcessTapPCMBuffer: Equatable, Sendable {
    public let format: ProcessTapPCMFormat
    public let buffers: [Data]
    public let sampleTime: Double?
    public let hostTime: UInt64?
    public let timestampFlags: UInt32
    public let generation: UInt64

    public init(
        format: ProcessTapPCMFormat,
        buffers: [Data],
        sampleTime: Double? = nil,
        hostTime: UInt64? = nil,
        timestampFlags: UInt32 = 0,
        generation: UInt64 = 1
    ) {
        self.format = format
        self.buffers = buffers
        self.sampleTime = sampleTime
        self.hostTime = hostTime
        self.timestampFlags = timestampFlags
        self.generation = generation
    }

    public init(output: TarsRealtimeSlotOutput) {
        let format = ProcessTapPCMFormat(asbd: output.asbd)
        var sizeTuple = output.bufferByteSizes
        let sizes = withUnsafeBytes(of: &sizeTuple) { raw in
            Array(raw.bindMemory(to: UInt32.self).prefix(Int(output.bufferCount))).map(Int.init)
        }
        var copied: [Data] = []
        copied.reserveCapacity(sizes.count)
        var offset = 0
        for size in sizes {
            copied.append(Data(bytes: output.bytes!.advanced(by: offset), count: size))
            offset += size
        }
        let sampleValid = (output.timestampFlags & AudioTimeStampFlags.sampleTimeValid.rawValue) != 0
        let hostValid = (output.timestampFlags & AudioTimeStampFlags.hostTimeValid.rawValue) != 0
        self.init(
            format: format,
            buffers: copied,
            sampleTime: sampleValid ? output.sampleTime : nil,
            hostTime: hostValid ? output.hostTime : nil,
            timestampFlags: output.timestampFlags,
            generation: output.generation
        )
    }
}

public struct ProcessTapTimeAnchor: Equatable, Sendable {
    public let monotonicNanoseconds: UInt64
    public let wallClockMilliseconds: UInt64
    public let hostTime: UInt64?

    public init(monotonicNanoseconds: UInt64, wallClockMilliseconds: UInt64, hostTime: UInt64? = nil) {
        self.monotonicNanoseconds = monotonicNanoseconds
        self.wallClockMilliseconds = wallClockMilliseconds
        self.hostTime = hostTime
    }

    public static func fixture(wallClockMilliseconds: UInt64 = 0) -> ProcessTapTimeAnchor {
        ProcessTapTimeAnchor(monotonicNanoseconds: 0, wallClockMilliseconds: wallClockMilliseconds)
    }
}

/// A discontinuity observed from the timestamp metadata copied by the
/// realtime bridge.  The source turns this into an unknown-end coverage gap;
/// no duration is invented for the missing or overlapping period.
public enum ProcessTapTimestampDiscontinuity: Equatable, Sendable {
    case gap(expectedSampleTime: Double, actualSampleTime: Double)
    case overlap(expectedSampleTime: Double, actualSampleTime: Double)
    case regression(previousSampleTime: Double, actualSampleTime: Double)
    case hostRegression(previousHostTime: UInt64, actualHostTime: UInt64)
}

/// Converts captured tap PCM off the realtime callback.  The conversion keeps
/// a fractional source position and an input sample tail between calls, so a
/// 44.1 kHz stream does not gain or lose samples at callback boundaries.
public final class CanonicalSystemAudioConverter: @unchecked Sendable {
    public static let outputSampleRate = 16_000
    public static let outputChannelCount = 1
    public static let outputSamplesPerFrame = 800
    public static let outputBytesPerFrame = 1_600

    public let inputFormat: ProcessTapPCMFormat
    public let identity: SourceIdentity
    public let anchor: ProcessTapTimeAnchor
    public let deviceID: String

    private var sourceSamples: [Float] = []
    private var sourceBaseIndex = 0
    private var nextSourcePosition: Double = 0
    private var outputSamples: [Int16] = []
    private var nextOutputSample: UInt64 = 0
    private var sequence: UInt64 = 0
    private var expectedInputSampleTime: Double?
    private var previousInputSampleTime: Double?
    private var previousInputHostTime: UInt64?
    private var pendingDiscontinuity: ProcessTapTimestampDiscontinuity?

    /// The next canonical position is exposed only so the owning source can
    /// put a causal unknown-end gap before frames following a timestamp
    /// discontinuity.
    public var nextSequenceForGap: UInt64 { sequence }
    public var nextSampleForGap: UInt64 { nextOutputSample }

    /// Test-only cursor placement used to exercise the restart-before-wrap
    /// contract.  Production sources start at zero and never call this hook;
    /// the converter rejects a frame when either cursor could wrap.
    internal func setCursorForTesting(sequence: UInt64, firstSample: UInt64) {
        self.sequence = sequence
        self.nextOutputSample = firstSample
    }

    public func takeLastDiscontinuity() -> ProcessTapTimestampDiscontinuity? {
        defer { pendingDiscontinuity = nil }
        return pendingDiscontinuity
    }

    public init(
        inputFormat: ProcessTapPCMFormat,
        identity: SourceIdentity,
        anchor: ProcessTapTimeAnchor = .fixture(),
        deviceID: String = "ProcessTap.SystemAudio"
    ) throws {
        try inputFormat.validate()
        guard identity.source == .systemAudio, identity.sampleRate == Self.outputSampleRate, identity.channelCount == Self.outputChannelCount else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("canonical identity must be 16 kHz mono system audio")
        }
        self.inputFormat = inputFormat
        self.identity = identity
        self.anchor = anchor
        guard SourceIdentity.isIdentifier(deviceID) else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("device identifier is invalid")
        }
        self.deviceID = deviceID
    }

    /// Returns true only when a finite decoded actual-channel sample has
    /// nonzero amplitude.  NaN, infinity and signed zero are not functional
    /// signal evidence.
    public static func containsFiniteNonzeroSignal(_ buffer: ProcessTapPCMBuffer) -> Bool {
        guard (try? buffer.format.validate()) != nil,
              let channels = try? decodeChannels(buffer), !channels.isEmpty else { return false }
        return channels.joined().contains { $0.isFinite && $0 != 0 }
    }

    /// Converts one copied ring item and emits zero or more canonical 50 ms
    /// `AudioFrame`s.  No sink or Core Audio operation occurs here.
    public func convert(_ buffer: ProcessTapPCMBuffer) throws -> [AudioFrame] {
        guard buffer.generation == identity.captureGeneration else {
            throw CompanionError.staleGeneration
        }
        guard buffer.format == inputFormat else {
            throw CanonicalSystemAudioConverterError.unsupportedFormat("tap ASBD changed during a generation")
        }
        let channels = try Self.decodeChannels(buffer)
        guard !channels.isEmpty else { return [] }
        guard channels.contains(where: { channel in channel.contains(where: \.isFinite) }) else {
            throw CanonicalSystemAudioConverterError.malformedBuffer("os canais contêm somente NaN ou infinito")
        }
        let frameCount = channels[0].count
        observeTimestamps(buffer, frameCount: frameCount)
        var downmixed = [Float]()
        downmixed.reserveCapacity(frameCount)
        for frameIndex in 0..<frameCount {
            var sum: Float = 0
            var finiteChannelCount = 0
            for channel in channels {
                let sample = channel[frameIndex]
                if sample.isFinite {
                    sum += sample
                    finiteChannelCount += 1
                }
            }
            downmixed.append(finiteChannelCount == 0 ? 0 : sum / Float(finiteChannelCount))
        }
        sourceSamples.append(contentsOf: downmixed)
        produceResampledSamples()
        return try makeReadyFrames()
    }

    public func convert(buffers: [Data], sampleTime: Double? = nil, hostTime: UInt64? = nil, generation: UInt64? = nil) throws -> [AudioFrame] {
        try convert(ProcessTapPCMBuffer(
            format: inputFormat,
            buffers: buffers,
            sampleTime: sampleTime,
            hostTime: hostTime,
            generation: generation ?? identity.captureGeneration
        ))
    }

    /// Test/fixture convenience for converting one complete PCM body.
    public static func convert(
        _ buffer: ProcessTapPCMBuffer,
        identity: SourceIdentity,
        anchor: ProcessTapTimeAnchor = .fixture()
    ) throws -> [AudioFrame] {
        let converter = try CanonicalSystemAudioConverter(inputFormat: buffer.format, identity: identity, anchor: anchor)
        return try converter.convert(buffer)
    }

    private func produceResampledSamples() {
        let ratio = inputFormat.sampleRate / Double(Self.outputSampleRate)
        let end = sourceBaseIndex + sourceSamples.count
        while nextSourcePosition + 1.0 < Double(end) {
            let floorPosition = Int(nextSourcePosition.rounded(.down))
            let localIndex = floorPosition - sourceBaseIndex
            guard localIndex >= 0, localIndex + 1 < sourceSamples.count else { break }
            let fraction = Float(nextSourcePosition - Double(floorPosition))
            let left = sourceSamples[localIndex]
            let right = sourceSamples[localIndex + 1]
            let sample = left + ((right - left) * fraction)
            let finite = sample.isFinite ? sample : 0
            let clamped = max(-1, min(1, finite))
            outputSamples.append(Int16(clamped * 32767.0))
            nextSourcePosition += ratio
        }

        // Retain the sample immediately before the fractional cursor; it is
        // the left interpolation neighbor for the next callback.
        let keepFrom = max(sourceBaseIndex, Int(nextSourcePosition.rounded(.down)) - 1)
        let dropCount = min(sourceSamples.count, max(0, keepFrom - sourceBaseIndex))
        if dropCount > 0 {
            sourceSamples.removeFirst(dropCount)
            sourceBaseIndex += dropCount
        }
    }

    private func observeTimestamps(_ buffer: ProcessTapPCMBuffer, frameCount: Int) {
        if let sampleTime = buffer.sampleTime, sampleTime.isFinite {
            if let previous = previousInputSampleTime,
               sampleTime < previous {
                pendingDiscontinuity = .regression(previousSampleTime: previous, actualSampleTime: sampleTime)
                resetResamplerAfterDiscontinuity()
            } else if let expected = expectedInputSampleTime {
                let delta = sampleTime - expected
                // AudioTimeStamp sample times are integral frame positions in
                // this path.  A small tolerance absorbs representational
                // rounding without hiding a real frame gap/overlap.
                if delta > 0.001 {
                    pendingDiscontinuity = .gap(expectedSampleTime: expected, actualSampleTime: sampleTime)
                    resetResamplerAfterDiscontinuity()
                } else if delta < -0.001 {
                    pendingDiscontinuity = .overlap(expectedSampleTime: expected, actualSampleTime: sampleTime)
                    resetResamplerAfterDiscontinuity()
                }
            }
            previousInputSampleTime = sampleTime
            expectedInputSampleTime = sampleTime + Double(frameCount)
        }

        if let hostTime = buffer.hostTime {
            if let previousHost = previousInputHostTime, hostTime < previousHost {
                pendingDiscontinuity = .hostRegression(previousHostTime: previousHost, actualHostTime: hostTime)
                resetResamplerAfterDiscontinuity()
            }
            previousInputHostTime = hostTime
        }
    }

    private func resetResamplerAfterDiscontinuity() {
        sourceSamples.removeAll(keepingCapacity: false)
        sourceBaseIndex = 0
        nextSourcePosition = 0
        // Do not carry a partial frame across an unknown boundary.  The
        // canonical position and sequence remain monotonic after the gap.
        outputSamples.removeAll(keepingCapacity: false)
    }

    private func makeReadyFrames() throws -> [AudioFrame] {
        guard outputSamples.count >= Self.outputSamplesPerFrame else { return [] }
        var frames: [AudioFrame] = []
        while outputSamples.count >= Self.outputSamplesPerFrame {
            // A canonical frame cannot be published if its sequence or the
            // following fixed-size sample cursor would wrap.  Stop loudly at
            // the boundary so downstream ordering never observes a smaller
            // cursor that looks like a fresh stream.
            guard sequence != UInt64.max,
                  nextOutputSample <= UInt64.max - UInt64(Self.outputSamplesPerFrame) else {
                throw CanonicalSystemAudioConverterError.arithmeticOverflow
            }
            let samples = Array(outputSamples.prefix(Self.outputSamplesPerFrame))
            let firstSample = nextOutputSample
            let millisecondsProduct = firstSample.multipliedReportingOverflow(by: 1_000)
            let nanosecondsProduct = firstSample.multipliedReportingOverflow(by: 1_000_000_000)
            guard !millisecondsProduct.overflow, !nanosecondsProduct.overflow else {
                throw CanonicalSystemAudioConverterError.arithmeticOverflow
            }
            let capturedAtMs = anchor.wallClockMilliseconds.addingReportingOverflow(
                millisecondsProduct.partialValue / UInt64(Self.outputSampleRate)
            )
            let capturedAtMonotonicNs = anchor.monotonicNanoseconds.addingReportingOverflow(
                nanosecondsProduct.partialValue / UInt64(Self.outputSampleRate)
            )
            guard !capturedAtMs.overflow, !capturedAtMonotonicNs.overflow else {
                throw CanonicalSystemAudioConverterError.arithmeticOverflow
            }
            let capturedAtMsValue = capturedAtMs.partialValue
            let capturedAtMonotonicNsValue = capturedAtMonotonicNs.partialValue
            outputSamples.removeFirst(Self.outputSamplesPerFrame)
            var payload = Data(capacity: Self.outputBytesPerFrame)
            for sample in samples {
                let bits = UInt16(bitPattern: sample)
                payload.append(UInt8(truncatingIfNeeded: bits))
                payload.append(UInt8(truncatingIfNeeded: bits >> 8))
            }
            let eventContext = try CaptureEventContext(
                deviceID: deviceID,
                capturedAtMonotonicNs: capturedAtMonotonicNsValue,
                capturedAtWallClockMs: capturedAtMsValue
            )
            let frame = try AudioFrame(
                identity: identity,
                sequence: sequence,
                firstSample: firstSample,
                capturedAtMs: capturedAtMsValue,
                eventContext: eventContext,
                payload: payload
            )
            frames.append(frame)
            sequence += 1
            nextOutputSample += UInt64(Self.outputSamplesPerFrame)
        }
        return frames
    }

    private static func decodeChannels(_ buffer: ProcessTapPCMBuffer) throws -> [[Float]] {
        try buffer.format.validate()
        let format = buffer.format
        let bytesPerSample = format.bitsPerChannel / 8
        if format.isInterleaved {
            guard buffer.buffers.count == 1 else {
                throw CanonicalSystemAudioConverterError.malformedBuffer("interleaved PCM requires one buffer")
            }
            let data = buffer.buffers[0]
            let bytesPerFrame = format.channelCount * bytesPerSample
            guard bytesPerFrame > 0, data.count % bytesPerFrame == 0 else {
                throw CanonicalSystemAudioConverterError.malformedBuffer("interleaved bytes are not frame-aligned")
            }
            let frameCount = data.count / bytesPerFrame
            var channels = Array(repeating: [Float](), count: format.channelCount)
            for channel in 0..<format.channelCount { channels[channel].reserveCapacity(frameCount) }
            for frame in 0..<frameCount {
                for channel in 0..<format.channelCount {
                    let offset = frame * bytesPerFrame + channel * bytesPerSample
                    channels[channel].append(try decodeSample(data, offset: offset, format: format))
                }
            }
            return channels
        }

        guard buffer.buffers.count == format.channelCount else {
            throw CanonicalSystemAudioConverterError.malformedBuffer("planar PCM requires one plane per channel")
        }
        guard let first = buffer.buffers.first, first.count % bytesPerSample == 0 else {
            throw CanonicalSystemAudioConverterError.malformedBuffer("planar bytes are not sample-aligned")
        }
        let frameCount = first.count / bytesPerSample
        var channels: [[Float]] = []
        channels.reserveCapacity(format.channelCount)
        for plane in buffer.buffers {
            guard plane.count == first.count else {
                throw CanonicalSystemAudioConverterError.malformedBuffer("planar channel lengths differ")
            }
            var values: [Float] = []
            values.reserveCapacity(frameCount)
            for frame in 0..<frameCount {
                values.append(try decodeSample(plane, offset: frame * bytesPerSample, format: format))
            }
            channels.append(values)
        }
        return channels
    }

    private static func decodeSample(_ data: Data, offset: Int, format: ProcessTapPCMFormat) throws -> Float {
        let bytesPerSample = format.bitsPerChannel / 8
        guard offset >= 0, offset <= data.count - bytesPerSample else {
            throw CanonicalSystemAudioConverterError.malformedBuffer("sample offset exceeds the buffer")
        }
        return data.withUnsafeBytes { rawBuffer in
            let bytes = rawBuffer.bindMemory(to: UInt8.self)
            if format.isFloat {
                let bits = UInt32(bytes[offset]) |
                    (UInt32(bytes[offset + 1]) << 8) |
                    (UInt32(bytes[offset + 2]) << 16) |
                    (UInt32(bytes[offset + 3]) << 24)
                return Float(bitPattern: bits)
            }
            let bits = UInt16(bytes[offset]) | (UInt16(bytes[offset + 1]) << 8)
            return Float(Int16(bitPattern: bits)) / 32768.0
        }
    }
}
