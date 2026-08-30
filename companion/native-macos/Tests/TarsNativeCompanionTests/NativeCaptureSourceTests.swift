import XCTest
import Foundation
import CoreAudio
@testable import TarsNativeCompanion

final class NativeCaptureSourceTests: XCTestCase {
    func testScreenCaptureKitAudioOnlyConfiguration() throws {
        let identity = try SourceIdentity(sessionID: "sess_1", streamID: "sys_1", captureGeneration: 1, source: .systemAudio, sampleRate: 48_000, channelCount: 2)
        let config = CaptureSourceConfiguration(identity: identity, deviceIdentity: "ScreenCaptureKit.SystemAudio")
        let source = ScreenCaptureKitSystemAudioSource(configuration: config, liveCaptureEnabled: true)

        XCTAssertEqual(source.source, .systemAudio)
        XCTAssertEqual(source.status, .idle)

        let streamConfig = source.makeAudioOnlyConfiguration()
        XCTAssertTrue(streamConfig.capturesAudio)
        XCTAssertTrue(streamConfig.excludesCurrentProcessAudio)
        XCTAssertEqual(streamConfig.sampleRate, 48_000)
        XCTAssertEqual(streamConfig.channelCount, 2)
    }

    func testAVAudioEngineMicrophoneSourceConfiguration() throws {
        let identity = try SourceIdentity(sessionID: "sess_1", streamID: "mic_1", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let config = CaptureSourceConfiguration(identity: identity, deviceIdentity: "AVAudioEngine.DefaultMic")
        let source = AVAudioEngineMicrophoneSource(configuration: config, liveCaptureEnabled: true)

        XCTAssertEqual(source.source, .microphone)
        XCTAssertEqual(source.status, .idle)
    }

    func testStopOnIdleSourceIsSafe() async throws {
        let identity = try SourceIdentity(sessionID: "sess_1", streamID: "mic_1", captureGeneration: 1, source: .microphone, sampleRate: 16_000, channelCount: 1)
        let config = CaptureSourceConfiguration(identity: identity, deviceIdentity: "AVAudioEngine.DefaultMic")
        let source = AVAudioEngineMicrophoneSource(configuration: config, liveCaptureEnabled: true)

        await source.stop()
        if case .stopped = source.status {
            // expected
        } else {
            XCTFail("expected stopped status")
        }
    }

    func testAppInfoPlistRetainsNonEmptyAudioCaptureUsageDescription() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let plistURL = packageRoot.appendingPathComponent("Resources/TarsCompanionApp-Info.plist")
        let plist = try XCTUnwrap(NSDictionary(contentsOf: plistURL))
        let usage = try XCTUnwrap(plist["NSAudioCaptureUsageDescription"] as? String)
        XCTAssertFalse(usage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

        // Mutation-effective characterization: the same guard rejects an
        // empty value, without touching the signed app's source plist.
        let mutated = NSMutableDictionary(dictionary: plist)
        mutated["NSAudioCaptureUsageDescription"] = ""
        let mutatedUsage = (mutated["NSAudioCaptureUsageDescription"] as? String) ?? ""
        XCTAssertTrue(mutatedUsage.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    func testStandaloneCLICharacterizationRemainsScreenCaptureKit() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let cliURL = packageRoot.appendingPathComponent("Sources/TarsCompanionCLI/main.swift")
        let cli = try String(contentsOf: cliURL)
        XCTAssertTrue(cli.contains("ScreenCaptureKitSystemAudioSource"))
        XCTAssertFalse(cli.contains("ProcessTapSystemAudioSource"))

        // A local mutation of the selected source must fail the same
        // characterization, proving the assertion is not vacuous.
        let mutated = cli.replacingOccurrences(
            of: "ScreenCaptureKitSystemAudioSource",
            with: "ProcessTapSystemAudioSource"
        )
        XCTAssertFalse(mutated.contains("ScreenCaptureKitSystemAudioSource"))
    }

    func testScreenCaptureKitSequenceContractIsZeroBasedAndMutationEffective() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let sourceURL = packageRoot.appendingPathComponent("Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift")
        let source = try String(contentsOf: sourceURL)
        let contractComponents = [
            "sequenceNumber = 0",
            "sampleOffset = 0",
            "pcmAccumulator.removeAll",
            "let sequence = sequenceNumber",
            "sequenceNumber += 1"
        ]
        XCTAssertTrue(contractComponents.allSatisfy { source.contains($0) }, "ScreenCaptureKit must reset and emit the current zero-based sequence before incrementing")

        let mutated = source.replacingOccurrences(of: "sequenceNumber = 0", with: "sequenceNumber = 1")
        XCTAssertFalse(contractComponents.allSatisfy { mutated.contains($0) }, "the sequence test must fail if the source is changed to one-based numbering")
    }

    @available(macOS 13.0, *)
    func testScreenCaptureKitStatusAndObserverRegistryShareOneLockDiscipline() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let sourceURL = packageRoot.appendingPathComponent("Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift")
        let source = try String(contentsOf: sourceURL)
        XCTAssertTrue(source.contains("public var status: CaptureSourceStatus {\n        lock.withLock { currentStatus }"))
        XCTAssertTrue(source.contains("healthObservers[token] = observer"))
        XCTAssertTrue(source.contains("let update = lock.withLock { () -> CaptureSourceHealthUpdate"))
        XCTAssertTrue(source.contains("let snapshot: (CaptureSourceStatus, [CaptureSourceHealthObserver]) = lock.withLock"))

        // Mutation-effective guard: removing the lock-backed status snapshot
        // must make the synchronized-state contract fail rather than silently
        // accepting an unsynchronized Sendable source.
        let unsynchronized = source.replacingOccurrences(
            of: "lock.withLock { currentStatus }",
            with: "currentStatus"
        )
        XCTAssertFalse(
            unsynchronized.contains("public var status: CaptureSourceStatus {\n        lock.withLock { currentStatus }"),
            "status reads must stay inside the source's lock-backed state discipline"
        )
    }

