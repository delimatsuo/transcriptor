import CoreAudio
import XCTest
@testable import TarsNativeCompanion

final class SystemAudioCaptureMonitorTests: XCTestCase {
    private func counters(callbacks: UInt64 = 0, valid: UInt64 = 0) -> SystemAudioCaptureCounterSnapshot {
        SystemAudioCaptureCounterSnapshot(callbackArrivals: callbacks, validNonemptyArrivals: valid)
    }

    func testStartSuccessIsUnknownAndSilentNonemptyStaysUnknown() {
        let monitor = SystemAudioCaptureMonitor(generation: 1, deadlineNanoseconds: 2)
        _ = monitor.resetForNewUserStart(generation: 1, counters: counters(), nowNanoseconds: 0)
        XCTAssertEqual(monitor.permission, .unknown)
        XCTAssertEqual(monitor.observeSilentNonempty(), .permissionUnknown)
        XCTAssertEqual(monitor.permission, .unknown)
    }

    func testFunctionalSignalGrantsButPermissionErrorDeniesExactly() {
        let monitor = SystemAudioCaptureMonitor(generation: 1, deadlineNanoseconds: 2)
        _ = monitor.resetForNewUserStart(generation: 1, counters: counters(), nowNanoseconds: 0)
        XCTAssertEqual(monitor.observeFunctionalNonzeroSignal(), .granted)
        XCTAssertEqual(monitor.permission, .granted)
        XCTAssertEqual(monitor.observePermissionError(), .denied(SystemAudioCaptureMonitor.permissionDeniedMessage))
    }

    func testOnlyEmptyCallbacksReachOneBoundedRebuildThenAmbiguousFailure() {
        let monitor = SystemAudioCaptureMonitor(generation: 3, deadlineNanoseconds: 2)
        _ = monitor.resetForNewUserStart(generation: 3, counters: counters(), nowNanoseconds: 0)
        XCTAssertEqual(monitor.deadlineFired(generation: 3, nowNanoseconds: 2), .rebuild(reason: "no-buffer-watchdog"))
        _ = monitor.beginRebuildGeneration(4, counters: counters(), nowNanoseconds: 2)
        XCTAssertEqual(monitor.deadlineFired(generation: 4, nowNanoseconds: 4), .ambiguous(SystemAudioCaptureMonitor.ambiguousCaptureMessage))
    }

    func testValidCounterCancelsDeadlineEvenWhenRingOverflowIsObserved() {
        let monitor = SystemAudioCaptureMonitor(generation: 1, deadlineNanoseconds: 2)
        _ = monitor.resetForNewUserStart(generation: 1, counters: counters(), nowNanoseconds: 0)
        XCTAssertEqual(monitor.observeCounters(SystemAudioCaptureCounterSnapshot(callbackArrivals: 1, validNonemptyArrivals: 1, ringOverflowCount: 1)), .none)
        XCTAssertEqual(monitor.deadlineFired(generation: 1, nowNanoseconds: 2), .none)
    }

    func testStaleDeadlineAndStopCancellationHaveNoEffect() {
        let monitor = SystemAudioCaptureMonitor(generation: 7, deadlineNanoseconds: 2)
        _ = monitor.resetForNewUserStart(generation: 7, counters: counters(), nowNanoseconds: 0)
        XCTAssertEqual(monitor.deadlineFired(generation: 6, nowNanoseconds: 2), .none)
        monitor.cancelDeadline()
        XCTAssertEqual(monitor.deadlineFired(generation: 7, nowNanoseconds: 2), .none)
    }

    func testNonPermissionStatusDoesNotUseDenialCopy() {
        let monitor = SystemAudioCaptureMonitor(generation: 1)
        let action = monitor.observeFailure("unsupported ASBD")
        guard case .failed(let message) = action else { return XCTFail("expected non-permission failure") }
        XCTAssertFalse(message == SystemAudioCaptureMonitor.permissionDeniedMessage)
        XCTAssertFalse(message.contains("permissão negada"))
    }

    func testMalformedAndCapacityRejectedCountersFailLoudlyWithoutAmbiguousWatchdog() {
        let malformed = SystemAudioCaptureMonitor(generation: 1, deadlineNanoseconds: 2)
        _ = malformed.resetForNewUserStart(generation: 1, counters: counters(), nowNanoseconds: 0)
        guard case .failed(let malformedMessage) = malformed.observeCounters(
            SystemAudioCaptureCounterSnapshot(callbackArrivals: 1, malformedArrivals: 1)
        ) else {
            return XCTFail("malformed descriptors must be a non-permission failure")
        }
        XCTAssertFalse(malformedMessage == SystemAudioCaptureMonitor.ambiguousCaptureMessage)

        let capacity = SystemAudioCaptureMonitor(generation: 1, deadlineNanoseconds: 2)
        _ = capacity.resetForNewUserStart(generation: 1, counters: counters(), nowNanoseconds: 0)
        guard case .failed(let capacityMessage) = capacity.observeCounters(
            SystemAudioCaptureCounterSnapshot(callbackArrivals: 1, capacityRejectedArrivals: 1)
        ) else {
            return XCTFail("capacity rejection must be a non-permission failure")
        }
        XCTAssertFalse(capacityMessage == SystemAudioCaptureMonitor.ambiguousCaptureMessage)
    }

    func testCursorOverflowIsAStableTerminalObservation() {
        let monitor = SystemAudioCaptureMonitor(generation: 1, deadlineNanoseconds: 2)
        _ = monitor.resetForNewUserStart(generation: 1, counters: counters(), nowNanoseconds: 0)
        let action = monitor.observeCounters(
            SystemAudioCaptureCounterSnapshot(callbackArrivals: 1, cursorOverflow: 1)
        )
        guard case .failed(let message) = action else {
            return XCTFail("cursor exhaustion must stop before an ambiguous wrap")
        }
        XCTAssertTrue(message.contains("limite suportado"))
        XCTAssertFalse(monitor.deadlineArmed)
        XCTAssertEqual(monitor.observeFunctionalNonzeroSignal(), action)
    }

    func testPublicMonitorReadsRemainCoherentDuringConcurrentMutation() {
        let monitor = SystemAudioCaptureMonitor(generation: 1, deadlineNanoseconds: 2)
        _ = monitor.resetForNewUserStart(generation: 1, counters: counters(), nowNanoseconds: 0)

        DispatchQueue.concurrentPerform(iterations: 128) { index in
            if index.isMultiple(of: 4) {
                _ = monitor.beginRebuildGeneration(
                    UInt64(index + 2),
                    counters: SystemAudioCaptureCounterSnapshot(
                        callbackArrivals: UInt64(index),
                        validNonemptyArrivals: UInt64(index)
                    ),
                    nowNanoseconds: UInt64(index)
                )
            } else if index.isMultiple(of: 3) {
                _ = monitor.observeFunctionalNonzeroSignal()
            } else if index.isMultiple(of: 2) {
                monitor.cancelDeadline()
            } else {
                _ = monitor.deadlineFired(
                    generation: monitor.generation,
                    nowNanoseconds: UInt64(index)
                )
            }

            // These are the public cross-queue reads that previously came
            // from synthesized unsynchronized storage.  Each getter must
            // observe a complete value while the mutations above race it.
            _ = monitor.generation
            _ = monitor.permission
            _ = monitor.deadlineArmed
            _ = monitor.rebuildUsed
        }

        XCTAssertGreaterThan(monitor.generation, 0)
        XCTAssertTrue([PermissionState.unknown, .granted, .denied].contains(monitor.permission))
    }
}
