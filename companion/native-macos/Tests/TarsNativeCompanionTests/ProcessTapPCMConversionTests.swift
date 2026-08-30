import CoreAudio
import Foundation
import XCTest
@testable import TarsNativeCompanion

final class ProcessTapPCMConversionTests: XCTestCase {
    private let floatFormat = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 2, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 8)

    private func identity(generation: UInt64 = 1) throws -> SourceIdentity {
        try SourceIdentity(sessionID: "pcm-test", streamID: "system", captureGeneration: generation, source: .systemAudio, sampleRate: 16_000, channelCount: 1)
    }

    private func floatData(_ values: [Float]) -> Data {
        var data = Data(capacity: values.count * 4)
        for value in values {
            let bits = value.bitPattern
            data.append(UInt8(truncatingIfNeeded: bits))
            data.append(UInt8(truncatingIfNeeded: bits >> 8))
            data.append(UInt8(truncatingIfNeeded: bits >> 16))
            data.append(UInt8(truncatingIfNeeded: bits >> 24))
        }
        return data
    }

    func testInterleavedFloat32ConvertsToExactly800PCM16Samples() throws {
        var values: [Float] = []
        values.reserveCapacity(2_400 * 2)
        for _ in 0..<2_400 { values.append(contentsOf: [0.25, 0.25]) }
        let buffer = ProcessTapPCMBuffer(format: floatFormat, buffers: [floatData(values)], generation: 1)
        let frames = try CanonicalSystemAudioConverter.convert(buffer, identity: try identity())
        XCTAssertEqual(frames.count, 1)
        XCTAssertEqual(frames[0].sequence, 0)
        XCTAssertEqual(frames[0].firstSample, 0)
        XCTAssertEqual(frames[0].payload.count, 1_600)
        XCTAssertEqual(frames[0].sampleCount, 800)
        XCTAssertEqual(frames[0].eventContext.deviceID, "ProcessTap.SystemAudio")
    }

    func testPlanar44100MonoAndStereoDownmixUsesActualChannelData() throws {
        let format = ProcessTapPCMFormat(sampleRate: 44_100, channelCount: 2, isFloat: true, isInterleaved: false, bitsPerChannel: 32, bytesPerFrame: 4)
        let left = floatData(Array(repeating: 0.5, count: 4_410))
        let right = floatData(Array(repeating: 0, count: 4_410))
        let buffer = ProcessTapPCMBuffer(format: format, buffers: [left, right], generation: 1)
        let frames = try CanonicalSystemAudioConverter.convert(buffer, identity: try identity())
        XCTAssertEqual(frames.count, 2)
        let payload = frames[0].payload.copyData()
        let first = Int16(bitPattern: UInt16(payload[0]) | (UInt16(payload[1]) << 8))
        XCTAssertGreaterThan(first, 7_000)
        XCTAssertLessThan(first, 9_000)
    }

    func testLeftOnlyAndRightOnlyAreNotSilentlyDuplicatedOrDropped() throws {
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 2, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 8)
        let leftOnly = floatData(Array(repeating: 0.8, count: 2_400).flatMap { [$0, 0] })
        let rightOnly = floatData(Array(repeating: 0.8, count: 2_400).flatMap { [0, $0] })
        let leftFrame = try CanonicalSystemAudioConverter.convert(ProcessTapPCMBuffer(format: format, buffers: [leftOnly]), identity: try identity())[0]
        let rightFrame = try CanonicalSystemAudioConverter.convert(ProcessTapPCMBuffer(format: format, buffers: [rightOnly]), identity: try identity())[0]
        XCTAssertEqual(leftFrame.payload.copyData(), rightFrame.payload.copyData())
        XCTAssertNotEqual(leftFrame.payload.copyData(), Data(repeating: 0, count: 1_600))
    }

    func testLong44100FixtureKeepsFractionalResamplerAccounting() throws {
        let format = ProcessTapPCMFormat(sampleRate: 44_100, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let converter = try CanonicalSystemAudioConverter(inputFormat: format, identity: try identity())
        var frames: [AudioFrame] = []
        let chunk = floatData(Array(repeating: 0.125, count: 4_410))
        for _ in 0..<10 {
            frames.append(contentsOf: try converter.convert(ProcessTapPCMBuffer(format: format, buffers: [chunk], generation: 1)))
        }
        XCTAssertEqual(frames.count, 20)
        XCTAssertEqual(frames.map(\.firstSample), (0..<20).map { UInt64($0 * 800) })
        XCTAssertEqual(frames.map(\.sequence), Array(UInt64(0)..<UInt64(20)))
    }

    func testUnsupportedFormatAndNonFiniteSignalAreNotPermissionEvidence() throws {
        let unsupported = ProcessTapPCMFormat(sampleRate: 32_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        XCTAssertThrowsError(try CanonicalSystemAudioConverter(inputFormat: unsupported, identity: try identity()))
        let silent = ProcessTapPCMBuffer(format: ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1), buffers: [floatData([.nan, .infinity, -.infinity] + Array(repeating: 0, count: 100))])
        XCTAssertFalse(CanonicalSystemAudioConverter.containsFiniteNonzeroSignal(silent))
    }

    func testExplicitZeroPCM16FlagsAreRejectedWhileOmittedFixtureFlagsInferSignedInteger() throws {
        let fixture = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: false,
            isInterleaved: true,
            bitsPerChannel: 16,
            bytesPerFrame: 2
        )
        XCTAssertNoThrow(try fixture.validate())
        XCTAssertTrue((fixture.formatFlags & UInt32(kAudioFormatFlagIsSignedInteger)) != 0)

        let explicitZero = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: false,
            isInterleaved: true,
            bitsPerChannel: 16,
            bytesPerFrame: 2,
            formatFlags: 0
        )
        XCTAssertThrowsError(try explicitZero.validate())
    }

    func testAllNonFiniteActualChannelSamplesAreAConversionFailure() throws {
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1)
        let buffer = ProcessTapPCMBuffer(
            format: format,
            buffers: [floatData([.nan, .infinity, -.infinity])],
            generation: 1
        )
        let converter = try CanonicalSystemAudioConverter(inputFormat: format, identity: try identity())
        XCTAssertThrowsError(try converter.convert(buffer)) { error in
            guard case .malformedBuffer = error as? CanonicalSystemAudioConverterError else {
                return XCTFail("all-NaN/Inf input must be a loud malformed conversion failure: \(error)")
            }
        }
    }

    func testSampleTimestampGapResetsFractionalStateAndIsObservable() throws {
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let converter = try CanonicalSystemAudioConverter(inputFormat: format, identity: try identity())
        let chunk = floatData(Array(repeating: 0.25, count: 2_400))
        let first = try converter.convert(ProcessTapPCMBuffer(format: format, buffers: [chunk], sampleTime: 0, generation: 1))
        XCTAssertEqual(first.count, 1)
        let second = try converter.convert(ProcessTapPCMBuffer(format: format, buffers: [chunk], sampleTime: 2_500, generation: 1))
        XCTAssertEqual(second.count, 1)
        XCTAssertEqual(converter.takeLastDiscontinuity(), .gap(expectedSampleTime: 2_400, actualSampleTime: 2_500))
        XCTAssertEqual(second[0].sequence, 1)
        XCTAssertEqual(second[0].firstSample, 800)
    }

    func testTimestampOverlapAndHostRegressionAreNotSilentlyDecodedAsContinuous() throws {
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1, isFloat: true, isInterleaved: true, bitsPerChannel: 32, bytesPerFrame: 4)
        let converter = try CanonicalSystemAudioConverter(inputFormat: format, identity: try identity())
        let chunk = floatData(Array(repeating: 0.25, count: 2_400))
        _ = try converter.convert(ProcessTapPCMBuffer(format: format, buffers: [chunk], sampleTime: 100, hostTime: 10, generation: 1))
        _ = try converter.convert(ProcessTapPCMBuffer(format: format, buffers: [chunk], sampleTime: 2_000, hostTime: 9, generation: 1))
        XCTAssertEqual(converter.takeLastDiscontinuity(), .hostRegression(previousHostTime: 10, actualHostTime: 9))
    }

    func testPlanarDefaultBytesPerFrameAndBigEndianInputAreClassifiedSafely() throws {
        let planar = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 2, isFloat: true, isInterleaved: false, bitsPerChannel: 32)
        XCTAssertEqual(planar.bytesPerFrame, 4)
        XCTAssertNoThrow(try planar.validate())
        let bigEndian = ProcessTapPCMFormat(
            sampleRate: 48_000,
            channelCount: 1,
            isFloat: true,
            isInterleaved: true,
            bitsPerChannel: 32,
            bytesPerFrame: 4,
            formatFlags: UInt32(kAudioFormatFlagIsFloat | kAudioFormatFlagIsBigEndian)
        )
        XCTAssertThrowsError(try bigEndian.validate())
    }

    func testCanonicalCursorsStopLoudlyBeforeSequenceOrSampleWrap() throws {
        let format = ProcessTapPCMFormat(sampleRate: 48_000, channelCount: 1)
        let body = floatData(Array(repeating: 0.25, count: 2_400))
        let buffer = ProcessTapPCMBuffer(format: format, buffers: [body], generation: 1)

        let sequenceBoundary = try CanonicalSystemAudioConverter(inputFormat: format, identity: try identity())
        sequenceBoundary.setCursorForTesting(sequence: UInt64.max - 1, firstSample: 0)
        XCTAssertEqual(try sequenceBoundary.convert(buffer).first?.sequence, UInt64.max - 1)
        XCTAssertThrowsError(try sequenceBoundary.convert(buffer)) { error in
            XCTAssertEqual(error as? CanonicalSystemAudioConverterError, .arithmeticOverflow)
        }

        let sampleBoundary = try CanonicalSystemAudioConverter(inputFormat: format, identity: try identity())
        sampleBoundary.setCursorForTesting(sequence: 0, firstSample: UInt64.max - 799)
        XCTAssertThrowsError(try sampleBoundary.convert(buffer)) { error in
            XCTAssertEqual(error as? CanonicalSystemAudioConverterError, .arithmeticOverflow)
        }
    }
}