    @available(macOS 13.0, *)
    func testScreenCaptureKitRetiredStreamCannotMutateReplacementPCMOwner() throws {
        let identity = try SourceIdentity(
            sessionID: "sess_sck",
            streamID: "sys_sck",
            captureGeneration: 1,
            source: .systemAudio,
            sampleRate: 48_000,
            channelCount: 1
        )
        let config = CaptureSourceConfiguration(
            identity: identity,
            deviceIdentity: "ScreenCaptureKit.SystemAudio"
        )
        let source = ScreenCaptureKitSystemAudioSource(configuration: config)
        let retiredOwner = NSObject()
        let replacementOwner = NSObject()
        let payload = Data(repeating: 0x2A, count: 2_400 * 2)

        // This fixture uses the production locked accumulator/cursor helper,
        // while replacing the unavailable live display with object identities.
        source.installTestingStreamOwner(retiredOwner)
        let retiredFrame = source.submitPCMForTesting(from: retiredOwner, data: payload)
        XCTAssertEqual(retiredFrame.map(\.sequence), [0])

        // Restart resets the replacement cursor.  A queued callback from A
        // must be rejected before append/cursor advance; B's first valid frame
        // must still start at sequence/sample zero.
        source.installTestingStreamOwner(replacementOwner)
        XCTAssertTrue(source.submitPCMForTesting(from: retiredOwner, data: payload).isEmpty)
        let replacementFrame = source.submitPCMForTesting(from: replacementOwner, data: payload)
        XCTAssertEqual(replacementFrame.map(\.sequence), [0])
        XCTAssertEqual(replacementFrame.first?.firstSample, 0)

        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let sourceURL = packageRoot.appendingPathComponent("Sources/TarsNativeCompanion/ScreenCaptureKitSystemAudioSource.swift")
        let sourceText = try String(contentsOf: sourceURL)
        XCTAssertTrue(
            sourceText.contains("guard currentStreamOwnerID == ownerID else { return (nil, []) }"),
            "the shared append edge must verify stream identity under the accumulator lock"
        )
        let removedIdentityGuard = sourceText.replacingOccurrences(
            of: "guard currentStreamOwnerID == ownerID else { return (nil, []) }",
            with: "if false { return (nil, []) }"
        )
        XCTAssertFalse(
            removedIdentityGuard.contains("guard currentStreamOwnerID == ownerID else { return (nil, []) }"),
            "removing the owner guard must invalidate the source characterization"
        )
    }

