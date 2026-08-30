import CoreAudio
import Foundation
import Darwin
import TarsRealtimeAudioBridge
import XCTest

private final class RealtimeRingFixtureOwner: @unchecked Sendable {
    let ring: OpaquePointer

    init(ring: OpaquePointer) {
        self.ring = ring
    }

    deinit {
        TarsRealtimeAudioRingDestroy(ring)
    }
}

/// Owns every pointer and its backing bytes for the bounded cross-queue
/// fixture.  The @unchecked Sendable boundary is intentionally lifetime
/// bounded: the owner outlives the callback task and destroys the ring only
/// after that task has signalled completion.
private final class ProductionIOProcFixtureOwner: @unchecked Sendable {
    let ring: OpaquePointer
    let inputList: UnsafeMutablePointer<AudioBufferList>
    private var inputBytes: Data

    init(ring: OpaquePointer, inputBytes: Data) {
        self.ring = ring
        self.inputList = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: 1)
        self.inputBytes = inputBytes
    }

    deinit {
        inputList.deallocate()
        TarsRealtimeAudioRingDestroy(ring)
    }

    func invoke(sampleTime: Double, hostTime: UInt64 = 0, flags: AudioTimeStampFlags = [.sampleTimeValid]) -> OSStatus {
        inputBytes.withUnsafeBytes { raw in
            inputList.pointee.mNumberBuffers = 1
            inputList.pointee.mBuffers.mNumberChannels = 1
            inputList.pointee.mBuffers.mDataByteSize = UInt32(raw.count)
            inputList.pointee.mBuffers.mData = UnsafeMutableRawPointer(mutating: raw.baseAddress)
            var stamp = AudioTimeStamp()
            stamp.mSampleTime = sampleTime
            stamp.mHostTime = hostTime
            stamp.mFlags = flags
            return TarsRealtimeAudioIOProc(
                9,
                nil,
                UnsafePointer(inputList),
                &stamp,
                nil,
                nil,
                UnsafeMutableRawPointer(bitPattern: UInt(bitPattern: ring))
            )
        }
    }
}

private final class StaleCleanupProbe: @unchecked Sendable {
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)
}

private final class PublicationFenceProbe: @unchecked Sendable {
    let started = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var fenceCountValue = 0

    func recordFence() { lock.withLock { fenceCountValue += 1 } }
    var fenceCount: Int { lock.withLock { fenceCountValue } }
}

private final class PublicationFenceCountProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var stored = 0

    func increment() { lock.withLock { stored += 1 } }
    var value: Int { lock.withLock { stored } }
}

private final class RetirementResultProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: TarsRealtimeRingRetirement?

    func store(_ value: TarsRealtimeRingRetirement) {
        lock.withLock { stored = value }
    }

    var value: TarsRealtimeRingRetirement? {
        lock.withLock { stored }
    }
}

private final class DescriptorResultProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var stored: TarsRealtimeDescriptorClass?

    func store(_ result: TarsRealtimeDescriptorClass) {
        lock.withLock { stored = result }
    }

    var value: TarsRealtimeDescriptorClass? {
        lock.withLock { stored }
    }
}

final class ProcessTapRealtimeBridgeTests: XCTestCase {
    private struct CommandResult {
        let status: Int32
        let output: String
    }