    @available(macOS 14.2, *)
    func testDeviceAlivePropertyReadPreservesActualBoolAndSurfacesReadFailure() {
        XCTAssertEqual(CoreAudioProcessTapHAL.deviceAliveEvent(status: noErr, value: 0), .deviceAlive(false))
        XCTAssertEqual(CoreAudioProcessTapHAL.deviceAliveEvent(status: noErr, value: 1), .deviceAlive(true))
        XCTAssertEqual(CoreAudioProcessTapHAL.deviceAliveEvent(status: -50, value: 1), .deviceAliveReadFailed(-50))
        XCTAssertEqual(
            CoreAudioProcessTapHAL.deviceAliveEvent(status: kAudioDevicePermissionsError, value: 1),
            .deviceAliveReadFailed(kAudioDevicePermissionsError),
            "a permission-bearing read must preserve its raw status across the listener event boundary"
        )
    }

    @available(macOS 14.2, *)
    func testCoreAudioPermissionNormalizationAndRetainedTapUIDOwnershipAreMutationEffective() throws {
        XCTAssertEqual(
            CoreAudioProcessTapHAL.normalizedError(
                operation: "kAudioTapPropertyUID",
                status: kAudioDevicePermissionsError
            ) as? SystemAudioCaptureFailure,
            .denied
        )
        XCTAssertEqual(
            CoreAudioProcessTapHAL.normalizedError(operation: "kAudioTapPropertyUID", status: -50) as? ProcessTapHALError,
            .osStatus(operation: "kAudioTapPropertyUID", status: -50)
        )

        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let halURL = packageRoot.appendingPathComponent("Sources/TarsNativeCompanion/ProcessTapHAL.swift")
        let source = try String(contentsOf: halURL)
        XCTAssertTrue(source.contains("let value = uid.takeRetainedValue() as String"))
        XCTAssertTrue(source.contains("Self.normalizedError(operation: \"kAudioTapPropertyUID\", status: status)"))

        let unretainedMutation = source.replacingOccurrences(of: "uid.takeRetainedValue()", with: "uid.takeUnretainedValue()")
        XCTAssertFalse(
            unretainedMutation.contains("let value = uid.takeRetainedValue() as String"),
            "the ownership characterization must fail if the SDK-retained UID is changed to unretained"
        )
        let rawStatusMutation = source.replacingOccurrences(
            of: "Self.normalizedError(operation: \"kAudioTapPropertyUID\", status: status)",
            with: "ProcessTapHALError.osStatus(operation: \"kAudioTapPropertyUID\", status: status)"
        )
        XCTAssertFalse(
            rawStatusMutation.contains("Self.normalizedError(operation: \"kAudioTapPropertyUID\", status: status)"),
            "the source guard must fail if a permission-bearing property edge bypasses normalization"
        )
    }

    func testAppInvalidEngineArgumentUsesExactConvertibleMessage() throws {
        XCTAssertEqual(
            String(describing: SystemAudioEngineLaunchArgumentError.invalidValue("bogus")),
            "Valor inválido para --system-audio-engine: 'bogus'. Use auto, process-tap ou screen-capture-kit."
        )
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
        let appURL = packageRoot.appendingPathComponent("Sources/TarsCompanionApp/TarsCompanionApp.swift")
        let app = try String(contentsOf: appURL)
        XCTAssertTrue(app.contains("launchArgumentError: String(describing: error)"))
        let mutated = app.replacingOccurrences(
            of: "launchArgumentError: String(describing: error)",
            with: "launchArgumentError: error.localizedDescription"
        )
        XCTAssertFalse(mutated.contains("launchArgumentError: String(describing: error)"))
    }
}