    private func runXCRUN(_ arguments: [String]) throws -> CommandResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
        process.arguments = arguments
        // AST JSON is larger than a pipe's kernel buffer.  Redirect command
        // output to an isolated temporary file before waiting so the test
        // cannot deadlock while collecting compiler diagnostics.
        let outputPath = FileManager.default.temporaryDirectory
            .appendingPathComponent("tars-task10-clang-\(UUID().uuidString).log")
        FileManager.default.createFile(atPath: outputPath.path, contents: nil)
        defer { try? FileManager.default.removeItem(at: outputPath) }
        let outputFile = try FileHandle(forWritingTo: outputPath)
        process.standardOutput = outputFile
        process.standardError = outputFile
        try process.run()
        process.waitUntilExit()
        try outputFile.close()
        let output = String(data: try Data(contentsOf: outputPath), encoding: .utf8) ?? ""
        return CommandResult(status: process.terminationStatus, output: output)
    }

    private func withTemporaryFixture(_ source: String, body: (URL) throws -> Void) throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("tars-task10-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let path = directory.appendingPathComponent("fixture.c")
        try source.write(to: path, atomically: true, encoding: .utf8)
        try body(path)
    }

    private func astFunction(named name: String, in root: Any) -> [String: Any]? {
        guard let dictionary = root as? [String: Any] else {
            if let array = root as? [Any] {
                for child in array {
                    if let result = astFunction(named: name, in: child) { return result }
                }
            }
            return nil
        }
        if dictionary["kind"] as? String == "FunctionDecl",
           dictionary["name"] as? String == name,
           let children = dictionary["inner"] as? [Any],
           children.contains(where: { ($0 as? [String: Any])?["kind"] as? String == "CompoundStmt" }) {
            return dictionary
        }
        if let children = dictionary["inner"] as? [Any] {
            for child in children {
                if let result = astFunction(named: name, in: child) { return result }
            }
        }
        return nil
    }

    private func astCallNodes(in root: Any) -> [[String: Any]] {
        if let dictionary = root as? [String: Any] {
            var result: [[String: Any]] = []
            if let kind = dictionary["kind"] as? String, kind == "CallExpr" || kind == "BuiltinCallExpr" {
                result.append(dictionary)
            }
            if let children = dictionary["inner"] as? [Any] {
                result.append(contentsOf: children.flatMap(astCallNodes(in:)))
            }
            return result
        }
        if let array = root as? [Any] {
            return array.flatMap(astCallNodes(in:))
        }
        return []
    }

    private func firstReferencedDeclaration(in root: Any) -> [String: Any]? {
        guard let dictionary = root as? [String: Any] else { return nil }
        if dictionary["kind"] as? String == "DeclRefExpr",
           let declaration = dictionary["referencedDecl"] as? [String: Any] {
            return declaration
        }
        guard let children = dictionary["inner"] as? [Any] else { return nil }
        for child in children {
            if let result = firstReferencedDeclaration(in: child) { return result }
        }
        return nil
    }

    private func astReachabilityPass(functionName: String, astOutput: String) throws -> Bool {
        let data = Data(astOutput.utf8)
        let root = try JSONSerialization.jsonObject(with: data)
        guard let function = astFunction(named: functionName, in: root) else { return false }
        let allowed = Set([
            "memcpy",
            "__builtin___memcpy_chk",
            "__builtin_object_size",
            "__atomic_load",
            "__atomic_store",
            "__atomic_fetch_add",
            "__atomic_compare_exchange",
            "__atomic_compare_exchange_n",
            "__atomic_exchange"
        ])
        for call in astCallNodes(in: function) {
            guard let children = call["inner"] as? [Any],
                  let declaration = children.first.flatMap({ firstReferencedDeclaration(in: $0) }),
                  declaration["kind"] as? String == "FunctionDecl",
                  let name = declaration["name"] as? String,
                  allowed.contains(name) else {
                return false
            }
        }
        return true
    }

    private func compileAndDumpAST(_ path: URL) throws -> CommandResult {
        let sdkPath = try runXCRUN(["--show-sdk-path"]).output.trimmingCharacters(in: .whitespacesAndNewlines)
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        return try runXCRUN([
            "clang", "-Xclang", "-ast-dump=json", "-fsyntax-only",
            "-isysroot", sdkPath,
            "-I", packageRoot.appendingPathComponent("Sources/TarsRealtimeAudioBridge/include").path,
            path.path
        ])
    }

    private func productionStaleCleanupOrderingPass(_ source: String) -> Bool {
        guard let callbackStart = source.range(of: "OSStatus TarsRealtimeAudioIOProc"),
              let callbackEnd = source.range(
                  of: "OSStatus TarsRealtimeAudioCreateIOProc",
                  range: callbackStart.upperBound..<source.endIndex
              ) else {
            return false
        }
        let callback = String(source[callbackStart.upperBound..<callbackEnd.lowerBound])
        guard let publicationMarker = callback.range(of: "Revalidate at the publication boundary"),
              let staleStart = callback.range(of: "if (!publish) {", range: publicationMarker.upperBound..<callback.endIndex),
              let release = callback.range(
                  of: "atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);",
                  range: staleStart.lowerBound..<callback.endIndex
              ) else {
            return false
        }
        let staleCleanup = callback[staleStart.lowerBound..<release.lowerBound]
        guard let zeroization = staleCleanup.range(of: "volatile uint8_t *zeroBytes"),
              let counterUpdate = staleCleanup.range(of: "TARS_REALTIME_SATURATING_INCREMENT(ring->staleGenerationArrivals)") else {
            return false
        }
        return zeroization.lowerBound < counterUpdate.lowerBound
    }

    private func asbd(interleaved: Bool = true, channels: UInt32 = 1) -> TarsRealtimeASBDSnapshot {
        let bytesPerFrame = interleaved ? channels * 4 : 4
        return TarsRealtimeASBDSnapshot(
            sampleRate: 48_000,
            formatID: 0x6C70636D,
            formatFlags: UInt32(kAudioFormatFlagIsFloat),
            bytesPerPacket: bytesPerFrame,
            framesPerPacket: 1,
            bytesPerFrame: bytesPerFrame,
            channelsPerFrame: channels,
            bitsPerChannel: 32,
            isInterleaved: interleaved ? 1 : 0
        )
    }

    private func push(
        _ ring: OpaquePointer,
        data: [Data],
        channels: [UInt32],
        generation: UInt64 = 1,
        format: TarsRealtimeASBDSnapshot? = nil
    ) -> TarsRealtimeDescriptorClass {
        var buffers = data.enumerated().map { index, bytes in
            TarsRealtimeInputBuffer(data: nil, byteSize: UInt32(bytes.count), channels: channels[index])
        }
        var descriptor = TarsRealtimeInputDescriptor(
            bufferCount: UInt32(buffers.count),
            buffers: nil,
            asbd: format ?? asbd(),
            sampleTime: 10,
            hostTime: 20,
            timestampFlags: UInt32(AudioTimeStampFlags.sampleTimeValid.rawValue | AudioTimeStampFlags.hostTimeValid.rawValue),
            generation: generation
        )
        var result = TARS_REALTIME_DESCRIPTOR_MALFORMED
        buffers.withUnsafeMutableBufferPointer { bufferPointer in
            descriptor.buffers = bufferPointer.baseAddress.map { UnsafePointer($0) }
            func bind(_ index: Int) {
                guard index < data.count else {
                    result = TarsRealtimeAudioRingPush(ring, &descriptor)
                    return
                }
                data[index].withUnsafeBytes { bytes in
                    bufferPointer[index].data = bytes.baseAddress?.assumingMemoryBound(to: UInt8.self)
                    bind(index + 1)
                }
            }
            bind(0)
        }
        return result
    }

    private func pushRaw(
        _ ring: OpaquePointer,
        buffers: [TarsRealtimeInputBuffer],
        format: TarsRealtimeASBDSnapshot,
        generation: UInt64 = 1
    ) -> TarsRealtimeDescriptorClass {
        var descriptor = TarsRealtimeInputDescriptor(
            bufferCount: UInt32(buffers.count),
            buffers: nil,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: generation
        )
        var result = TARS_REALTIME_DESCRIPTOR_MALFORMED
        var copied = buffers
        copied.withUnsafeMutableBufferPointer { pointer in
            descriptor.buffers = pointer.baseAddress.map { UnsafePointer($0) }
            result = TarsRealtimeAudioRingPush(ring, &descriptor)
        }
        return result
    }

    func testRingHasFixedCapacityAndSeparatesValidRingOverflow() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(1, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        let bytes = Data(repeating: 0x41, count: 16)
        XCTAssertEqual(push(ring, data: [bytes], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(push(ring, data: [bytes], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        let counters = TarsRealtimeAudioRingSnapshot(ring)
        XCTAssertEqual(counters.callbackArrivals, 2)
        XCTAssertEqual(counters.validNonemptyArrivals, 2)
        XCTAssertEqual(counters.enqueuedCount, 1)
        XCTAssertEqual(counters.ringOverflowCount, 1)
        XCTAssertEqual(counters.ringOverflowEpisodes, 1)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 1)
    }

    func testProductionIOProcCopiesTimestampAndUsesCancellableGenerationFence() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        let inputList = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: 1)
        defer { inputList.deallocate() }
        var inputBytes = Data(repeating: 0x3C, count: 16)
        inputBytes.withUnsafeMutableBytes { raw in
            inputList.pointee.mNumberBuffers = 1
            inputList.pointee.mBuffers.mNumberChannels = 1
            inputList.pointee.mBuffers.mDataByteSize = UInt32(raw.count)
            inputList.pointee.mBuffers.mData = raw.baseAddress
            var stamp = AudioTimeStamp()
            stamp.mSampleTime = 123
            stamp.mHostTime = 456
            stamp.mFlags = AudioTimeStampFlags(rawValue: AudioTimeStampFlags.sampleTimeValid.rawValue | AudioTimeStampFlags.hostTimeValid.rawValue)
            let status = TarsRealtimeAudioIOProc(
                9,
                nil,
                UnsafePointer(inputList),
                &stamp,
                nil,
                nil,
                UnsafeMutableRawPointer(bitPattern: UInt(bitPattern: ring))
            )
            XCTAssertEqual(status, noErr)
        }
        let counters = TarsRealtimeAudioRingSnapshot(ring)
        XCTAssertEqual(counters.callbackArrivals, 1)
        XCTAssertEqual(counters.validNonemptyArrivals, 1)
        XCTAssertEqual(counters.enqueuedCount, 1)

        TarsRealtimeAudioRingSetGeneration(ring, 2)
        let staleClass = push(
            ring,
            data: [Data(repeating: 0x3C, count: 16)],
            channels: [1],
            generation: 1,
            format: format
        )
        XCTAssertEqual(staleClass, TARS_REALTIME_DESCRIPTOR_STALE_GENERATION)
        XCTAssertEqual(TarsRealtimeAudioRingSnapshot(ring).staleGenerationArrivals, 1)
    }

    func testProductionIOProcExternalHoldDoesNotEnqueuePartiallyCopiedSlot() throws {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        let owner = ProductionIOProcFixtureOwner(
            ring: ring,
            inputBytes: Data(repeating: 0xCD, count: 16)
        )
        let callbackFinished = DispatchSemaphore(value: 0)
        let fenceProbe = PublicationFenceProbe()
        let fenceContext = Unmanaged.passUnretained(fenceProbe).toOpaque()
        let fenceHook: TarsRealtimePublicationFenceHook = { context in
            guard let context else { return }
            Unmanaged<PublicationFenceProbe>
                .fromOpaque(context)
                .takeUnretainedValue()
                .started
                .signal()
        }
        TarsRealtimeAudioRingSetPublicationFenceHookForTesting(ring, fenceHook, fenceContext)
        defer {
            TarsRealtimeAudioRingSetPublicationFenceHookForTesting(ring, nil, nil)
        }

        // The production callback is externally held after its bounded copy;
        // it does not change activeGeneration or run a self-fencing branch.
        TarsRealtimeAudioRingHoldNextFinalPublicationForTesting(ring)
        DispatchQueue.global().async {
            _ = owner.invoke(sampleTime: 99)
            callbackFinished.signal()
        }
        XCTAssertEqual(callbackFinished.wait(timeout: .now() + 1), .success)
        XCTAssertTrue(TarsRealtimeAudioRingPublicationPauseReadyForTesting(ring))

        let fenceFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            TarsRealtimeAudioRingSetGeneration(owner.ring, 0)
            fenceFinished.signal()
        }
        XCTAssertEqual(fenceProbe.started.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(ring), 0)
        XCTAssertFalse(TarsRealtimeAudioRingTryBeginPublicationForTesting(ring, 1))
        XCTAssertEqual(fenceFinished.wait(timeout: .now()), .timedOut)

        // Releasing the held callback completes the final generation check and
        // stale cleanup, allowing the non-realtime fence to return.
        TarsRealtimeAudioRingResumeHeldPublicationForTesting(ring)
        XCTAssertEqual(fenceFinished.wait(timeout: .now() + 1), .success)
        let counters = TarsRealtimeAudioRingSnapshot(ring)
        XCTAssertEqual(counters.callbackArrivals, 1)
        XCTAssertEqual(counters.validNonemptyArrivals, 1)
        XCTAssertEqual(counters.staleGenerationArrivals, 1)
        XCTAssertEqual(counters.enqueuedCount, 0)
        XCTAssertEqual(counters.poppedCount, 0)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 0)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(ring), 0)

        // The externally held fixture is not allowed to regress into the old
        // callback-side self-fencing schedule.  The production callback may
        // only observe the generation at its final edge; closing the ring is
        // a non-realtime ownership operation.
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let cSource = try String(
            contentsOf: packageRoot.appendingPathComponent(
                "Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c"
            )
        )
        guard let callbackStart = cSource.range(of: "OSStatus TarsRealtimeAudioIOProc"),
              let callbackEnd = cSource.range(
                  of: "OSStatus TarsRealtimeAudioCreateIOProc",
                  range: callbackStart.upperBound..<cSource.endIndex
              ) else {
            return XCTFail("production IOProc symbol range is missing")
        }
        let callback = cSource[callbackStart.upperBound..<callbackEnd.lowerBound]
        let callbackWithoutWhitespace = callback.filter { !$0.isWhitespace }
        XCTAssertFalse(
            callbackWithoutWhitespace.contains("atomic_store_explicit(&ring->activeGeneration,0u"),
            "production IOProc must never self-fence activeGeneration"
        )
        XCTAssertFalse(
            callbackWithoutWhitespace.contains("atomic_flag"),
            "production IOProc must never reintroduce a callback-side lock/spin"
        )
    }

    func testProductionIOProcRecordsDistinctOverflowBoundariesAcrossInterleavedPopAndEnqueue() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        let owner = ProductionIOProcFixtureOwner(
            ring: ring,
            inputBytes: Data(repeating: 0x37, count: 16)
        )

        XCTAssertEqual(owner.invoke(sampleTime: 0), noErr)
        XCTAssertEqual(owner.invoke(sampleTime: 800), noErr)
        XCTAssertEqual(owner.invoke(sampleTime: 1_600), noErr) // episode A

        // Pop one retained slot, then successfully enqueue before the second
        // overflow.  The producer metadata must preserve A and B separately;
        // reconstructing from a later retained-slot count would collapse them.
        var outputBytes = Data(count: 64)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(owner.ring, &output), 1)
        }
        XCTAssertEqual(owner.invoke(sampleTime: 2_400), noErr)
        XCTAssertEqual(owner.invoke(sampleTime: 3_200), noErr) // episode B

        var first = TarsRealtimeOverflowBoundary(
            producerWriteIndex: 0,
            producerReadIndex: 0,
            episodeNumber: 0,
            retainedSlotCount: 0
        )
        var second = first
        XCTAssertTrue(TarsRealtimeAudioRingPopOverflowBoundary(owner.ring, &first))
        XCTAssertTrue(TarsRealtimeAudioRingPopOverflowBoundary(owner.ring, &second))
        XCTAssertFalse(TarsRealtimeAudioRingPopOverflowBoundary(owner.ring, &second))
        XCTAssertEqual(first.producerWriteIndex, 2)
        XCTAssertEqual(first.producerReadIndex, 0)
        XCTAssertEqual(first.retainedSlotCount, 2)
        XCTAssertEqual(second.producerWriteIndex, 3)
        XCTAssertEqual(second.producerReadIndex, 1)
        XCTAssertEqual(second.retainedSlotCount, 2)
        XCTAssertEqual(second.episodeNumber, first.episodeNumber + 1)
        XCTAssertEqual(TarsRealtimeAudioRingSnapshot(owner.ring).ringOverflowMetadataDrops, 0)
    }

    func testOverflowBoundaryAbsoluteWriteCursorSurvivesPollPopRace() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        let first = Data(repeating: 0x11, count: 16)
        let second = Data(repeating: 0x22, count: 16)
        let dropped = Data(repeating: 0x33, count: 16)
        let afterBoundary = Data(repeating: 0x44, count: 16)
        XCTAssertEqual(push(ring, data: [first], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(push(ring, data: [second], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(push(ring, data: [dropped], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)

        // Poll the episode record first, then make room and publish a new
        // frame.  The absolute write cursor must keep that new frame out of
        // the older episode's retained-pop phase.
        var boundary = TarsRealtimeOverflowBoundary(
            producerWriteIndex: 0,
            producerReadIndex: 0,
            episodeNumber: 0,
            retainedSlotCount: 0
        )
        XCTAssertTrue(TarsRealtimeAudioRingPopOverflowBoundary(ring, &boundary))
        var outputBytes = Data(count: 64)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(Data(outputBytes.prefix(first.count)), first)
        XCTAssertEqual(push(ring, data: [afterBoundary], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)

        outputBytes.resetBytes(in: outputBytes.startIndex..<outputBytes.endIndex)
        output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPopThrough(ring, boundary.producerWriteIndex, &output), 1)
        }
        XCTAssertEqual(Data(outputBytes.prefix(second.count)), second)
        XCTAssertEqual(TarsRealtimeAudioRingReadIndex(ring), boundary.producerWriteIndex)

        // The producer's post-boundary frame is only eligible after its
        // episode marker has been emitted by the Swift drain.
        XCTAssertEqual(TarsRealtimeAudioRingPopThrough(ring, boundary.producerWriteIndex, &output), 0)
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(Data(outputBytes.prefix(afterBoundary.count)), afterBoundary)
    }

    func testPopRefusesToCrossBoundaryPublishedAfterInitialMetadataPoll() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        let frame0 = Data(repeating: 0x10, count: 16)
        let frame1 = Data(repeating: 0x20, count: 16)
        let dropped = Data(repeating: 0x30, count: 16)
        let postBoundary = Data(repeating: 0x40, count: 16)
        XCTAssertEqual(push(ring, data: [frame0], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(push(ring, data: [frame1], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)

        // This is the Swift-side metadata poll that misses the episode.  The
        // producer then records the boundary and publishes a post-boundary
        // frame before the consumer attempts its next pop.
        var boundary = TarsRealtimeOverflowBoundary(
            producerWriteIndex: 0,
            producerReadIndex: 0,
            episodeNumber: 0,
            retainedSlotCount: 0
        )
        XCTAssertFalse(TarsRealtimeAudioRingPopOverflowBoundary(ring, &boundary))
        XCTAssertEqual(push(ring, data: [dropped], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)

        var outputBytes = Data(count: 64)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(Data(outputBytes.prefix(frame0.count)), frame0)
        XCTAssertEqual(push(ring, data: [postBoundary], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)

        // Frame 1 is before the captured producer cursor and remains eligible.
        outputBytes.resetBytes(in: outputBytes.startIndex..<outputBytes.endIndex)
        output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(Data(outputBytes.prefix(frame1.count)), frame1)

        // The C pop edge now observes read == producerWriteIndex and must
        // stop before touching the post-boundary slot.  Swift must consume the
        // FIFO marker first, even though the marker was published after its
        // initial poll.
        XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), -2)
        XCTAssertTrue(TarsRealtimeAudioRingPopOverflowBoundary(ring, &boundary))
        XCTAssertEqual(boundary.producerWriteIndex, 2)
        XCTAssertEqual(boundary.producerReadIndex, 0)

        outputBytes.resetBytes(in: outputBytes.startIndex..<outputBytes.endIndex)
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(Data(outputBytes.prefix(postBoundary.count)), postBoundary)
    }

    func testAbsoluteRingCursorStopsBeforeWrapAndDiagnosticCounterSaturates() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        TarsRealtimeAudioRingSetCursorForTesting(ring, UInt64.max - 1, UInt64.max - 1)
        let data = Data(repeating: 0x55, count: 16)
        XCTAssertEqual(push(ring, data: [data], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)

        var outputBytes = Data(count: 64)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(TarsRealtimeAudioRingReadIndex(ring), UInt64.max)
        XCTAssertEqual(push(ring, data: [data], channels: [1]), TARS_REALTIME_DESCRIPTOR_CURSOR_OVERFLOW)
        let counters = TarsRealtimeAudioRingSnapshot(ring)
        XCTAssertEqual(counters.cursorOverflow, 1)
        XCTAssertEqual(push(ring, data: [data], channels: [1]), TARS_REALTIME_DESCRIPTOR_CURSOR_OVERFLOW)
        XCTAssertEqual(TarsRealtimeAudioRingSnapshot(ring).cursorOverflow, 1)
        TarsRealtimeAudioRingSetGeneration(ring, 2)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(ring), 0,
                       "a cursor-exhausted ring must not silently reopen and invert absolute order")
    }

    func testNonzeroGenerationReopenRetiresQueuedPayloadAndMetadata() throws {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        let oldA = Data(repeating: 0xA1, count: 16)
        let oldB = Data(repeating: 0xB2, count: 16)
        XCTAssertEqual(push(ring, data: [oldA], channels: [1], generation: 1), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(push(ring, data: [oldB], channels: [1], generation: 1), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(push(ring, data: [Data(repeating: 0xD4, count: 16)], channels: [1], generation: 1), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 2)
        XCTAssertFalse(
            TarsRealtimeAudioRingOverflowMetadataIsZeroizedForTesting(ring),
            "the full-ring fixture must seed nonzero overflow evidence before retirement"
        )
        // A nonzero transition is a new ownership epoch, not merely an
        // active-generation store.  Every queued gen-1 slot and producer
        // boundary must be retired before gen 2 can publish.
        TarsRealtimeAudioRingSetGeneration(ring, 2)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 0)
        XCTAssertTrue(TarsRealtimeAudioRingSlotIsZeroizedForTesting(ring, 0))
        XCTAssertTrue(TarsRealtimeAudioRingSlotIsZeroizedForTesting(ring, 1))
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(ring), 2)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 0)
        XCTAssertTrue(TarsRealtimeAudioRingSlotIsZeroizedForTesting(ring, 0))
        XCTAssertTrue(TarsRealtimeAudioRingSlotIsZeroizedForTesting(ring, 1))
        XCTAssertTrue(
            TarsRealtimeAudioRingOverflowMetadataIsZeroizedForTesting(ring),
            "a direct nonzero reopen must zeroize overflow evidence bytes, not only reset cursors"
        )
        // The byte inspector intentionally closes publication admission while
        // it reads producer-owned metadata.  Reopen the same generation only
        // after that quiesced inspection before publishing replacement input.
        TarsRealtimeAudioRingSetGeneration(ring, 2)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(ring), 2)
        var overflowBoundary = TarsRealtimeOverflowBoundary(
            producerWriteIndex: 0,
            producerReadIndex: 0,
            episodeNumber: 0,
            retainedSlotCount: 0
        )
        XCTAssertFalse(
            TarsRealtimeAudioRingPopOverflowBoundary(ring, &overflowBoundary),
            "a direct nonzero reopen must retire old overflow metadata"
        )

        let sourcePath = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c")
        let source = try String(contentsOf: sourcePath)
        func hasReopenRetirement(_ body: String) -> Bool {
            body.contains("tars_retire_queued_slots(ring);") &&
                body.contains("ring->overflowMetadata") &&
                body.contains("atomic_store_explicit(&ring->overflowMetadataReadIndex, 0u") &&
                body.contains("atomic_store_explicit(&ring->overflowMetadataWriteIndex, 0u")
        }
        let metadataZeroizationCall = "(void)memset_s(\n        ring->overflowMetadata,\n        sizeof(ring->overflowMetadata),\n        0,\n        sizeof(ring->overflowMetadata));"
        func hasMetadataByteRetirement(_ body: String) -> Bool {
            body.contains(metadataZeroizationCall)
        }
        XCTAssertTrue(hasReopenRetirement(source))
        XCTAssertTrue(
            hasMetadataByteRetirement(source),
            "direct nonzero reopen must retain the exact overflow-metadata byte clear"
        )
        XCTAssertFalse(
            hasReopenRetirement(source.replacingOccurrences(
                of: "tars_retire_queued_slots(ring);",
                with: "",
                options: [.literal]
            )),
            "removing the nonzero reopen purge must fail the source guard"
        )
        XCTAssertFalse(
            hasReopenRetirement(source.replacingOccurrences(
                of: "atomic_store_explicit(&ring->overflowMetadataWriteIndex, 0u, memory_order_release);",
                with: "",
                options: [.literal]
            )),
            "removing old overflow-metadata retirement must fail the source guard"
        )
        XCTAssertFalse(
            hasMetadataByteRetirement(source.replacingOccurrences(
                of: metadataZeroizationCall,
                with: "",
                options: [.literal]
            )),
            "removing only overflow-metadata byte zeroization must fail the source guard"
        )

        let replacement = Data(repeating: 0xC3, count: 16)
        XCTAssertEqual(push(ring, data: [replacement], channels: [1], generation: 2), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        var outputBytes = Data(count: 64)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(output.generation, 2)
        XCTAssertEqual(Data(outputBytes.prefix(replacement.count)), replacement)
    }

    func testDiagnosticCounterSaturatesFromMaxMinusOneWithoutWrapping() throws {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        TarsRealtimeAudioRingSetCallbackArrivalsForTesting(ring, UInt64.max - 1)
        let data = Data(repeating: 0x5A, count: 16)
        XCTAssertEqual(push(ring, data: [data], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(TarsRealtimeAudioRingSnapshot(ring).callbackArrivals, UInt64.max)
        XCTAssertEqual(push(ring, data: [data], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        XCTAssertEqual(
            TarsRealtimeAudioRingSnapshot(ring).callbackArrivals,
            UInt64.max,
            "a diagnostic counter must remain saturated rather than wrap"
        )

        let sourcePath = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c")
        let source = try String(contentsOf: sourcePath)
        func hasSaturatingCounterGuard(_ body: String) -> Bool {
            body.contains("tars_counter_expected != UINT64_MAX") &&
                body.contains("atomic_compare_exchange_strong_explicit")
        }
        XCTAssertTrue(hasSaturatingCounterGuard(source))
        let wrappingMutation = source.replacingOccurrences(
            of: "tars_counter_expected != UINT64_MAX",
            with: "true",
            options: [.literal]
        )
        XCTAssertFalse(
            hasSaturatingCounterGuard(wrappingMutation),
            "the mutation to wrapping addition must fail the source guard"
        )
    }

    func testPublicationFenceClosesAdmissionAndWaitsForInFlightPublication() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        let owner = RealtimeRingFixtureOwner(ring: ring)
        let probe = PublicationFenceProbe()
        let context = Unmanaged.passUnretained(probe).toOpaque()
        let hook: TarsRealtimePublicationFenceHook = { context in
            guard let context else { return }
            Unmanaged<PublicationFenceProbe>.fromOpaque(context).takeUnretainedValue().started.signal()
        }
        TarsRealtimeAudioRingSetPublicationFenceHookForTesting(owner.ring, hook, context)
        defer { TarsRealtimeAudioRingSetPublicationFenceHookForTesting(owner.ring, nil, nil) }
        XCTAssertTrue(TarsRealtimeAudioRingTryBeginPublicationForTesting(owner.ring, 1))
        let fenceFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            TarsRealtimeAudioRingSetGeneration(owner.ring, 0)
            fenceFinished.signal()
        }

        // The non-realtime fence closes admission and stores generation zero
        // before waiting.  The admitted publication is held by this test so
        // completion cannot be observed until EndPublication is called.
        XCTAssertEqual(probe.started.wait(timeout: .now() + 1), .success)
        XCTAssertTrue(TarsRealtimeAudioRingPublicationFenceStartedForTesting(owner.ring))
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(owner.ring), 0)
        XCTAssertFalse(TarsRealtimeAudioRingTryBeginPublicationForTesting(owner.ring, 1))

        TarsRealtimeAudioRingEndPublicationForTesting(owner.ring)
        XCTAssertEqual(fenceFinished.wait(timeout: .now() + .seconds(1)), .success)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(owner.ring), 0)
    }

    func testTerminalRetirementSnapshotsIOProcPublicationBeforeRawPurge() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        let owner = ProductionIOProcFixtureOwner(
            ring: ring,
            inputBytes: Data(repeating: 0xD7, count: 16)
        )
        let context = Unmanaged.passUnretained(owner).toOpaque()
        let hook: TarsRealtimePublicationFenceHook = { context in
            guard let context else { return }
            let owner = Unmanaged<ProductionIOProcFixtureOwner>
                .fromOpaque(context)
                .takeUnretainedValue()
            _ = owner.invoke(sampleTime: 0)
        }
        TarsRealtimeAudioRingSetTerminalRetirementHookForTesting(owner.ring, hook, context)
        defer {
            TarsRealtimeAudioRingSetTerminalRetirementHookForTesting(owner.ring, nil, nil)
        }

        // The raw ring starts empty.  The hook admits the production callback
        // after terminal ownership is about to retire the ring but before C
        // closes publication.  The returned snapshot must therefore report
        // the callback's exact raw boundary; a pre-close RetainedSlots read
        // would silently lose it during purge.
        let retirement = TarsRealtimeAudioRingRetireForTerminalFailure(owner.ring)
        XCTAssertTrue(retirement.hadRetainedSlots)
        XCTAssertEqual(retirement.firstReadIndex, 0)
        XCTAssertEqual(retirement.writeIndex, 1)
        XCTAssertEqual(retirement.retainedSlotCount, 1)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(owner.ring), 0)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(owner.ring), 0)
        XCTAssertTrue(TarsRealtimeAudioRingSlotIsZeroizedForTesting(owner.ring, 0))
    }

    func testTerminalRetirementAccountsAdmittedProductionIOProcDiscardBeforeFailure() throws {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        let owner = ProductionIOProcFixtureOwner(
            ring: ring,
            inputBytes: Data(repeating: 0xE5, count: 16)
        )
        let fenceProbe = PublicationFenceProbe()
        let fenceContext = Unmanaged.passUnretained(fenceProbe).toOpaque()
        let publicationFenceHook: TarsRealtimePublicationFenceHook = { context in
            guard let context else { return }
            Unmanaged<PublicationFenceProbe>
                .fromOpaque(context)
                .takeUnretainedValue()
                .recordFence()
            Unmanaged<PublicationFenceProbe>
                .fromOpaque(context)
                .takeUnretainedValue()
                .started
                .signal()
        }
        TarsRealtimeAudioRingSetPublicationFenceHookForTesting(ring, publicationFenceHook, fenceContext)
        defer {
            TarsRealtimeAudioRingSetPublicationFenceHookForTesting(ring, nil, nil)
        }

        // Run the real production callback first.  Its bounded copy returns
        // with one publication admission held at the final generation edge;
        // it does not change activeGeneration or self-fence.
        TarsRealtimeAudioRingHoldNextFinalPublicationForTesting(ring)
        let callbackFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            _ = owner.invoke(sampleTime: 0)
            callbackFinished.signal()
        }
        XCTAssertEqual(callbackFinished.wait(timeout: .now() + 1), .success)
        XCTAssertTrue(TarsRealtimeAudioRingPublicationPauseReadyForTesting(ring))

        // A separate control actor owns terminal retirement.  The publication
        // hook fires only after the gate is closed and generation zero is
        // published; the held callback keeps the retirement blocked until the
        // control actor explicitly resumes it.
        let retirementProbe = RetirementResultProbe()
        let retirementFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            retirementProbe.store(TarsRealtimeAudioRingRetireForTerminalFailure(owner.ring))
            retirementFinished.signal()
        }
        XCTAssertEqual(fenceProbe.started.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(ring), 0)
        XCTAssertTrue(TarsRealtimeAudioRingPublicationPauseReadyForTesting(ring))
        XCTAssertFalse(TarsRealtimeAudioRingTryBeginPublicationForTesting(ring, 1))
        XCTAssertEqual(retirementFinished.wait(timeout: .now()), .timedOut)

        TarsRealtimeAudioRingResumeHeldPublicationForTesting(ring)
        XCTAssertEqual(retirementFinished.wait(timeout: .now() + 1), .success)
        guard let retirement = retirementProbe.value else {
            return XCTFail("terminal retirement did not return a result")
        }

        XCTAssertFalse(retirement.hadRetainedSlots)
        XCTAssertTrue(retirement.hadAdmittedLoss)
        XCTAssertEqual(retirement.admittedLossReadIndex, 0)
        XCTAssertEqual(retirement.admittedLossWriteIndex, 0)
        XCTAssertEqual(TarsRealtimeAudioRingGeneration(ring), 0)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 0)
        let counters = TarsRealtimeAudioRingSnapshot(ring)
        XCTAssertEqual(counters.enqueuedCount, 0)
        XCTAssertEqual(counters.staleGenerationArrivals, 1)
        XCTAssertEqual(fenceProbe.fenceCount, 1)

        let duplicateRetirement = TarsRealtimeAudioRingRetireForTerminalFailure(ring)
        XCTAssertFalse(duplicateRetirement.hadRetainedSlots)
        XCTAssertFalse(duplicateRetirement.hadAdmittedLoss)
        XCTAssertEqual(
            fenceProbe.fenceCount,
            1,
            "a duplicate terminal caller must not refence an already retired ring"
        )

        // Mutation-effective source guard: terminal retirement must retain an
        // explicit admitted-loss outcome.  Removing the outcome fields would
        // make this real production schedule unable to report its boundary.
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let header = try String(
            contentsOf: packageRoot
                .appendingPathComponent("Sources/TarsRealtimeAudioBridge/include/TarsRealtimeAudioBridge.h")
        )
        XCTAssertTrue(header.contains("bool hadAdmittedLoss;"))
        let removedOutcome = header.replacingOccurrences(
            of: "bool hadAdmittedLoss;",
            with: "bool removedAdmittedLoss;"
        )
        XCTAssertFalse(removedOutcome.contains("bool hadAdmittedLoss;"))

        let cSource = try String(
            contentsOf: packageRoot.appendingPathComponent(
                "Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c"
            )
        )
        func admittedLossAccountingPass(_ source: String) -> Bool {
            guard let callbackStart = source.range(of: "OSStatus TarsRealtimeAudioIOProc"),
                  let callbackEnd = source.range(
                      of: "OSStatus TarsRealtimeAudioCreateIOProc",
                      range: callbackStart.upperBound..<source.endIndex
                  ) else { return false }
            let callback = String(source[callbackStart.upperBound..<callbackEnd.lowerBound])
            guard let staleStart = callback.range(of: "if (!publish) {"),
                  let release = callback.range(
                      of: "atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);",
                      range: staleStart.lowerBound..<callback.endIndex
                  ) else { return false }
            let stalePath = callback[staleStart.lowerBound..<release.lowerBound]
            guard let lossObservation = stalePath.range(of: "terminalRetirementActive"),
                  let lossClaim = stalePath.range(of: "terminalRetirementHadAdmittedLoss"),
                  let lossRead = stalePath.range(of: "terminalRetirementLossReadIndex"),
                  let lossWrite = stalePath.range(of: "terminalRetirementLossWriteIndex") else { return false }
            return lossObservation.lowerBound < lossClaim.lowerBound &&
                lossClaim.lowerBound < lossRead.lowerBound &&
                lossRead.lowerBound < lossWrite.lowerBound
        }
        XCTAssertTrue(admittedLossAccountingPass(cSource))
        let accountingRemoved = cSource.replacingOccurrences(
            of: "if (atomic_load_explicit(&ring->terminalRetirementActive, memory_order_acquire)) {",
            with: "if (false) {",
            options: [.literal]
        )
        XCTAssertFalse(
            admittedLossAccountingPass(accountingRemoved),
            "removing admitted-loss accounting from the real callback must fail the guard"
        )
    }

    func testPublicationAdmissionTokenCannotCrossCloseReopenABA() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }

        // Model an IOProc that loaded an open gate and was paused immediately
        // before its one admission CAS.  Close and reopen the ring while that
        // stale token is held; the replacement generation must not let the old
        // CAS tag/publish bytes as generation 2.
        let staleToken = TarsRealtimeAudioRingLoadPublicationAdmissionTokenForTesting(ring)
        XCTAssertNotEqual(staleToken, 0)
        TarsRealtimeAudioRingSetGeneration(ring, 0)
        TarsRealtimeAudioRingSetGeneration(ring, 2)
        XCTAssertFalse(
            TarsRealtimeAudioRingTryCommitPublicationAdmissionTokenForTesting(ring, staleToken, 2),
            "a pre-close admission token must fail after close and nonzero reopen"
        )
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 0)

        let replacement = Data(repeating: 0xE2, count: 16)
        XCTAssertEqual(push(ring, data: [replacement], channels: [1], generation: 2), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        var outputBytes = Data(count: 64)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(output.generation, 2)
        XCTAssertEqual(Data(outputBytes.prefix(replacement.count)), replacement)
    }

    func testReopenCannotReuseSlotDuringStaleCleanup() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(1, 32, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        let expectedFormat = format
        let owner = RealtimeRingFixtureOwner(ring: ring)
        let probe = StaleCleanupProbe()
        let context = Unmanaged.passUnretained(probe).toOpaque()
        let hook: TarsRealtimeStaleCleanupHook = { context in
            guard let context else { return }
            let probe = Unmanaged<StaleCleanupProbe>.fromOpaque(context).takeUnretainedValue()
            probe.entered.signal()
            probe.release.wait()
        }
        TarsRealtimeAudioRingSetStaleCleanupHookForTesting(owner.ring, hook, context)
        let fenceProbe = PublicationFenceProbe()
        let fenceContext = Unmanaged.passUnretained(fenceProbe).toOpaque()
        let fenceHook: TarsRealtimePublicationFenceHook = { context in
            guard let context else { return }
            Unmanaged<PublicationFenceProbe>.fromOpaque(context).takeUnretainedValue().started.signal()
        }
        TarsRealtimeAudioRingSetPublicationFenceHookForTesting(owner.ring, fenceHook, fenceContext)
        defer {
            TarsRealtimeAudioRingSetStaleCleanupHookForTesting(owner.ring, nil, nil)
            TarsRealtimeAudioRingSetPublicationFenceHookForTesting(owner.ring, nil, nil)
        }
        TarsRealtimeAudioRingFenceBeforeNextPushPublicationForTesting(owner.ring)

        let oldData = Data(repeating: 0xA5, count: 16)
        let stalePushFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            var input = TarsRealtimeInputBuffer(data: nil, byteSize: UInt32(oldData.count), channels: 1)
            var descriptor = TarsRealtimeInputDescriptor(
                bufferCount: 1,
                buffers: nil,
                asbd: expectedFormat,
                sampleTime: 0,
                hostTime: 0,
                timestampFlags: 0,
                generation: 1
            )
            oldData.withUnsafeBytes { raw in
                input.data = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
                withUnsafeMutablePointer(to: &input) { inputPointer in
                    descriptor.buffers = UnsafePointer(inputPointer)
                    _ = TarsRealtimeAudioRingPush(owner.ring, &descriptor)
                }
            }
            stalePushFinished.signal()
        }
        XCTAssertEqual(probe.entered.wait(timeout: .now() + 1), .success)

        let fenceFinished = DispatchSemaphore(value: 0)
        DispatchQueue.global().async {
            TarsRealtimeAudioRingSetGeneration(owner.ring, 2)
            fenceFinished.signal()
        }
        XCTAssertEqual(fenceProbe.started.wait(timeout: .now() + 1), .success)

        let replacementResult = DescriptorResultProbe()
        let replacementFinished = DispatchSemaphore(value: 0)
        let replacementData = Data(repeating: 0x5A, count: 16)
        DispatchQueue.global().async {
            var input = TarsRealtimeInputBuffer(data: nil, byteSize: UInt32(replacementData.count), channels: 1)
            var descriptor = TarsRealtimeInputDescriptor(
                bufferCount: 1,
                buffers: nil,
                asbd: expectedFormat,
                sampleTime: 0,
                hostTime: 0,
                timestampFlags: 0,
                generation: 2
            )
            let result = replacementData.withUnsafeBytes { raw in
                input.data = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
                return withUnsafeMutablePointer(to: &input) { inputPointer in
                    descriptor.buffers = UnsafePointer(inputPointer)
                    return TarsRealtimeAudioRingPush(owner.ring, &descriptor)
                }
            }
            replacementResult.store(result)
            replacementFinished.signal()
        }
        // The fence has closed admission but is still held by the stale
        // callback.  A replacement producer must be rejected while closed;
        // it must not reuse the slot that stale cleanup is clearing.
        XCTAssertEqual(replacementFinished.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(replacementResult.value, TARS_REALTIME_DESCRIPTOR_STALE_GENERATION)

        probe.release.signal()
        XCTAssertEqual(stalePushFinished.wait(timeout: .now() + 1), .success)
        XCTAssertEqual(fenceFinished.wait(timeout: .now() + 1), .success)

        let newData = Data(repeating: 0x5A, count: 16)
        XCTAssertEqual(push(owner.ring, data: [newData], channels: [1], generation: 2, format: expectedFormat), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        var outputBytes = Data(count: 32)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(owner.ring, &output), 1)
        }
        XCTAssertEqual(Data(outputBytes.prefix(newData.count)), newData)
    }

    func testDescriptorCategoriesAreMutuallyExclusiveAndBounded() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(4, 16, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }
        XCTAssertEqual(push(ring, data: [Data()], channels: [1]), TARS_REALTIME_DESCRIPTOR_EMPTY)
        XCTAssertEqual(push(ring, data: [Data(repeating: 1, count: 20)], channels: [1]), TARS_REALTIME_DESCRIPTOR_CAPACITY_REJECTED)
        XCTAssertEqual(push(ring, data: [Data(repeating: 1, count: 4)], channels: [2]), TARS_REALTIME_DESCRIPTOR_MALFORMED)
        XCTAssertEqual(push(ring, data: [Data(repeating: 1, count: 4)], channels: [1], generation: 2), TARS_REALTIME_DESCRIPTOR_STALE_GENERATION)
        let counters = TarsRealtimeAudioRingSnapshot(ring)
        XCTAssertEqual(counters.emptyArrivals, 1)
        XCTAssertEqual(counters.capacityRejectedArrivals, 1)
        XCTAssertEqual(counters.malformedArrivals, 1)
        XCTAssertEqual(counters.staleGenerationArrivals, 1)
        XCTAssertEqual(counters.validNonemptyArrivals, 0)
    }

    func testUnexpectedASBDIsMalformedBeforeLivenessCounters() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }
        var changed = format
        changed.sampleRate = 44_100
        XCTAssertEqual(
            push(ring, data: [Data(repeating: 1, count: 4)], channels: [1], format: changed),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )
        let counters = TarsRealtimeAudioRingSnapshot(ring)
        XCTAssertEqual(counters.callbackArrivals, 1)
        XCTAssertEqual(counters.malformedArrivals, 1)
        XCTAssertEqual(counters.validNonemptyArrivals, 0)
    }

    func testDescriptorMatrixCoversNullEmptyPlanarAndAlignmentFailures() {
        var interleavedFormat = asbd()
        guard let interleaved = TarsRealtimeAudioRingCreate(8, 64, &interleavedFormat, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(interleaved) }
        XCTAssertEqual(
            pushRaw(interleaved, buffers: [], format: interleavedFormat),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )
        XCTAssertEqual(
            pushRaw(interleaved, buffers: [TarsRealtimeInputBuffer(data: nil, byteSize: 4, channels: 1)], format: interleavedFormat),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )
        XCTAssertEqual(
            push(interleaved, data: [Data()], channels: [1]),
            TARS_REALTIME_DESCRIPTOR_EMPTY
        )

        var planarFormat = asbd(interleaved: false, channels: 2)
        guard let planar = TarsRealtimeAudioRingCreate(8, 64, &planarFormat, 2, false, 1) else {
            return XCTFail("planar ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(planar) }
        XCTAssertEqual(
            push(planar, data: [Data(repeating: 1, count: 8), Data(repeating: 2, count: 4)], channels: [1, 1], format: planarFormat),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )
        XCTAssertEqual(
            push(planar, data: [Data(repeating: 1, count: 8), Data()], channels: [1, 1], format: planarFormat),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )
        XCTAssertEqual(
            push(planar, data: [Data(repeating: 1, count: 6), Data(repeating: 2, count: 6)], channels: [1, 1], format: planarFormat),
            TARS_REALTIME_DESCRIPTOR_MALFORMED
        )
        XCTAssertEqual(
            push(planar, data: [Data(repeating: 1, count: 8), Data(repeating: 2, count: 8)], channels: [1, 1], format: planarFormat),
            TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY
        )
    }

    func testConsumedAndDestroyedSlotsAreZeroizedThroughCAPIHook() {
        final class ZeroizationBox: @unchecked Sendable {
            let lock = NSLock()
            var snapshots: [[UInt8]] = []
        }
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(1, 32, &format, 1, true, 1) else {
            return XCTFail("ring allocation failed")
        }
        let box = ZeroizationBox()
        let context = Unmanaged.passUnretained(box).toOpaque()
        let hook: TarsRealtimeZeroizationHook = { bytes, count, context in
            guard let context else { return }
            let box = Unmanaged<ZeroizationBox>.fromOpaque(context).takeUnretainedValue()
            let values = bytes.map { pointer in Array(UnsafeBufferPointer(start: pointer, count: count)) } ?? []
            box.lock.withLock { box.snapshots.append(values) }
        }
        TarsRealtimeAudioRingSetZeroizationHook(ring, hook, context)
        XCTAssertEqual(push(ring, data: [Data(repeating: 0xA5, count: 16)], channels: [1]), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        var outputBytes = Data(count: 32)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        TarsRealtimeAudioRingDestroy(ring)
        let snapshots = box.lock.withLock { box.snapshots }
        XCTAssertEqual(snapshots.count, 1)
        XCTAssertEqual(snapshots.first, Array(repeating: 0, count: 32))
    }

    func testPopCopiesMetadataAndConsumesSlot() {
        var format = asbd()
        guard let ring = TarsRealtimeAudioRingCreate(2, 64, &format, 1, true, 9) else {
            return XCTFail("ring allocation failed")
        }
        defer { TarsRealtimeAudioRingDestroy(ring) }
        let input = Data(repeating: 0x7f, count: 16)
        XCTAssertEqual(push(ring, data: [input], channels: [1], generation: 9), TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY)
        var outputBytes = Data(count: 64)
        var output = TarsRealtimeSlotOutput(
            bufferCount: 0,
            bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
            bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
            totalBytes: 0,
            asbd: format,
            sampleTime: 0,
            hostTime: 0,
            timestampFlags: 0,
            generation: 0,
            bytes: nil,
            byteCapacity: 0
        )
        outputBytes.withUnsafeMutableBytes { raw in
            output.bytes = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
            output.byteCapacity = UInt32(raw.count)
            XCTAssertEqual(TarsRealtimeAudioRingPop(ring, &output), 1)
        }
        XCTAssertEqual(output.totalBytes, UInt32(input.count))
        XCTAssertEqual(output.generation, 9)
        XCTAssertEqual(TarsRealtimeAudioRingRetainedSlots(ring), 0)
        XCTAssertEqual(TarsRealtimeAudioRingSnapshot(ring).poppedCount, 1)
        XCTAssertTrue(
            TarsRealtimeAudioRingSlotIsZeroizedForTesting(ring, 0),
            "Pop must zeroize the reusable slot before the independent destroy hook runs"
        )
    }

    func testRealtimeContractDeclaresAnnotationAndNoAsyncOrAllocationPath() throws {
        let sourcePath = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c")
        let source = try String(contentsOf: sourcePath)
        XCTAssertTrue(source.contains("TarsRealtimeAudioIOProc") && source.contains("CA_REALTIME_API"))
        let callbackBody = source
            .components(separatedBy: "OSStatus TarsRealtimeAudioIOProc")
            .last?
            .components(separatedBy: "OSStatus TarsRealtimeAudioCreateIOProc")
            .first ?? ""
        XCTAssertFalse(callbackBody.contains("malloc"))
        XCTAssertFalse(callbackBody.contains("calloc"))
        XCTAssertFalse(callbackBody.contains("AudioFrame"))
        XCTAssertFalse(callbackBody.contains("AsyncStream"))
        XCTAssertFalse(callbackBody.contains("AVAudioConverter"))
        XCTAssertFalse(callbackBody.contains("atomic_flag"), "the IOProc must never use a lock")
        let publicationBody = callbackBody.components(separatedBy: "Revalidate at the publication boundary").last ?? ""
        XCTAssertFalse(publicationBody.contains("while ("), "the IOProc publication path must never spin")
        let admissionMarker = callbackBody.range(of: "publicationAdmitted = false")
        let callbackCounter = callbackBody.range(of: "TARS_REALTIME_SATURATING_INCREMENT(ring->callbackArrivals)")
        XCTAssertNotNil(admissionMarker)
        XCTAssertNotNil(callbackCounter)
        if let admissionMarker, let callbackCounter {
            XCTAssertGreaterThan(
                callbackCounter.lowerBound,
                admissionMarker.lowerBound,
                "the callback-arrival counter must remain inside the bounded publication lifetime"
            )
        }
    }

    func testEveryAtomicRingMemberIsExplicitlyInitializedBeforePublication() throws {
        let sourcePath = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c")
        let source = try String(contentsOf: sourcePath)
        guard let structStart = source.range(of: "struct TarsRealtimeAudioRing {"),
              let structEnd = source.range(of: "};", range: structStart.upperBound..<source.endIndex) else {
            return XCTFail("the ring definition must remain source-visible to the initialization guard")
        }
        let ringBody = String(source[structStart.upperBound..<structEnd.lowerBound])
        let regex = try NSRegularExpression(pattern: #"_Atomic\s+[A-Za-z_][A-Za-z0-9_]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*;"#)
        let matches = regex.matches(in: ringBody, range: NSRange(ringBody.startIndex..., in: ringBody))
        XCTAssertFalse(matches.isEmpty, "the guard must discover the ring's atomic members")
        for match in matches {
            guard let nameRange = Range(match.range(at: 1), in: ringBody) else {
                return XCTFail("the atomic member name must be extractable")
            }
            let name = String(ringBody[nameRange])
            XCTAssertTrue(
                source.contains("atomic_init(&ring->\(name),"),
                "ring atomic member \(name) must be explicitly atomic_init-initialized"
            )
        }
    }

    func testProductionIOProcStaleCleanupHoldsAdmissionThroughZeroizationAndCounters() throws {
        let sourcePath = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c")
        let source = try String(contentsOf: sourcePath)
        XCTAssertTrue(
            productionStaleCleanupOrderingPass(source),
            "the production callback must release publication admission only after stale cleanup and counters"
        )

        // Mutation-effective proof: an early release inserted immediately
        // before stale zeroization must fail the same ordering guard.  The
        // fixture-only hook cannot establish this property for the actual
        // production IOProc symbol, so this source gate is intentionally bound
        // to that callback body.
        let earlyReleaseMutation = source.replacingOccurrences(
            of: "        /* The SDK's memset_s declaration is not inferred nonblocking by",
            with: "        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);\n        /* The SDK's memset_s declaration is not inferred nonblocking by",
            options: [.literal]
        )
        XCTAssertFalse(productionStaleCleanupOrderingPass(earlyReleaseMutation))

        func productionStaleCleanupBoundedPass(_ body: String) -> Bool {
            guard let callbackStart = body.range(of: "OSStatus TarsRealtimeAudioIOProc"),
                  let callbackEnd = body.range(
                      of: "OSStatus TarsRealtimeAudioCreateIOProc",
                      range: callbackStart.upperBound..<body.endIndex
                  ) else { return false }
            let callback = String(body[callbackStart.upperBound..<callbackEnd.lowerBound])
            guard let publicationMarker = callback.range(of: "Revalidate at the publication boundary"),
                  let staleStart = callback.range(of: "if (!publish) {", range: publicationMarker.upperBound..<callback.endIndex),
                  let release = callback.range(
                      of: "atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);",
                      range: staleStart.lowerBound..<callback.endIndex
                  ) else { return false }
            let staleCleanup = callback[staleStart.lowerBound..<release.lowerBound]
            return staleCleanup.contains("const uint32_t staleByteCount = (uint32_t)totalBytes") &&
                staleCleanup.contains("zeroIndex < staleByteCount") &&
                !staleCleanup.contains("zeroIndex < ring->slotCapacity")
        }
        XCTAssertTrue(
            productionStaleCleanupBoundedPass(source),
            "stale realtime cleanup must be bounded by copied bytes, not the configured slot capacity"
        )
        let unboundedCleanupMutation = source.replacingOccurrences(
            of: "zeroIndex < staleByteCount",
            with: "zeroIndex < ring->slotCapacity",
            options: [.literal]
        )
        XCTAssertFalse(productionStaleCleanupBoundedPass(unboundedCleanupMutation))
    }

    func testCompilerEffectsGateRejectsAllocationHiddenBehindRealtimeHelper() throws {
        let fixture = """
        #include <CoreAudio/AudioHardware.h>
        #include <stdlib.h>
        static void *hidden_allocation(void *context) { (void)context; return malloc(1); }
        OSStatus fixture_callback(AudioObjectID device, const AudioTimeStamp *now,
                                  const AudioBufferList *input, const AudioTimeStamp *inputTime,
                                  AudioBufferList *output, const AudioTimeStamp *outputTime,
                                  void *context) CA_REALTIME_API {
            (void)device; (void)now; (void)input; (void)inputTime;
            (void)output; (void)outputTime; (void)hidden_allocation(context);
            return noErr;
        }
        """
        try withTemporaryFixture(fixture) { path in
            let result = try runXCRUN([
                "clang", "-fsyntax-only", "-Werror=function-effects",
                "-isysroot", try runXCRUN(["--show-sdk-path"]).output.trimmingCharacters(in: .whitespacesAndNewlines),
                path.path
            ])
            XCTAssertNotEqual(result.status, 0, "the realtime compiler gate unexpectedly accepted hidden allocation: \(result.output)")
            XCTAssertTrue(result.output.contains("function effects") || result.output.contains("nonblocking"), result.output)
        }
    }

    func testClangASTGateAcceptsProductionCallbackAndRejectsReachabilityMutations() throws {
        let production = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources/TarsRealtimeAudioBridge/TarsRealtimeAudioBridge.c")
        let productionAST = try compileAndDumpAST(production)
        XCTAssertEqual(productionAST.status, 0, productionAST.output)
        XCTAssertTrue(try astReachabilityPass(functionName: "TarsRealtimeAudioIOProc", astOutput: productionAST.output))

        let directHelperFixture = """
        #include <CoreAudio/AudioHardware.h>
        static volatile int side_effect;
        static void extra_helper(void) CA_REALTIME_API { side_effect = 1; }
        OSStatus fixture_callback(AudioObjectID device, const AudioTimeStamp *now,
                                  const AudioBufferList *input, const AudioTimeStamp *inputTime,
                                  AudioBufferList *output, const AudioTimeStamp *outputTime,
                                  void *context) CA_REALTIME_API {
            (void)device; (void)now; (void)input; (void)inputTime;
            (void)output; (void)outputTime; (void)context; extra_helper(); return noErr;
        }
        """
        try withTemporaryFixture(directHelperFixture) { path in
            let compiled = try compileAndDumpAST(path)
            XCTAssertEqual(compiled.status, 0, compiled.output)
            XCTAssertFalse(try astReachabilityPass(functionName: "fixture_callback", astOutput: compiled.output))
        }

        let indirectFixture = """
        #include <CoreAudio/AudioHardware.h>
        typedef void (*RealtimeFunction)(void);
        static void extra_helper(void) CA_REALTIME_API { }
        OSStatus fixture_callback(AudioObjectID device, const AudioTimeStamp *now,
                                  const AudioBufferList *input, const AudioTimeStamp *inputTime,
                                  AudioBufferList *output, const AudioTimeStamp *outputTime,
                                  void *context) CA_REALTIME_API {
            (void)device; (void)now; (void)input; (void)inputTime;
            (void)output; (void)outputTime; (void)context;
            RealtimeFunction function = extra_helper; function(); return noErr;
        }
        """
        try withTemporaryFixture(indirectFixture) { path in
            let compiled = try compileAndDumpAST(path)
            XCTAssertEqual(compiled.status, 0, compiled.output)
            XCTAssertFalse(try astReachabilityPass(functionName: "fixture_callback", astOutput: compiled.output))
        }

        let memsetFixture = """
        #include <CoreAudio/AudioHardware.h>
        #include <string.h>
        OSStatus fixture_callback(AudioObjectID device, const AudioTimeStamp *now,
                                  const AudioBufferList *input, const AudioTimeStamp *inputTime,
                                  AudioBufferList *output, const AudioTimeStamp *outputTime,
                                  void *context) CA_REALTIME_API {
            (void)device; (void)now; (void)input; (void)inputTime;
            (void)output; (void)outputTime; (void)context;
            char bytes[8] = {0};
            (void)memset_s(bytes, sizeof(bytes), 0, sizeof(bytes));
            return noErr;
        }
        """
        try withTemporaryFixture(memsetFixture) { path in
            let compiled = try compileAndDumpAST(path)
            XCTAssertEqual(compiled.status, 0, compiled.output)
            XCTAssertFalse(
                try astReachabilityPass(functionName: "fixture_callback", astOutput: compiled.output),
                "memset_s must remain outside the realtime callback allowlist"
            )
        }
    }
}
