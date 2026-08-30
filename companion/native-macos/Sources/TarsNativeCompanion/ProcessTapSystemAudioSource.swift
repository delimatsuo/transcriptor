import CoreAudio
import Foundation
import TarsRealtimeAudioBridge

@available(macOS 14.2, *)
public final class ProcessTapSystemAudioSource: CaptureSource, @unchecked Sendable {
    public let source: AudioSource = .systemAudio
    public let configuration: CaptureSourceConfiguration
    private static let permissionDeniedStartupMarker = "permission-denied"
    public var sink: CaptureFrameSink? {
        get { stateLock.withLock { configuredSink } }
        set { stateLock.withLock { configuredSink = newValue } }
    }

    public var status: CaptureSourceStatus {
        stateLock.withLock { currentStatus }
    }

    public typealias UUIDFactory = @Sendable () -> String
    public typealias MonotonicClock = @Sendable () -> UInt64
    public typealias WallClock = @Sendable () -> UInt64
    public typealias DrainScratchFactory = @Sendable (Int) -> Data

    private let hal: ProcessTapHALBoundary
    private let uuidFactory: UUIDFactory
    private let monotonicClock: MonotonicClock
    private let wallClock: WallClock
    private let stopDeadlineNanoseconds: UInt64
    private let ringSlotCount: UInt32
    private let ringSlotCapacity: UInt32
    private let deliveryQueueCapacity: Int
    private let drainScratchFactory: DrainScratchFactory
    private var drainScratch: Data
    private let stateLock = NSLock()
    private var currentStatus: CaptureSourceStatus = .idle
    private var configuredSink: CaptureFrameSink?
    private var observers: [CaptureSourceObserverToken: CaptureSourceHealthObserver] = [:]

    private var lifecycleState: LifecycleState = .idle
    private var lifecycleEpoch: UInt64 = 0
    private var currentGeneration: UInt64
    private var startupAbort: (generation: UInt64, operation: UInt64, reason: String)?
    private var processObjectID: UInt32?
    private var tapID: UInt32?
    private var aggregateID: UInt32?
    private var ioProcID: UInt64?
    private var ring: OpaquePointer?
    private var tapAttached = false
    private var converter: CanonicalSystemAudioConverter?
    private var inputFormat: ProcessTapPCMFormat?
    private var listenerTokens: [UInt64] = []
    private var aggregateStarted = false
    // Cleanup may destroy the aggregate while an IOProc-destruction call is
    // transiently failing.  Keep only the opaque device context needed to
    // retry that exact IOProc edge; it is not treated as a live aggregate
    // ownership edge and is cleared after the retry succeeds.
    private var pendingIOProcAggregateID: UInt32?
    private var aggregateDestroySucceeded = false
    private var stopFailureOutstanding = false
    // Terminal retirement owns the one destructive C ring transition.  The
    // later generic teardown edge transfers that fenced ownership instead of
    // issuing a second SetGeneration(0) against the same ring.
    private var terminalRetiredGeneration: UInt64?
    private var monitor: SystemAudioCaptureMonitor
    private var watchdogTask: Task<Void, Never>?
    // The watchdog may legitimately expire while the HAL start call is still
    // executing.  It is recorded, not acted upon, until the acquisition owner
    // has published a running graph and can safely hand recovery ownership to
    // the normal lifecycle path.
    private var watchdogExpiredDuringStart: (generation: UInt64, nowNanoseconds: UInt64)?
    private var drainTask: Task<Void, Never>?
    // The C ring is SPSC.  The production drain loop and deterministic test
    // drains must therefore share one consumer lock.
    private let drainLock = NSLock()
    // Serializes every Swift-side ring access with fencing and destruction.
    // Core Audio itself quiesces the production IOProc during stop; this lock
    // closes the equivalent source/test race for watchdog and fixture calls.
    private let ringUseLock = NSLock()
    private var deliveryWorker: ProcessTapDeliveryWorker?
    private var pendingGaps: [CoverageGap] = []
    private var terminalEvidenceGaps: [CoverageGap] = []
    // Each entry is the number of raw slots that were retained at the point
    // one overflow episode was observed.  Keeping episodes in FIFO order and
    // retaining the absolute producer write cursor is necessary when a
    // consumer pop makes room for a successful enqueue and a later callback
    // overflows again before the first marker is emitted.
    private struct PendingOverflowBoundary {
        let producerWriteIndex: UInt64
        let producerReadIndex: UInt64
        let episodeNumber: UInt64
    }

    // Sleep/wake callbacks arrive from the HAL on a queue that is independent
    // of Swift task scheduling. Keep callback order and drain it FIFO so a
    // delayed sleep task cannot be overtaken by a later wake task.
    private struct PendingSleepWakeEvent {
        let event: ProcessTapHALEvent
        let generation: UInt64
        let operation: UInt64?
        let completion: CompletionLatch
    }

    // Producer-side metadata is consumed FIFO.  Each absolute write cursor
    // is captured at the exact callback that first observed a full ring; the
    // drain never reconstructs a boundary from a moving retained-slot count.
    private var pendingOverflowBoundaries: [PendingOverflowBoundary] = []
    private var pendingSleepWakeEvents: [PendingSleepWakeEvent] = []
    private var sleepWakeEventTaskActive = false
    private var sleepWakeEventTaskToken: UInt64 = 0
    private var usedAggregateUIDs: Set<String> = []
    private var sleepingGeneration: UInt64?
    private let quarantine = ProcessTapQuarantineRegistry()
    private var cleanupDiagnostic: String?
    private var rebuildTask: Task<Void, Never>?
    private var teardownTask: Task<Void, Never>?
    private var startCompletion: CompletionLatch?
    private var failureTask: Task<Void, Never>?
    // Every concurrent user stop joins this exact owner task.  The owner is
    // installed while the lifecycle lock is held, before it can create the
    // lower-level teardown task, so no caller can observe an early return.
    private var stopOwnerTask: Task<Void, Never>?
    private var stopOwnerOperation: UInt64?
    private var stopOwnerHadCleanupFailure = false
    private var terminalFailureOwnerToken: UInt64?
    private var nextTerminalFailureOwnerToken: UInt64 = 0
    private var terminalFailureCompletion: CompletionLatch?
    private var teardownTaskOwner: UInt64?
    private var nextTeardownTaskOwner: UInt64 = 0
    private var teardownCompletionHookForTesting: (@Sendable (UInt64) -> Void)?
    private var stopClaimHookForTesting: (@Sendable () -> Void)?
    private var stopOwnerPauseHookForTesting: (@Sendable () async -> Void)?
    private var ringFenceHookForTesting: (@Sendable () -> Void)?
    private var acquisitionPauseHookForTesting: (@Sendable () async -> Void)?
    private var teardownPauseHookForTesting: (@Sendable () async -> Void)?
    private var terminalFailureClaimHookForTesting: (@Sendable () -> Void)?
    private var terminalRetirementHookForTesting: (@Sendable (OpaquePointer) -> Void)?
    private var watchdogSnapshotHookForTesting: (@Sendable () -> Void)?
    private var deliveryFenceHookForTesting: (@Sendable () -> Void)?
    private var deliveryWorkerStartHookForTesting: (@Sendable (ProcessTapDeliveryWorker) -> Void)?
    private var deliveryWorkerActivationHookForTesting: (@Sendable (ProcessTapDeliveryWorker) -> Void)?
    private var deliveryAdmissionHookForTesting: (@Sendable () -> Void)?
    private var conversionHookForTesting: (@Sendable () -> Void)?
    private var sleepWakeDispatchHookForTesting: (@Sendable (ProcessTapHALEvent) -> Void)?
    private var pauseDrainForTesting = false

    private enum LifecycleState {
        case idle
        case starting
        case running
        case stopping
        case failed
    }

    private enum RecoveryPlan {
        case join(Task<Void, Never>)
        case fail(message: String, generation: UInt64, operation: UInt64)
        case ignore
    }

    public init(
        configuration: CaptureSourceConfiguration,
        liveCaptureEnabled: Bool = true,
        sink: CaptureFrameSink? = nil,
        hal: ProcessTapHALBoundary? = nil,
        uuidFactory: @escaping UUIDFactory = { UUID().uuidString },
        monotonicClock: @escaping MonotonicClock = { DispatchTime.now().uptimeNanoseconds },
        wallClock: @escaping WallClock = { UInt64(Date().timeIntervalSince1970 * 1_000) },
        stopDeadlineNanoseconds: UInt64 = 2_000_000_000,
        ringSlotCount: UInt32 = 64,
        ringSlotCapacity: UInt32 = 1_048_576,
        deliveryQueueCapacity: Int = 256,
        drainScratchFactory: @escaping DrainScratchFactory = { Data(count: $0) }
    ) {
        // Kept as a source-compatible initializer label for older native
        // sources.  Process Tap tests always exercise the injected HAL graph;
        // this value never bypasses that graph.
        _ = liveCaptureEnabled
        self.configuration = configuration
        self.configuredSink = sink
        self.hal = hal ?? CoreAudioProcessTapHAL()
        self.uuidFactory = uuidFactory
        self.monotonicClock = monotonicClock
        self.wallClock = wallClock
        self.stopDeadlineNanoseconds = stopDeadlineNanoseconds
        self.ringSlotCount = ringSlotCount
        self.ringSlotCapacity = ringSlotCapacity
        self.deliveryQueueCapacity = max(2, deliveryQueueCapacity)
        self.drainScratchFactory = drainScratchFactory
        // A 1 MiB scratch buffer belongs to the active drain lifecycle, not
        // to an idle source.  Allocate it only after the ring is established
        // and before the first drain task can run.
        self.drainScratch = Data()
        self.currentGeneration = configuration.identity.captureGeneration
        self.monitor = SystemAudioCaptureMonitor(generation: configuration.identity.captureGeneration)
    }

    public convenience init(
        configuration: CaptureSourceConfiguration,
        sink: CaptureFrameSink,
        hal: ProcessTapHALBoundary,
        uuidFactory: @escaping UUIDFactory = { UUID().uuidString },
        monotonicClock: @escaping MonotonicClock = { DispatchTime.now().uptimeNanoseconds },
        wallClock: @escaping WallClock = { UInt64(Date().timeIntervalSince1970 * 1_000) },
        stopDeadlineNanoseconds: UInt64 = 2_000_000_000,
        deliveryQueueCapacity: Int = 256,
        drainScratchFactory: @escaping DrainScratchFactory = { Data(count: $0) }
    ) {
        self.init(
            configuration: configuration,
            liveCaptureEnabled: true,
            sink: sink,
            hal: hal,
            uuidFactory: uuidFactory,
            monotonicClock: monotonicClock,
            wallClock: wallClock,
            stopDeadlineNanoseconds: stopDeadlineNanoseconds,
            deliveryQueueCapacity: deliveryQueueCapacity,
            drainScratchFactory: drainScratchFactory
        )
    }

    public func installHealthObserver(_ observer: @escaping CaptureSourceHealthObserver) -> CaptureSourceObserverToken {
        let token = CaptureSourceObserverToken()
        let update: CaptureSourceHealthUpdate = stateLock.withLock {
            observers[token] = observer
            return CaptureSourceHealthUpdate(source: source, generation: currentGeneration, status: currentStatus)
        }
        observer(update)
        return token
    }

    public func removeHealthObserver(_ token: CaptureSourceObserverToken) {
        _ = stateLock.withLock { observers.removeValue(forKey: token) }
    }

    public func start() async throws {
        guard quarantine.isEmpty else {
            let message = "A captura anterior ainda possui uma chamada de sink em andamento; aguarde a finalização antes de reiniciar."
            publish(.failed(message), generation: currentGeneration)
            throw CompanionError.invalid(message)
        }
        if let cleanupDiagnostic = stateLock.withLock({ self.cleanupDiagnostic }) {
            let message = SystemAudioCaptureFailure.cleanupFailure(cleanupDiagnostic).description
            publish(.failed(message), generation: currentGeneration)
            throw SystemAudioCaptureFailure.cleanupFailure(cleanupDiagnostic)
        }
        let needsFailedGraphTeardown = stateLock.withLock {
            guard lifecycleState == .failed else { return false }
            let hasGraph = ring != nil || tapID != nil || aggregateID != nil || ioProcID != nil || drainTask != nil || deliveryWorker != nil
            if hasGraph { lifecycleState = .stopping }
            return hasGraph
        }
        if needsFailedGraphTeardown {
            await teardownGraph(discardQueuedItems: true)
            stateLock.withLock {
                if lifecycleState == .stopping { lifecycleState = .idle }
            }
            if let cleanupDiagnostic = stateLock.withLock({ self.cleanupDiagnostic }) {
                let message = SystemAudioCaptureFailure.cleanupFailure(cleanupDiagnostic).description
                publish(.failed(message), generation: currentGeneration)
                throw SystemAudioCaptureFailure.cleanupFailure(cleanupDiagnostic)
            }
        }
        let completion = CompletionLatch()
        let startOperationAndGeneration: (UInt64, UInt64)? = stateLock.withLock {
            guard lifecycleState == .idle || lifecycleState == .failed else { return nil }
            lifecycleState = .starting
            currentGeneration = configuration.identity.captureGeneration
            lifecycleEpoch &+= 1
            startupAbort = nil
            sleepingGeneration = nil
            watchdogExpiredDuringStart = nil
            pendingSleepWakeEvents.removeAll(keepingCapacity: true)
            sleepWakeEventTaskActive = false
            sleepWakeEventTaskToken &+= 1
            startCompletion = completion
            failureTask = nil
            return (lifecycleEpoch, currentGeneration)
        }
        guard let (operation, generation) = startOperationAndGeneration else { return }
        defer {
            completion.finish()
            stateLock.withLock {
                if startCompletion === completion { startCompletion = nil }
            }
        }

        do {
            try await acquireGraph(generation: generation, isRebuild: false, operation: operation)
            let stillOwned = stateLock.withLock {
                lifecycleState == .running && lifecycleEpoch == operation && currentGeneration == generation
            }
            if !stillOwned {
                await teardownGraph(discardQueuedItems: true)
            }
        } catch {
            let cancellation: (userStop: Bool, startupReason: String?) = stateLock.withLock {
                if let startupAbort,
                   startupAbort.operation == operation,
                   startupAbort.generation == generation {
                    self.startupAbort = nil
                    return (false, startupAbort.reason)
                }
                return (
                    lifecycleEpoch != operation || lifecycleState == .stopping || lifecycleState == .idle,
                    nil
                )
            }
            await teardownGraph(discardQueuedItems: true)
            if let startupReason = cancellation.startupReason {
                let message = startupReason == Self.permissionDeniedStartupMarker
                    ? SystemAudioCaptureMonitor.permissionDeniedMessage
                    : "O evento HAL \(startupReason) interrompeu a inicialização da captura de áudio do sistema; a captura não foi iniciada."
                stateLock.withLock { lifecycleState = .failed }
                publish(.failed(message), generation: generation)
                if startupReason == Self.permissionDeniedStartupMarker {
                    throw SystemAudioCaptureFailure.denied
                }
                throw CompanionError.invalid(message)
            }
            if cancellation.userStop {
                return
            }
            let message = Self.errorMessage(error)
            stateLock.withLock { lifecycleState = .failed }
            publish(.failed(message), generation: generation)
            throw error
        }
    }

    public func stop() async {
        let stopPlan: (operation: UInt64, owner: Task<Void, Never>, hasCleanupFailure: Bool)? = stateLock.withLock {
            let hasResources = hasOwnedGraphLocked()
            guard lifecycleState != .idle || hasResources || cleanupDiagnostic != nil || failureTask != nil else {
                currentStatus = .stopped(SourceHealth(permission: .unknown, route: .unknown, deviceIdentity: configuration.deviceIdentity))
                return nil
            }

            if let owner = stopOwnerTask, let operation = stopOwnerOperation {
                // The lifecycle remains .stopping until this exact owner has
                // completed all dependency joins and graph cleanup.  A
                // concurrent caller must join it rather than incrementing the
                // epoch and creating a stale waiter that can return early.
                return (
                    operation: operation,
                    owner: owner,
                    hasCleanupFailure: stopOwnerHadCleanupFailure
                )
            }

            lifecycleState = .stopping
            lifecycleEpoch &+= 1
            let operation = lifecycleEpoch
            let rebuild = rebuildTask
            let start = startCompletion
            let failure = failureTask
            let terminal = terminalFailureCompletion
            let hasCleanupFailure = cleanupDiagnostic != nil
            let owner = Task { [weak self, rebuild, start, failure, terminal] in
                guard let self else { return }
                // This hook is a test-only barrier.  It is intentionally
                // before teardownGraph so both stop callers can claim the
                // shared owner while no lower-level teardown task exists.
                let pauseHook = self.stateLock.withLock { self.stopOwnerPauseHookForTesting }
                if let pauseHook { await pauseHook() }
                await rebuild?.value
                await start?.wait()
                await terminal?.wait()
                await failure?.value
                await self.teardownGraph(discardQueuedItems: false)
            }
            stopOwnerTask = owner
            stopOwnerOperation = operation
            stopOwnerHadCleanupFailure = hasCleanupFailure
            return (operation: operation, owner: owner, hasCleanupFailure: hasCleanupFailure)
        }
        guard let stopPlan else { return }
        let stopClaimHook = stateLock.withLock { stopClaimHookForTesting }
        stopClaimHook?()
        await stopPlan.owner.value
        let ownsStopCompletion = stateLock.withLock { () -> Bool in
            // The operation token is the post-teardown ownership proof.  A
            // joined waiter may resume after a replacement start has already
            // acquired a new epoch; it must not publish stopped or put that
            // replacement back into idle.
            guard lifecycleEpoch == stopPlan.operation,
                  stopOwnerOperation == stopPlan.operation,
                  lifecycleState == .stopping else { return false }
            if stopPlan.hasCleanupFailure && stopFailureOutstanding && !hasRetainedCleanupEdgesLocked() {
                // A second explicit stop is the retry boundary for a stop
                // failure whose stronger destruction edges already proved
                // callback quiescence.  The first stop remains fenced and
                // blocks restart until this boundary is crossed.
                stopFailureOutstanding = false
                cleanupDiagnostic = nil
            }
            lifecycleState = .idle
            failureTask = nil
            stopOwnerTask = nil
            stopOwnerOperation = nil
            stopOwnerHadCleanupFailure = false
            return true
        }
        if ownsStopCompletion {
            publish(.stopped(SourceHealth(permission: .unknown, route: .unknown, deviceIdentity: configuration.deviceIdentity)), generation: currentGeneration)
        }
    }

    /// Test and fixture boundary.  It is equivalent to one production IOProc
    /// input after the C callback has copied the bytes into its ring.  The
    /// caller owns `buffer` and it is copied synchronously.
    @discardableResult
    public func submitForTesting(_ buffer: ProcessTapPCMBuffer) -> TarsRealtimeDescriptorClass {
        submitForTesting(buffer, observeMonitor: true)
    }

    @discardableResult
    func submitForTestingWithoutMonitorObservation(_ buffer: ProcessTapPCMBuffer) -> TarsRealtimeDescriptorClass {
        submitForTesting(buffer, observeMonitor: false)
    }

    @discardableResult
    private func submitForTesting(_ buffer: ProcessTapPCMBuffer, observeMonitor: Bool) -> TarsRealtimeDescriptorClass {
        var result: TarsRealtimeDescriptorClass = TARS_REALTIME_DESCRIPTOR_MALFORMED
        let counters: TarsRealtimeCounters? = ringUseLock.withLock {
            guard stateLock.withLock({ lifecycleState == .running || lifecycleState == .starting }),
                  let ring = self.ring else { return nil }
                var inputs = buffer.buffers.map { data in
                    TarsRealtimeInputBuffer(
                        data: nil,
                        byteSize: UInt32(data.count),
                        channels: buffer.format.isInterleaved ? UInt32(buffer.format.channelCount) : 1
                    )
                }
                var descriptor = TarsRealtimeInputDescriptor(
                    bufferCount: UInt32(buffer.buffers.count),
                    buffers: nil,
                    asbd: Self.snapshot(buffer.format),
                    sampleTime: buffer.sampleTime ?? 0,
                    hostTime: buffer.hostTime ?? 0,
                    timestampFlags: buffer.timestampFlags |
                        (buffer.sampleTime == nil ? 0 : AudioTimeStampFlags.sampleTimeValid.rawValue) |
                        (buffer.hostTime == nil ? 0 : AudioTimeStampFlags.hostTimeValid.rawValue),
                    generation: buffer.generation
                )
                inputs.withUnsafeMutableBufferPointer { inputPointer in
                    descriptor.buffers = inputPointer.baseAddress.map { UnsafePointer($0) }
                    withUnsafeBytesForInputs(buffer.buffers) { rawPointers in
                        for index in rawPointers.indices { inputPointer[index].data = rawPointers[index] }
                        result = TarsRealtimeAudioRingPush(ring, &descriptor)
                    }
                }
                return TarsRealtimeAudioRingSnapshot(ring)
        }
        if observeMonitor, let counters {
            let operation = stateLock.withLock { () -> UInt64? in
                guard (lifecycleState == .running || lifecycleState == .starting),
                      currentGeneration == buffer.generation else { return nil }
                return lifecycleEpoch
            }
            applyMonitorAction(
                monitor.observeCounters(counters),
                generation: buffer.generation,
                operation: operation
            )
        }
        return result
    }

    @discardableResult
    public func pushForTesting(_ buffer: ProcessTapPCMBuffer) -> TarsRealtimeDescriptorClass {
        submitForTesting(buffer)
    }

    /// Test-only production-callback path.  It deliberately bypasses the
    /// Swift lifecycle admission check used by `submitForTesting`: a real
    /// Core Audio IOProc can still be admitted after terminal ownership has
    /// been claimed but before the non-realtime ring retirement edge closes
    /// C publication.  The caller uses this only while the source's
    /// retirement fixture hook owns the ring lifetime.
    @discardableResult
    func invokeRealtimeIOProcForTesting(_ buffer: ProcessTapPCMBuffer) -> OSStatus {
        guard buffer.buffers.count == 1,
              let ring = stateLock.withLock({ self.ring }) else { return noErr }
        return buffer.buffers[0].withUnsafeBytes { raw in
            var input = AudioBufferList()
            input.mNumberBuffers = 1
            input.mBuffers.mNumberChannels = UInt32(buffer.format.channelCount)
            input.mBuffers.mDataByteSize = UInt32(raw.count)
            input.mBuffers.mData = UnsafeMutableRawPointer(mutating: raw.baseAddress)
            var timestamp = AudioTimeStamp()
            timestamp.mSampleTime = buffer.sampleTime ?? 0
            timestamp.mHostTime = buffer.hostTime ?? 0
            timestamp.mFlags = AudioTimeStampFlags(rawValue: buffer.timestampFlags |
                (buffer.sampleTime == nil ? 0 : AudioTimeStampFlags.sampleTimeValid.rawValue) |
                (buffer.hostTime == nil ? 0 : AudioTimeStampFlags.hostTimeValid.rawValue))
            return TarsRealtimeAudioIOProc(
                0,
                nil,
                &input,
                &timestamp,
                nil,
                nil,
                UnsafeMutableRawPointer(bitPattern: UInt(bitPattern: ring))
            )
        }
    }

    public func drainForTesting() async {
        while drainOne() { }
        await Task.yield()
        let failure = stateLock.withLock { failureTask }
        await failure?.value
    }

    @discardableResult
    func drainOneForTesting() -> Bool {
        // The fixture can pause the background consumer to create a causal
        // ring sequence, while this explicit single-step boundary remains
        // available to the test itself.
        drainOne(ignoringTestPause: true)
    }

    public func triggerWatchdogForTesting(nowNanoseconds: UInt64? = nil) async {
        let now = nowNanoseconds ?? monotonicClock()
        let generation = stateLock.withLock { currentGeneration }
        await handleWatchdog(generation: generation, nowNanoseconds: now)
    }

    public func triggerEventForTesting(_ event: ProcessTapHALEvent) async {
        let generationAndOperation: (generation: UInt64, operation: UInt64?) = stateLock.withLock {
            // A synthetic event launched while a replacement graph is in its
            // starting phase represents the old graph's listener.  Real HAL
            // closures carry that captured generation; preserve the same
            // fencing semantics here instead of aborting the new graph as if
            // its own listener had already been installed.
            if lifecycleState == .starting, rebuildTask != nil, currentGeneration > 0 {
                return (currentGeneration - 1, nil)
            }
            return (
                currentGeneration,
                lifecycleState == .running ? lifecycleEpoch : nil
            )
        }
        switch event {
        case .sleep, .wake:
            let completion = enqueueSleepWakeEvent(
                event: event,
                generation: generationAndOperation.generation,
                operation: generationAndOperation.operation
            )
            await completion?.wait()
        default:
            await handle(
                event: event,
                generation: generationAndOperation.generation,
                operation: generationAndOperation.operation
            )
        }
    }

    public var resolvedHealth: SourceHealth? {
        if case .ready(let health) = status { return health }
        if case .running(let health) = status { return health }
        if case .stopped(let health) = status { return health }
        return nil
    }

    public var activeLifecycleGeneration: UInt64 {
        stateLock.withLock { currentGeneration }
    }

    public var cleanupFailureDiagnostic: String? {
        stateLock.withLock { cleanupDiagnostic }
    }

    var terminalEvidenceGapsForTesting: [CoverageGap] {
        stateLock.withLock { terminalEvidenceGaps }
    }

    // Internal observability for the deterministic cleanup fixture.  A
    // retained value here means the realtime callback's ownership proof was
    // intentionally not established, so restart must remain fenced.
    var hasRetainedRealtimeResourceForTesting: Bool {
        ringUseLock.withLock { stateLock.withLock { ring != nil } }
    }

    var teardownOwnerForTesting: UInt64? {
        stateLock.withLock { teardownTaskOwner }
    }

    var isIdleForTesting: Bool {
        stateLock.withLock { lifecycleState == .idle }
    }

    var teardownCompletionHook: (@Sendable (UInt64) -> Void)? {
        get { stateLock.withLock { teardownCompletionHookForTesting } }
        set { stateLock.withLock { teardownCompletionHookForTesting = newValue } }
    }

    var stopClaimHook: (@Sendable () -> Void)? {
        get { stateLock.withLock { stopClaimHookForTesting } }
        set { stateLock.withLock { stopClaimHookForTesting = newValue } }
    }

    /// Pauses the shared stop owner before it creates the lower-level
    /// teardown task.  Production never installs this hook; it lets the
    /// duplicate-stop fixture deterministically prove that both callers have
    /// joined the same owner before any cleanup edge begins.
    var stopOwnerPauseHook: (@Sendable () async -> Void)? {
        get { stateLock.withLock { stopOwnerPauseHookForTesting } }
        set { stateLock.withLock { stopOwnerPauseHookForTesting = newValue } }
    }

    /// Test-only observation of a generic ring fence.  Terminal failure has
    /// already transferred this ownership to RetireForTerminalFailure and
    /// therefore must not invoke this hook for a second SetGeneration(0).
    var ringFenceHook: (@Sendable () -> Void)? {
        get { stateLock.withLock { ringFenceHookForTesting } }
        set { stateLock.withLock { ringFenceHookForTesting = newValue } }
    }

    // Async-only fixture boundaries.  They let concurrency tests suspend an
    // acquisition or shared teardown owner without parking a cooperative
    // executor thread in a semaphore wait; production leaves both hooks nil.
    var acquisitionPauseHook: (@Sendable () async -> Void)? {
        get { stateLock.withLock { acquisitionPauseHookForTesting } }
        set { stateLock.withLock { acquisitionPauseHookForTesting = newValue } }
    }

    var teardownPauseHook: (@Sendable () async -> Void)? {
        get { stateLock.withLock { teardownPauseHookForTesting } }
        set { stateLock.withLock { teardownPauseHookForTesting = newValue } }
    }

    var terminalFailureClaimHook: (@Sendable () -> Void)? {
        get { stateLock.withLock { terminalFailureClaimHookForTesting } }
        set { stateLock.withLock { terminalFailureClaimHookForTesting = newValue } }
    }

    var terminalRetirementHook: (@Sendable (OpaquePointer) -> Void)? {
        get { stateLock.withLock { terminalRetirementHookForTesting } }
        set { stateLock.withLock { terminalRetirementHookForTesting = newValue } }
    }

    var watchdogSnapshotHook: (@Sendable () -> Void)? {
        get { stateLock.withLock { watchdogSnapshotHookForTesting } }
        set { stateLock.withLock { watchdogSnapshotHookForTesting = newValue } }
    }

    var deliveryFenceHook: (@Sendable () -> Void)? {
        get { stateLock.withLock { deliveryFenceHookForTesting } }
        set { stateLock.withLock { deliveryFenceHookForTesting = newValue } }
    }

    var deliveryWorkerStartHook: (@Sendable (ProcessTapDeliveryWorker) -> Void)? {
        get { stateLock.withLock { deliveryWorkerStartHookForTesting } }
        set { stateLock.withLock { deliveryWorkerStartHookForTesting = newValue } }
    }

    var deliveryWorkerActivationHook: (@Sendable (ProcessTapDeliveryWorker) -> Void)? {
        get { stateLock.withLock { deliveryWorkerActivationHookForTesting } }
        set { stateLock.withLock { deliveryWorkerActivationHookForTesting = newValue } }
    }

    var deliveryAdmissionHook: (@Sendable () -> Void)? {
        get { stateLock.withLock { deliveryAdmissionHookForTesting } }
        set {
            let worker = stateLock.withLock {
                deliveryAdmissionHookForTesting = newValue
                return deliveryWorker
            }
            worker?.beforeDeliveryAdmissionHook = newValue
        }
    }

    var conversionHook: (@Sendable () -> Void)? {
        get { stateLock.withLock { conversionHookForTesting } }
        set { stateLock.withLock { conversionHookForTesting = newValue } }
    }

    var sleepWakeDispatchHook: (@Sendable (ProcessTapHALEvent) -> Void)? {
        get { stateLock.withLock { sleepWakeDispatchHookForTesting } }
        set { stateLock.withLock { sleepWakeDispatchHookForTesting = newValue } }
    }

    var deliveryQueueCountForTesting: Int {
        stateLock.withLock { deliveryWorker?.queuedItemCount ?? 0 }
    }

    var quarantineEmptyHook: (@Sendable () -> Void)? {
        get { quarantine.emptyHook }
        set { quarantine.emptyHook = newValue }
    }

    var drainPausedForTesting: Bool {
        get { stateLock.withLock { pauseDrainForTesting } }
        set { stateLock.withLock { pauseDrainForTesting = newValue } }
    }

    private func acquireGraph(generation: UInt64, isRebuild: Bool, operation: UInt64) async throws {
        try ensureStartStillOwned(generation: generation, operation: operation)
        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        guard configuration.identity.source == .systemAudio else {
            throw CompanionError.invalid("system-audio identity is invalid")
        }
        let processID = hal.currentProcessID
        let processObject = try hal.translatePIDToProcessObject(pid: processID)
        guard processObject != UInt32(kAudioObjectUnknown) else { throw ProcessTapHALError.unknownProcessObject }
        stateLock.withLock { processObjectID = processObject }

        // Test-only asynchronous pause after the first synchronous HAL edge.
        // This models an acquisition owner that is still in flight without
        // blocking the cooperative executor, allowing stop callers to prove
        // their shared ownership path under strict scheduling.
        let acquisitionPauseHook = stateLock.withLock { acquisitionPauseHookForTesting }
        if let acquisitionPauseHook { await acquisitionPauseHook() }

        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        let scope = try ProcessTapCaptureScope.globalExclusive(excluding: processObject)
        let tap = try hal.createProcessTap(description: scope.description)
        stateLock.withLock { tapID = tap }
        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        let tapUID = try hal.tapUID(tapID: tap)
        let descriptor = try hal.readTapFormat(tapID: tap)
        let format = descriptor.converterFormat
        try format.validate()

        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        var asbd = Self.snapshot(format)
        guard let newRing = TarsRealtimeAudioRingCreate(
            ringSlotCount,
            ringSlotCapacity,
            &asbd,
            UInt32(descriptor.channels),
            descriptor.isInterleaved,
            generation
        ) else {
            throw CompanionError.invalid("não foi possível alocar o ring realtime do Process Tap")
        }
        stateLock.withLock {
            ring = newRing
            pendingOverflowBoundaries.removeAll(keepingCapacity: true)
            sleepingGeneration = nil
        }
        // This is deliberately outside the realtime callback.  The drain
        // loop only borrows this storage after start has completed.
        let scratch = drainScratchFactory(Int(ringSlotCapacity))
        stateLock.withLock { drainScratch = scratch }

        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        let uid = try freshAggregateUID(generation: generation)
        let aggregate = try hal.createAggregate(
            tapID: tap,
            tapUID: tapUID,
            uid: uid,
            name: "TarsCompanion Process Tap \(uid.prefix(8))"
        )
        stateLock.withLock {
            aggregateID = aggregate
            pendingIOProcAggregateID = nil
            aggregateDestroySucceeded = false
            tapAttached = true
        }

        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        let rawRing = UInt64(UInt(bitPattern: newRing))
        let ioProc = try hal.createIOProc(aggregateID: aggregate, ringAddress: rawRing)
        stateLock.withLock { ioProcID = ioProc }

        let identity = try SourceIdentity(
            sessionID: configuration.identity.sessionID,
            streamID: configuration.identity.streamID,
            captureGeneration: generation,
            source: .systemAudio,
            sampleRate: CanonicalSystemAudioConverter.outputSampleRate,
            channelCount: CanonicalSystemAudioConverter.outputChannelCount
        )
        let anchor = ProcessTapTimeAnchor(
            monotonicNanoseconds: monotonicClock(),
            wallClockMilliseconds: wallClock(),
            hostTime: nil
        )
        let newConverter = try CanonicalSystemAudioConverter(
            inputFormat: format,
            identity: identity,
            anchor: anchor,
            deviceID: configuration.deviceIdentity ?? "ProcessTap.SystemAudio"
        )
        stateLock.withLock {
            inputFormat = format
            converter = newConverter
        }

        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        let worker = ProcessTapDeliveryWorker(sink: sink, registry: quarantine, queueCapacity: deliveryQueueCapacity)
        let (queuedGaps, admissionHook) = stateLock.withLock { () -> ([CoverageGap], (@Sendable () -> Void)?) in
            deliveryWorker = worker
            let gaps = pendingGaps
            pendingGaps.removeAll()
            return (gaps, deliveryAdmissionHookForTesting)
        }
        worker.beforeDeliveryAdmissionHook = admissionHook
        // The worker and drain task are intentionally not started until the
        // HAL start call has returned.  A startup callback may report a
        // malformed/capacity input while AudioDeviceStart is in flight; no
        // callback-driven teardown may race that call.
        for gap in queuedGaps { worker.enqueue(.gap(gap)) }

        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        try installListeners(aggregateID: aggregate, tapID: tap, generation: generation, operation: operation)
        let counters = TarsRealtimeAudioRingSnapshot(newRing)
        let deadline = isRebuild
            ? monitor.beginRebuildGeneration(generation, counters: Self.counterSnapshot(counters), nowNanoseconds: monotonicClock())
            : monitor.resetForNewUserStart(generation: generation, counters: Self.counterSnapshot(counters), nowNanoseconds: monotonicClock())
        stateLock.withLock {
            // The watchdog task itself is safe during startup: an expiry only
            // records a pending deadline.  It cannot teardown the graph until
            // the acquisition owner publishes `.running` below.
            watchdogExpiredDuringStart = nil
        }
        armWatchdog(generation: generation, deadlineNanoseconds: deadline)

        await Task.yield()
        try ensureStartStillOwned(generation: generation, operation: operation)
        do {
            try hal.start(aggregateID: aggregate, ioProc: ioProc)
            stateLock.withLock { aggregateStarted = true }
        } catch {
            if case .denied = error as? SystemAudioCaptureFailure {
                _ = monitor.observePermissionError()
            }
            throw error
        }

        // Drain actions are deferred while `.starting`, but a callback can
        // still have incremented a malformed/capacity counter during the HAL
        // start call.  Consume that result while the acquisition owner still
        // owns startup; `applyMonitorAction` converts it to an abort marker
        // rather than launching teardown concurrently with AudioDeviceStart.
        let startupCounterAction: SystemAudioCaptureMonitorAction = ringUseLock.withLock {
            guard let ring = stateLock.withLock({ self.ring }) else { return .none }
            return monitor.observeCounters(TarsRealtimeAudioRingSnapshot(ring))
        }
        if startupCounterAction != .none {
            applyMonitorAction(startupCounterAction, generation: generation, operation: operation)
        }
        try ensureStartStillOwned(generation: generation, operation: operation)
        let activation: (worker: ProcessTapDeliveryWorker, watchdogPending: UInt64?)? = stateLock.withLock {
            guard lifecycleState == .starting,
                  lifecycleEpoch == operation,
                  currentGeneration == generation,
                  let worker = deliveryWorker else { return nil }
            let watchdogPending = watchdogExpiredDuringStart?.generation == generation
                ? watchdogExpiredDuringStart?.nowNanoseconds
                : nil
            return (worker, watchdogPending)
        }
        guard let (activeWorker, watchdogPending) = activation else {
            throw CompanionError.callbackFenced
        }
        let workerStartHook = stateLock.withLock { deliveryWorkerStartHookForTesting }
        workerStartHook?(activeWorker)
        guard activeWorker.start() else {
            stateLock.withLock {
                guard lifecycleState == .starting,
                      lifecycleEpoch == operation,
                      currentGeneration == generation else { return }
                startupAbort = (
                    generation: generation,
                    operation: operation,
                    reason: "delivery-worker-start"
                )
                lifecycleState = .stopping
                lifecycleEpoch &+= 1
            }
            throw CompanionError.callbackFenced
        }
        // The worker is installed but still suspended behind its activation
        // gate.  Give the deterministic race hook the same pre-activation
        // window a concurrent stop/recovery owns in production; the source
        // then commits lifecycle + worker activation as one state-lock edge.
        let workerActivationHook = stateLock.withLock { deliveryWorkerActivationHookForTesting }
        workerActivationHook?(activeWorker)
        let activated = stateLock.withLock {
            guard lifecycleState == .starting,
                  lifecycleEpoch == operation,
                  currentGeneration == generation,
                  deliveryWorker === activeWorker else { return false }
            // Commit the lifecycle while the worker remains suspended.  The
            // activation lease is released only after this state-lock edge;
            // a concurrent stop can therefore either fence the still-suspended
            // worker or observe a fully committed running owner.
            guard activeWorker.prepareActivation() else { return false }
            lifecycleState = .running
            return true
        }
        guard activated else {
            _ = activeWorker.fenceAndDiscard()
            throw CompanionError.callbackFenced
        }
        activeWorker.releaseActivation()
        // A watchdog task can observe `.starting` after the activation tuple
        // was read but before the worker/lifecycle transition below became
        // visible.  Consume that late startup expiry after `.running` is
        // published as well; otherwise the one-shot watchdog could disappear
        // in this narrow hand-off window.
        let watchdogPendingAfterActivation = stateLock.withLock { () -> UInt64? in
            guard currentGeneration == generation else { return nil }
            let pending = watchdogExpiredDuringStart?.generation == generation
                ? watchdogExpiredDuringStart?.nowNanoseconds
                : nil
            watchdogExpiredDuringStart = nil
            return pending
        }
        let drain = Task { [weak self] () -> Void in
            guard let self else { return }
            await self.drainLoop()
        }
        let installDrain = stateLock.withLock {
            guard lifecycleState == .running,
                  lifecycleEpoch == operation,
                  currentGeneration == generation,
                  deliveryWorker === activeWorker else { return false }
            drainTask = drain
            return true
        }
        if !installDrain { drain.cancel() }
        // Process Tap start does not prove permission or signal.  Initial
        // health is deliberately unknown and remains so for silent PCM.
        publishRunningIfOwned(SourceHealth(
            permission: .unknown,
            route: .unknown,
            interruption: .clear,
            sleep: .awake,
            overflowed: false,
            deviceIdentity: configuration.deviceIdentity ?? "ProcessTap.SystemAudio"
        ), generation: generation, operation: operation)
        if let watchdogAt = watchdogPendingAfterActivation ?? watchdogPending {
            await handleWatchdog(generation: generation, nowNanoseconds: watchdogAt)
        }
    }

    private func ensureStartStillOwned(generation: UInt64, operation: UInt64) throws {
        guard stateLock.withLock({
            lifecycleState == .starting && lifecycleEpoch == operation && currentGeneration == generation
        }) else {
            throw CompanionError.callbackFenced
        }
    }

    private func hasOwnedGraphLocked() -> Bool {
        hasRetainedCleanupEdgesLocked() || stopFailureOutstanding
    }

    private func hasRetainedCleanupEdgesLocked() -> Bool {
        ring != nil || tapID != nil || aggregateID != nil || ioProcID != nil ||
            !listenerTokens.isEmpty || tapAttached || aggregateStarted ||
            pendingIOProcAggregateID != nil ||
            drainTask != nil || deliveryWorker != nil
    }

    private func freshAggregateUID(generation: UInt64) throws -> String {
        let base = uuidFactory()
        guard !base.isEmpty else { throw CompanionError.invalid("UID do aggregate é vazio") }
        return stateLock.withLock {
            var candidate = base
            var suffix: UInt64 = 0
            while usedAggregateUIDs.contains(candidate) {
                suffix &+= 1
                candidate = "\(base)-\(generation)-\(suffix)"
            }
            usedAggregateUIDs.insert(candidate)
            return candidate
        }
    }

    private func installListeners(aggregateID: UInt32, tapID: UInt32, generation: UInt64, operation: UInt64) throws {
        let kinds: [ProcessTapHALListenerKind] = [.sleepWake, .serviceReset, .tapList, .deviceAlive, .tapFormat]
        for kind in kinds {
            let token = try hal.installListener(kind: kind, aggregateID: aggregateID, tapID: tapID) { [weak self] event in
                guard let self else { return }
                // A listener can arrive between the final registration and
                // HAL start.  Claiming the lifecycle transition synchronously
                // makes the later start ownership check fail, so startup can
                // never publish a running graph whose ring was fenced here.
                let abortedStart = self.abortStartingIfOwned(
                    generation: generation,
                    operation: operation,
                    reason: Self.startupAbortReason(for: event)
                )
                if !abortedStart,
                   case .deviceAliveReadFailed(let status) = event,
                   status == kAudioDevicePermissionsError,
                   let permissionAction = self.observePermissionErrorIfOwned(
                       status: status,
                       generation: generation,
                       operation: operation
                   ) {
                    // Permission denial claims terminal ownership before any
                    // synchronous fence. The terminal owner then takes
                    // ringUseLock and snapshots the converter, so an
                    // in-flight drain cannot race a mutable cursor read.
                    self.applyMonitorAction(
                        permissionAction.action,
                        generation: generation,
                        operation: permissionAction.operation
                    )
                    return
                }
                // Fence synchronously on the listener callback's thread.  A
                // queued Swift rebuild must never race a drain that is about
                // to decode bytes under the old tap format/generation.
                if abortedStart || self.shouldFenceEvent(
                    generation: generation,
                    operation: operation,
                    event: event
                ) {
                    self.fenceCurrentGeneration(generation)
                }
                switch event {
                case .sleep, .wake:
                    // Claim the event order synchronously.  The queue owner
                    // starts at most one task for this listener generation;
                    // later wake delivery therefore cannot overtake a sleep
                    // whose task was merely delayed by the executor.
                    _ = self.enqueueSleepWakeEvent(
                        event: event,
                        generation: generation,
                        operation: operation
                    )
                default:
                    Task { await self.handle(event: event, generation: generation, operation: operation) }
                }
            }
            stateLock.withLock { listenerTokens.append(token) }
        }
    }

    private func armWatchdog(generation: UInt64, deadlineNanoseconds: UInt64) {
        let task = Task { [weak self] in
            guard let self else { return }
            let now = self.monotonicClock()
            if deadlineNanoseconds > now {
                try? await Task.sleep(nanoseconds: deadlineNanoseconds - now)
            }
            guard !Task.isCancelled else { return }
            await self.handleWatchdog(generation: generation, nowNanoseconds: self.monotonicClock())
        }
        let previous = stateLock.withLock { () -> Task<Void, Never>? in
            guard currentGeneration == generation,
                  lifecycleState == .starting || lifecycleState == .running else {
                return nil
            }
            let previous = watchdogTask
            watchdogTask = task
            return previous
        }
        previous?.cancel()
        if previous == nil,
           !stateLock.withLock({ currentGeneration == generation && (lifecycleState == .starting || lifecycleState == .running) }) {
            task.cancel()
        }
    }

    private func handleWatchdog(generation: UInt64, nowNanoseconds: UInt64) async {
        let startupState = stateLock.withLock {
            lifecycleState == .starting && currentGeneration == generation
        }
        if startupState {
            // Do not perform recovery or teardown while the acquisition owner
            // is inside AudioDeviceStart.  Preserve the expiry so activation
            // can re-evaluate the deadline immediately after start returns.
            stateLock.withLock {
                guard lifecycleState == .starting, currentGeneration == generation else { return }
                if watchdogExpiredDuringStart == nil {
                    watchdogExpiredDuringStart = (generation: generation, nowNanoseconds: nowNanoseconds)
                }
            }
            return
        }
        let counters: TarsRealtimeCounters? = ringUseLock.withLock {
            guard let ring = stateLock.withLock({ self.ring }) else { return nil }
            return TarsRealtimeAudioRingSnapshot(ring)
        }
        guard let counters else { return }
        let snapshotHook = stateLock.withLock { watchdogSnapshotHookForTesting }
        snapshotHook?()
        // Serialize the monitor decision with recovery-task creation.  A
        // watchdog and a HAL callback can arrive together; once one task is
        // installed, every other cause joins it instead of consuming the
        // one-rebuild budget or turning a valid recovery into a terminal
        // failure.
        let plan: RecoveryPlan = stateLock.withLock {
            guard lifecycleState == .running,
                  currentGeneration == generation,
                  sleepingGeneration != generation else { return .ignore }
            if let rebuildTask { return .join(rebuildTask) }

            let action = monitor.observeCounters(counters)
            let resolved = action == .none
                ? monitor.deadlineFired(generation: generation, nowNanoseconds: nowNanoseconds)
                : action
            switch resolved {
            case .rebuild(let reason):
                return makeRecoveryTaskLocked(reason: reason, budgetAlreadyClaimed: true)
            case .ambiguous(let message), .denied(let message), .failed(let message):
                return .fail(message: message, generation: generation, operation: lifecycleEpoch)
            default:
                return .ignore
            }
        }
        switch plan {
        case .join(let task):
            await task.value
        case .fail(let message, let generation, let operation):
            await failTerminal(message: message, generation: generation, operation: operation)
        case .ignore:
            break
        }
    }

    private func handle(event: ProcessTapHALEvent, generation: UInt64) async {
        await handle(event: event, generation: generation, operation: nil)
    }

    @discardableResult
    private func enqueueSleepWakeEvent(
        event: ProcessTapHALEvent,
        generation: UInt64,
        operation: UInt64?
    ) -> CompletionLatch? {
        let completion = CompletionLatch()
        let result: (accepted: Bool, taskToken: UInt64?) = stateLock.withLock {
            guard (lifecycleState == .running || lifecycleState == .starting),
                  currentGeneration == generation,
                  operation.map({ lifecycleEpoch == $0 }) ?? true else {
                return (false, nil)
            }
            pendingSleepWakeEvents.append(PendingSleepWakeEvent(
                event: event,
                generation: generation,
                operation: operation,
                completion: completion
            ))
            guard !sleepWakeEventTaskActive else { return (true, nil) }
            sleepWakeEventTaskActive = true
            sleepWakeEventTaskToken &+= 1
            return (true, sleepWakeEventTaskToken)
        }
        guard result.accepted else { return nil }
        if let taskToken = result.taskToken {
            Task { [weak self] in
                await self?.drainSleepWakeEvents(taskToken: taskToken)
            }
        }
        return completion
    }

    private func drainSleepWakeEvents(taskToken: UInt64) async {
        while true {
            let next: PendingSleepWakeEvent? = stateLock.withLock {
                guard sleepWakeEventTaskActive,
                      sleepWakeEventTaskToken == taskToken else { return nil }
                guard !pendingSleepWakeEvents.isEmpty else {
                    sleepWakeEventTaskActive = false
                    return nil
                }
                return pendingSleepWakeEvents.removeFirst()
            }
            guard let next else { return }
            let dispatchHook = stateLock.withLock { sleepWakeDispatchHookForTesting }
            dispatchHook?(next.event)
            await handle(
                event: next.event,
                generation: next.generation,
                operation: next.operation
            )
            next.completion.finish()
        }
    }

    private func handle(event: ProcessTapHALEvent, generation: UInt64, operation: UInt64?) async {
        let isStarting = stateLock.withLock { lifecycleState == .starting && currentGeneration == generation }
        if isStarting {
            if let operation {
                abortStartingIfOwned(
                    generation: generation,
                    operation: operation,
                    reason: Self.startupAbortReason(for: event)
                )
            } else {
                stateLock.withLock {
                    if lifecycleState == .starting && currentGeneration == generation {
                        startupAbort = (
                            generation: generation,
                            operation: lifecycleEpoch,
                            reason: Self.startupAbortReason(for: event)
                        )
                        lifecycleEpoch &+= 1
                        lifecycleState = .stopping
                    }
                }
            }
        } else if case .wake = event {
            // A duplicate wake after the replacement graph is running is a
            // no-op.  It must not fence the active ring before discovering
            // that there is no sleeping episode to recover.
            guard stateLock.withLock({
                lifecycleState == .running &&
                currentGeneration == generation &&
                sleepingGeneration == generation
            }) else { return }
        }
        guard stateLock.withLock({
            lifecycleState == .running &&
                currentGeneration == generation &&
                (operation.map { lifecycleEpoch == $0 } ?? true)
        }) else { return }
        fenceCurrentGeneration(generation)
        switch event {
        case .sleep:
            let sleepingStatus: (status: CaptureSourceStatus, operation: UInt64)? = stateLock.withLock {
                guard lifecycleState == .running, currentGeneration == generation else { return nil }
                sleepingGeneration = generation
                guard case .running(var health) = currentStatus else { return nil }
                health.sleep = .sleeping
                let status = CaptureSourceStatus.running(health)
                currentStatus = status
                return (status, lifecycleEpoch)
            }
            monitor.cancelDeadline()
            if let sleepingStatus {
                publishOwned(sleepingStatus.status, generation: generation, operation: sleepingStatus.operation)
            }
        case .wake:
            let shouldRebuild = stateLock.withLock {
                guard lifecycleState == .running,
                      currentGeneration == generation,
                      sleepingGeneration == generation else { return false }
                sleepingGeneration = nil
                return true
            }
            if shouldRebuild {
                await rebuild(reason: "hal-wake")
            }
        case .deviceAlive(false):
            await rebuild(reason: "device-route-loss")
        case .deviceAliveReadFailed(let status):
            if status == kAudioDevicePermissionsError {
                // The HAL event intentionally preserves the raw OSStatus.
                // Normalize permission denial here through the monitor so the
                // exact user-facing copy and denied health are published
                // without consuming the automatic recovery budget.
                if let permissionAction = observePermissionErrorIfOwned(
                    status: status,
                    generation: generation,
                    operation: operation
                ) {
                    applyMonitorAction(
                        permissionAction.action,
                        generation: generation,
                        operation: permissionAction.operation
                    )
                }
            } else {
                await rebuild(reason: "hal-device-alive-read-\(status)")
            }
        case .serviceReset, .tapListChanged, .tapFormatChanged, .deviceAlive(true):
            await rebuild(reason: "hal-\(Self.eventName(event))")
        }
    }

    @discardableResult
    private func abortStartingIfOwned(generation: UInt64, operation: UInt64, reason: String) -> Bool {
        stateLock.withLock {
            guard lifecycleState == .starting,
                  lifecycleEpoch == operation,
                  currentGeneration == generation else { return false }
            startupAbort = (generation: generation, operation: operation, reason: reason)
            lifecycleEpoch &+= 1
            lifecycleState = .stopping
            return true
        }
    }

    private func shouldFenceEvent(
        generation: UInt64,
        operation: UInt64? = nil,
        event: ProcessTapHALEvent
    ) -> Bool {
        stateLock.withLock {
            guard lifecycleState == .running,
                  currentGeneration == generation,
                  operation.map({ lifecycleEpoch == $0 }) ?? true else { return false }
            if case .wake = event {
                return sleepingGeneration == generation
            }
            return true
        }
    }

    /// Permission reads are terminal observations, not merely queued route
    /// events.  Validate the listener's captured generation and operation at
    /// the same state-lock boundary that mutates the shared monitor.  A late
    /// retired listener therefore cannot poison the monitor for a replacement
    /// generation before its event reaches the async lifecycle handler.
    private func observePermissionErrorIfOwned(
        status: OSStatus,
        generation: UInt64,
        operation: UInt64?
    ) -> (action: SystemAudioCaptureMonitorAction, operation: UInt64)? {
        stateLock.withLock {
            guard lifecycleState == .running,
                  currentGeneration == generation,
                  operation.map({ lifecycleEpoch == $0 }) ?? true else { return nil }
            return (monitor.observePermissionError(status), lifecycleEpoch)
        }
    }

    private func rebuild(reason: String, budgetAlreadyClaimed: Bool = false) async {
        let plan: RecoveryPlan = stateLock.withLock {
            makeRecoveryTaskLocked(reason: reason, budgetAlreadyClaimed: budgetAlreadyClaimed)
        }
        switch plan {
        case .join(let task):
            await task.value
        case .fail(let message, let generation, let operation):
            await failTerminal(message: message, generation: generation, operation: operation)
        case .ignore:
            break
        }
    }

    /// Creates or joins the single in-flight recovery while `stateLock` is
    /// held.  Installing `rebuildTask` before the async graph work starts is
    /// the key causal edge: concurrent event/watchdog causes can only join
    /// this task and never claim a second rebuild budget.
    private func makeRecoveryTaskLocked(reason: String, budgetAlreadyClaimed: Bool) -> RecoveryPlan {
        guard lifecycleState == .running else { return .ignore }
        if let rebuildTask { return .join(rebuildTask) }
        if !budgetAlreadyClaimed && !monitor.claimAutomaticRebuild() {
            return .fail(
                message: "A recuperação automática da captura de áudio excedeu o limite desta sessão.",
                generation: currentGeneration,
                operation: lifecycleEpoch
            )
        }
        let task = Task { [weak self] in
            guard let self else { return }
            await self.rebuildGraph(reason: reason)
            self.stateLock.withLock {
                // No replacement task can be installed while this one is
                // retained, so this clear cannot erase a newer recovery.
                self.rebuildTask = nil
            }
        }
        rebuildTask = task
        return .join(task)
    }

    private func rebuildGraph(reason: String) async {
        guard stateLock.withLock({ lifecycleState == .running }) else { return }
        let (oldGeneration, operation) = stateLock.withLock { (currentGeneration, lifecycleEpoch) }
        stateLock.withLock {
            if currentGeneration == oldGeneration { sleepingGeneration = nil }
        }
        // Fence and snapshot the old converter cursor as one ownership edge.
        // The immutable values below must be the only cursor inputs used after
        // teardown clears the converter and ring.  Lock order is always
        // ringUseLock -> stateLock when both are needed.
        let oldCursor: (identity: SourceIdentity, firstSequence: UInt64, firstSample: UInt64) = ringUseLock.withLock {
            let oldRing = stateLock.withLock {
                currentGeneration == oldGeneration ? self.ring : nil
            }
            if let oldRing {
                TarsRealtimeAudioRingSetGeneration(oldRing, 0)
            }
            if let converter = stateLock.withLock({ self.converter }) {
                return (converter.identity, converter.nextSequenceForGap, converter.nextSampleForGap)
            }
            return (makeIdentity(generation: oldGeneration), 0, 0)
        }
        let oldWorker = stateLock.withLock { deliveryWorker }
        let discardedBoundaries = oldWorker?.fenceAndDiscard() ?? []
        let deliveryFenceHook = stateLock.withLock { deliveryFenceHookForTesting }
        deliveryFenceHook?()
        let gaps = makeDiscardedGaps(
            boundaries: discardedBoundaries,
            fallback: oldCursor,
            reason: reason == "no-buffer-watchdog" ? .unknownEnd : .routeLoss,
            includeFallback: true
        )
        await teardownGraph(discardQueuedItems: true)
        if !gaps.isEmpty { stateLock.withLock { pendingGaps.append(contentsOf: gaps) } }

        guard stateLock.withLock({ lifecycleEpoch == operation && lifecycleState == .stopping }) else { return }

        guard !quarantine.hasActive else {
            let message = "A captura não pode ser reconstruída enquanto uma chamada de sink permanece em andamento."
            stateLock.withLock { lifecycleState = .failed }
            publish(.failed(message), generation: oldGeneration)
            return
        }
        if let cleanupDiagnostic = stateLock.withLock({ self.cleanupDiagnostic }) {
            let message = SystemAudioCaptureFailure.cleanupFailure(cleanupDiagnostic).description
            stateLock.withLock { lifecycleState = .failed }
            publish(.failed(message), generation: oldGeneration)
            return
        }

        let nextResult = oldGeneration.addingReportingOverflow(1)
        guard !nextResult.overflow, nextResult.partialValue != 0 else {
            stateLock.withLock { lifecycleState = .failed }
            publish(.failed("A geração da captura do Process Tap excedeu o limite suportado."), generation: oldGeneration)
            return
        }
        let next = nextResult.partialValue
        let rebuildOperation: UInt64? = stateLock.withLock {
            guard lifecycleEpoch == operation, lifecycleState == .stopping else { return nil }
            currentGeneration = next
            lifecycleState = .starting
            lifecycleEpoch &+= 1
            return lifecycleEpoch
        }
        guard let rebuildOperation else { return }
        do {
            try await acquireGraph(generation: next, isRebuild: true, operation: rebuildOperation)
        } catch {
            let startupReason: String? = stateLock.withLock {
                guard let startupAbort,
                      startupAbort.operation == rebuildOperation,
                      startupAbort.generation == next else { return nil }
                self.startupAbort = nil
                return startupAbort.reason
            }
            if let startupReason {
                await teardownGraph(discardQueuedItems: true)
                let message = startupReason == Self.permissionDeniedStartupMarker
                    ? SystemAudioCaptureMonitor.permissionDeniedMessage
                    : "O evento HAL \(startupReason) interrompeu a reconstrução da captura de áudio do sistema; a captura foi encerrada."
                stateLock.withLock { lifecycleState = .failed }
                publish(.failed(message), generation: next)
                return
            }
            if stateLock.withLock({ lifecycleEpoch != rebuildOperation || lifecycleState == .stopping || lifecycleState == .idle }) {
                return
            }
            await teardownGraph(discardQueuedItems: true)
            stateLock.withLock { lifecycleState = .failed }
            publish(.failed(Self.errorMessage(error)), generation: next)
        }
    }

    private func failTerminal(message: String, generation: UInt64, operation: UInt64) async {
        let task = beginTerminalFailure(message: message, generation: generation, operation: operation)
        await task?.value
    }

    @discardableResult
    private func beginTerminalFailure(
        message: String,
        generation: UInt64,
        operation: UInt64? = nil
    ) -> Task<Void, Never>? {
        let isStarting = stateLock.withLock {
            lifecycleState == .starting &&
                currentGeneration == generation &&
                (operation.map { lifecycleEpoch == $0 } ?? true)
        }
        if isStarting {
            // Startup failures are reported as an ownership marker only.  The
            // acquisition owner must unwind the graph after its HAL call
            // returns; a monitor task is never allowed to teardown resources
            // concurrently with AudioDeviceStart.
            let claimed = abortStartingWithFailure(message: message, generation: generation, operation: operation)
            if claimed {
                publish(.failed(message), generation: generation)
            }
            return nil
        }
        // Claim terminal ownership before any destructive worker operation.
        // A losing concurrent cause is a no-op and a stale plan cannot fence a
        // replacement graph's worker.  The converter cursor is deliberately
        // not read here: drainOne mutates that cursor while holding
        // ringUseLock, so terminal evidence must snapshot it only after the
        // same lock has fenced the ring.
        let claim: (
            token: UInt64,
            worker: ProcessTapDeliveryWorker?,
            completion: CompletionLatch
        )? = stateLock.withLock {
            guard lifecycleState == .running,
                  currentGeneration == generation,
                  (operation.map { lifecycleEpoch == $0 } ?? true),
                  terminalFailureOwnerToken == nil,
                  failureTask == nil else { return nil }
            nextTerminalFailureOwnerToken &+= 1
            let token = nextTerminalFailureOwnerToken
            let completion = CompletionLatch()
            terminalFailureOwnerToken = token
            terminalFailureCompletion = completion
            let worker = deliveryWorker
            lifecycleState = .stopping
            lifecycleEpoch &+= 1
            return (token: token, worker: worker, completion: completion)
        }
        guard let claim else { return nil }
        let terminalClaimHook = stateLock.withLock { terminalFailureClaimHookForTesting }
        terminalClaimHook?()

        // Lock order is ringUseLock -> stateLock everywhere a ring and graph
        // state are needed.  The owner claim above is non-destructive; this
        // second edge fences the raw ring and snapshots the mutable converter
        // only after any in-flight drain conversion has completed.  Thus a
        // converted frame cannot be rejected after the worker fence without a
        // corresponding queued/discarded boundary.
        let terminalSnapshot: (
            fallback: (identity: SourceIdentity, firstSequence: UInt64, firstSample: UInt64)?,
            rawBoundary: ProcessTapDeliveryWorker.DiscardedDeliveryBoundary?
        ) = ringUseLock.withLock {
            let ownsTerminalEdge = stateLock.withLock {
                terminalFailureOwnerToken == claim.token &&
                    lifecycleState == .stopping &&
                    currentGeneration == generation
            }
            guard ownsTerminalEdge else { return (fallback: nil, rawBoundary: nil) }
            let currentRing = stateLock.withLock {
                currentGeneration == generation ? self.ring : nil
            }
            let terminalRetirementHook = stateLock.withLock { terminalRetirementHookForTesting }
            // The fixture hook is deliberately inside the Swift ring-use
            // ownership edge and immediately before C retirement.  A real
            // IOProc does not use this lock, so the test can admit one final
            // raw slot here while the terminal owner has already claimed the
            // failure but before C closes publication admission.
            if let currentRing {
                terminalRetirementHook?(currentRing)
            }
            let rawRetirement: TarsRealtimeRingRetirement? = currentRing.map {
                TarsRealtimeAudioRingRetireForTerminalFailure($0)
            }
            let rawBoundary: ProcessTapDeliveryWorker.DiscardedDeliveryBoundary? = {
                guard let rawRetirement,
                      (rawRetirement.hadRetainedSlots || rawRetirement.hadAdmittedLoss),
                      let converter = stateLock.withLock({ self.converter }) else { return nil }
                return ProcessTapDeliveryWorker.DiscardedDeliveryBoundary(
                    identity: converter.identity,
                    firstSequence: converter.nextSequenceForGap,
                    firstSample: converter.nextSampleForGap
                )
            }()
            if currentRing != nil {
                // RetireForTerminalFailure already closed admission, waited
                // for every admitted IOProc, captured raw loss, and purged
                // the ring.  Transfer that fenced ownership to teardown so a
                // generic cleanup pass cannot issue a second terminal fence.
                stateLock.withLock {
                    guard terminalFailureOwnerToken == claim.token,
                          currentGeneration == generation else { return }
                    terminalRetiredGeneration = generation
                }
            }
            let fallback: (identity: SourceIdentity, firstSequence: UInt64, firstSample: UInt64)? = stateLock.withLock {
                guard terminalFailureOwnerToken == claim.token,
                      currentGeneration == generation else { return nil }
                return converter.map {
                    (
                        identity: $0.identity,
                        firstSequence: $0.nextSequenceForGap,
                        firstSample: $0.nextSampleForGap
                    )
                }
            }
            return (fallback: fallback, rawBoundary: rawBoundary)
        }
        let worker = claim.worker
        let discardedBoundaries = worker?.fenceAndDiscard() ?? []
        var terminalBoundaries = discardedBoundaries
        if let rawBoundary = terminalSnapshot.rawBoundary {
            terminalBoundaries.append(rawBoundary)
        }
        let deliveryFenceHook = stateLock.withLock { deliveryFenceHookForTesting }
        deliveryFenceHook?()
        let terminalGaps: [CoverageGap]
        if let fallback = terminalSnapshot.fallback {
            terminalGaps = makeDiscardedGaps(
                boundaries: terminalBoundaries,
                fallback: fallback,
                reason: .unknownEnd,
                includeFallback: false
            )
        } else {
            terminalGaps = terminalBoundaries.compactMap { boundary in
                makeUnknownEndGap(
                    identity: boundary.identity,
                    firstSequence: boundary.firstSequence,
                    firstSample: boundary.firstSample,
                    reason: .unknownEnd
                )
            }
        }
        stateLock.withLock {
            if terminalFailureOwnerToken == claim.token {
                terminalEvidenceGaps.append(contentsOf: terminalGaps)
            }
        }
        let task = Task { [weak self, worker, terminalGaps, completion = claim.completion] in
            guard let self else {
                completion.finish()
                return
            }
            defer {
                completion.finish()
                self.stateLock.withLock {
                    guard self.terminalFailureOwnerToken == claim.token else { return }
                    self.terminalFailureOwnerToken = nil
                    self.terminalFailureCompletion = nil
                }
            }
            await worker?.deliverTerminalGaps(
                terminalGaps,
                deadlineNanoseconds: self.stopDeadlineNanoseconds
            )
            await self.teardownGraph(discardQueuedItems: true)
            let ownsFinalPublication = self.stateLock.withLock {
                guard self.terminalFailureOwnerToken == claim.token,
                      self.currentGeneration == generation else { return false }
                self.lifecycleState = .failed
                return true
            }
            if ownsFinalPublication {
                self.publish(.failed(message), generation: generation)
            }
        }
        stateLock.withLock {
            guard terminalFailureOwnerToken == claim.token else { return }
            failureTask = task
        }
        return task
    }

    private func teardownGraph(
        discardQueuedItems: Bool,
        onlyIfOperation expectedOperation: UInt64? = nil
    ) async {
        let taskAndOwner: (Task<Void, Never>, UInt64)? = stateLock.withLock {
            if let teardownTask, let teardownTaskOwner { return (teardownTask, teardownTaskOwner) }
            if let expectedOperation,
               (lifecycleEpoch != expectedOperation || lifecycleState == .idle) {
                // A stale duplicate-stop waiter must not recreate a teardown
                // task after a newer stop owner has already completed and
                // returned the source to idle.  If a newer owner is still
                // active, its task was returned by the branch above and is
                // joined instead.
                return nil
            }
            nextTeardownTaskOwner &+= 1
            let owner = nextTeardownTaskOwner
            let task = Task { [weak self] () -> Void in
                guard let self else { return }
                let pauseHook = self.stateLock.withLock { self.teardownPauseHookForTesting }
                if let pauseHook { await pauseHook() }
                await self.performTeardown(discardQueuedItems: discardQueuedItems)
            }
            teardownTask = task
            teardownTaskOwner = owner
            return (task, owner)
        }
        guard let taskAndOwner else { return }
        await taskAndOwner.0.value
        let completionHook = stateLock.withLock { teardownCompletionHookForTesting }
        completionHook?(taskAndOwner.1)
        stateLock.withLock {
            guard teardownTaskOwner == taskAndOwner.1 else { return }
            teardownTask = nil
            teardownTaskOwner = nil
        }
    }

    private func fenceCurrentGeneration(_ generation: UInt64) {
        ringUseLock.withLock {
            let fence: (ring: OpaquePointer, hook: (@Sendable () -> Void)?)? = stateLock.withLock {
                guard currentGeneration == generation,
                      let ring = self.ring,
                      terminalRetiredGeneration != generation else { return nil }
                return (ring: ring, hook: ringFenceHookForTesting)
            }
            if let fence {
                fence.hook?()
                TarsRealtimeAudioRingSetGeneration(fence.ring, 0)
            }
        }
    }

    private func performTeardown(discardQueuedItems: Bool) async {
        let cancelledSleepWakeCompletions: [CompletionLatch] = stateLock.withLock {
            lifecycleState = .stopping
            let completions = pendingSleepWakeEvents.map(\.completion)
            pendingSleepWakeEvents.removeAll(keepingCapacity: true)
            sleepWakeEventTaskActive = false
            sleepWakeEventTaskToken &+= 1
            return completions
        }
        cancelledSleepWakeCompletions.forEach { $0.finish() }

        // Fence before touching any HAL object.  A late callback sees zero and
        // can only increment the stale counter; it cannot publish a slot.
        let generation = stateLock.withLock { currentGeneration }
        fenceCurrentGeneration(generation)
        let watchdog = stateLock.withLock { () -> Task<Void, Never>? in
            let task = watchdogTask
            watchdogTask = nil
            watchdogExpiredDuringStart = nil
            return task
        }
        watchdog?.cancel()

        var retainedListenerTokens: [UInt64] = []
        let listenersToRemove = stateLock.withLock { listenerTokens }
        for token in listenersToRemove {
            do { try hal.removeListener(token) }
            catch {
                retainedListenerTokens.append(token)
                recordCleanupFailure(error)
            }
        }
        stateLock.withLock {
            // No acquisition may append a listener while teardown owns the
            // lifecycle.  Keep the assignment under the same lock anyway so
            // duplicate stop callers cannot observe a partially updated edge.
            listenerTokens = retainedListenerTokens
        }
        let listenersRemoved = stateLock.withLock { listenerTokens.isEmpty }

        let stopEdges = stateLock.withLock { (aggregateID, ioProcID, aggregateStarted) }
        if let aggregateID = stopEdges.0, let ioProcID = stopEdges.1, stopEdges.2 {
            do {
                try hal.stop(aggregateID: aggregateID, ioProc: ioProcID)
                stateLock.withLock {
                    if self.aggregateID == aggregateID, self.ioProcID == ioProcID {
                        aggregateStarted = false
                        stopFailureOutstanding = false
                    }
                }
            } catch {
                // Retain the started edge and IOProc token for a retry.  The
                // remaining cleanup edges are still attempted below.
                stateLock.withLock { stopFailureOutstanding = true }
                recordCleanupFailure(error)
            }
        }

        // Do not destroy an IOProc while AudioDeviceStop failed: the running
        // callback still owns the ring.  Keeping the aggregate context until
        // the IOProc edge succeeds also preserves the exact identifiers needed
        // for a later retry.
        let ioState = stateLock.withLock { (ioProcID, aggregateID, aggregateStarted) }
        if let ioProcID = ioState.0, !ioState.2 {
            if let aggregateID = ioState.1 {
                do {
                    try hal.destroyIOProc(aggregateID: aggregateID, ioProc: ioProcID)
                    stateLock.withLock {
                        if self.ioProcID == ioProcID, self.aggregateID == aggregateID {
                            self.ioProcID = nil
                            pendingIOProcAggregateID = nil
                        }
                    }
                } catch {
                    recordCleanupFailure(error)
                }
            } else {
                recordCleanupFailure(CompanionError.invalid("o IOProc não possui contexto de dispositivo para liberação"))
            }
        }

        let drain = stateLock.withLock { drainTask }
        drain?.cancel()
        await drain?.value
        stateLock.withLock { drainTask = nil }

        let workerBeforeDiscard = stateLock.withLock { deliveryWorker }
        if discardQueuedItems {
            // Close worker admission before removing the queue.  A cancelled
            // drain may have completed conversion concurrently with this
            // teardown edge; fencing first makes that late enqueue return a
            // rejected, zeroized item instead of recreating work after the
            // terminal/rebuild owner captured its evidence.
            _ = workerBeforeDiscard?.fenceAndDiscard()
        }
        stateLock.withLock {
            pendingOverflowBoundaries.removeAll(keepingCapacity: true)
            sleepingGeneration = nil
        }

        // Listener tokens and their aggregate/tap identifiers form one
        // ownership chain.  If even one listener removal failed, retain the
        // aggregate and tap so a retry can use the still-valid identifiers.
        // Likewise, never destroy a dependent object while an earlier edge
        // (stop, IOProc destruction, or detach) remains unresolved.
        let dependencyState = stateLock.withLock {
            (ioProcID, aggregateStarted, tapAttached, aggregateID, tapID, aggregateDestroySucceeded)
        }
        if listenersRemoved, dependencyState.0 == nil, !dependencyState.1 {
            if dependencyState.2, let aggregateID = dependencyState.3, let tapID = dependencyState.4 {
                do {
                    try hal.detachTap(tapID: tapID, aggregateID: aggregateID)
                    stateLock.withLock {
                        if self.tapID == tapID, self.aggregateID == aggregateID { tapAttached = false }
                    }
                } catch {
                    recordCleanupFailure(error)
                }
            }

            let aggregateState = stateLock.withLock { (tapAttached, aggregateID, aggregateDestroySucceeded) }
            if !aggregateState.0, let aggregateID = aggregateState.1, !aggregateState.2 {
                do {
                    try hal.destroyAggregate(aggregateID: aggregateID)
                    stateLock.withLock {
                        if self.aggregateID == aggregateID {
                            aggregateDestroySucceeded = true
                            // Retain the success bit until the dependent tap
                            // has also been released; this prevents a retry
                            // from destroying the same aggregate twice.
                            self.aggregateID = nil
                        }
                    }
                } catch {
                    recordCleanupFailure(error)
                }
            }

            // A tap may be destroyed only after it is detached and its
            // aggregate has either never existed or was destroyed
            // successfully.  On failure retain tapID for the retry.
            let tapState = stateLock.withLock { (tapAttached, aggregateID, aggregateDestroySucceeded, tapID) }
            if !tapState.0, (tapState.1 == nil || tapState.2), let tapID = tapState.3 {
                do {
                    try hal.destroyProcessTap(tapID: tapID)
                    stateLock.withLock {
                        if self.tapID == tapID {
                            self.tapID = nil
                            tapAttached = false
                        }
                    }
                } catch {
                    recordCleanupFailure(error)
                }
            }
        }

        stateLock.withLock {
            if tapID == nil { processObjectID = nil }
        }

        // A ring may be freed only after callback ownership is proven
        // quiesced by successful IOProc destruction.  If that edge remains
        // unresolved, leave the fenced ring and all dependent IDs retained so
        // a later explicit stop can retry without a UAF shortcut.
        let ringToDestroy: OpaquePointer? = ringUseLock.withLock {
            let callbackOwnershipRemains = stateLock.withLock { self.ioProcID != nil || self.aggregateStarted }
            guard !callbackOwnershipRemains else {
                recordCleanupFailure(CompanionError.invalid("o Process Tap não confirmou a finalização do callback realtime"))
                return nil
            }
            return stateLock.withLock {
                let retained = self.ring
                self.ring = nil
                self.terminalRetiredGeneration = nil
                return retained
            }
        }
        if let ringToDestroy {
            TarsRealtimeAudioRingDestroy(ringToDestroy)
        }
        // The drain task is already cancelled and joined above, so the
        // scratch bytes can be wiped and released even when another cleanup
        // edge remains retained for a later retry.
        stateLock.withLock {
            drainScratch.resetBytes(in: drainScratch.startIndex..<drainScratch.endIndex)
            drainScratch = Data()
            converter = nil
            inputFormat = nil
        }

        if let worker = workerBeforeDiscard {
            let outcome = await worker.stop(deadlineNanoseconds: stopDeadlineNanoseconds)
            if case .inFlight(let invocation) = outcome {
                quarantine.adopt(invocation)
            }
            stateLock.withLock {
                if deliveryWorker === worker { deliveryWorker = nil }
            }
        }

        let noRetainedEdges = stateLock.withLock {
            listenerTokens.isEmpty && ioProcID == nil && aggregateID == nil && tapID == nil &&
                ring == nil && !tapAttached && !aggregateStarted && pendingIOProcAggregateID == nil &&
                !stopFailureOutstanding
        }
        if noRetainedEdges {
            stateLock.withLock {
                cleanupDiagnostic = nil
                aggregateDestroySucceeded = false
                watchdogExpiredDuringStart = nil
            }
        }
    }

    private func drainLoop() async {
        while !Task.isCancelled {
            if !drainOne() {
                try? await Task.sleep(nanoseconds: 1_000_000)
            }
        }
        while drainOne() { }
    }

    private func drainOne(ignoringTestPause: Bool = false) -> Bool {
        guard ignoringTestPause || !stateLock.withLock({ pauseDrainForTesting }) else { return false }
        drainLock.lock()
        var didWork = false
        var pendingAction: (action: SystemAudioCaptureMonitorAction, generation: UInt64, operation: UInt64)?
        ringUseLock.lock()
        let isRunning = stateLock.withLock { lifecycleState == .running }
        if let ring = stateLock.withLock({ self.ring }),
           let converter = stateLock.withLock({ self.converter }) {
            let worker = stateLock.withLock { deliveryWorker }
            let counters = TarsRealtimeAudioRingSnapshot(ring)
            if isRunning {
                let counterAction = monitor.observeCounters(counters)
                if counterAction != .none,
                   let operation = stateLock.withLock({
                       lifecycleState == .running && currentGeneration == converter.identity.captureGeneration
                           ? lifecycleEpoch : nil
                   }) {
                    pendingAction = (counterAction, converter.identity.captureGeneration, operation)
                }
            }

            let expectedGeneration = converter.identity.captureGeneration
            let activeRingGeneration = TarsRealtimeAudioRingGeneration(ring)
            if pendingAction == nil && isRunning {
                if activeRingGeneration != expectedGeneration {
                    // A listener/teardown fence invalidates every queued raw
                    // slot.  Pop only to zeroize/release it; never decode an
                    // old-format item after the fence.
                    stateLock.withLock { pendingOverflowBoundaries.removeAll(keepingCapacity: true) }
                    while popRingItem(ring, decode: false).0 == 1 { didWork = true }
                } else {
                    // Consume the producer's fixed-size FIFO records.  The
                    // absolute producer write cursor is the boundary; the
                    // later moving retained count is intentionally ignored.
                    pollOverflowBoundaries(ring)

                    let capacity = Int(TarsRealtimeAudioRingSlotCapacity(ring))
                    let scratchCount = stateLock.withLock { drainScratch.count }
                    if scratchCount < capacity {
                        if let operation = stateLock.withLock({
                            lifecycleState == .running && currentGeneration == expectedGeneration ? lifecycleEpoch : nil
                        }) {
                            pendingAction = (
                                monitor.observeFailure("o buffer de drenagem realtime é menor que a capacidade do ring"),
                                expectedGeneration,
                                operation
                            )
                        }
                    } else {
                        _ = emitPendingOverflowBoundaryIfReady(
                            ring,
                            converter: converter,
                            worker: worker,
                            didWork: &didWork
                        )
                        if stateLock.withLock({ pendingOverflowBoundaries.isEmpty }) {
                            // No pending boundary: normal dequeue may consume
                            // any frame published after the last episode.
                            let preConvertSequence = converter.nextSequenceForGap
                            let preConvertSample = converter.nextSampleForGap
                            let popped = popRingItem(ring, decode: true)
                            if popped.0 == -2 {
                                // The producer published an overflow boundary
                                // after the Swift poll but before this pop.
                                // The C bridge makes that cursor uncrossable;
                                // retry metadata first and emit its marker
                                // before allowing any post-boundary frame.
                                pollOverflowBoundaries(ring)
                                _ = emitPendingOverflowBoundaryIfReady(
                                    ring,
                                    converter: converter,
                                    worker: worker,
                                    didWork: &didWork
                                )
                            }
                            if popped.0 == 1 {
                                didWork = true
                                // Recheck after the pop as well as before it.
                                // A format listener may have fenced the
                                // generation while this slot was copied out.
                                if TarsRealtimeAudioRingGeneration(ring) == expectedGeneration,
                                   let item = popped.1 {
                                    do {
                                        let conversionHook = stateLock.withLock { conversionHookForTesting }
                                        conversionHook?()
                                        let frames = try converter.convert(item)
                                        if converter.takeLastDiscontinuity() != nil,
                                           let gap = makeTimestampUnknownEndGap(
                                               identity: converter.identity,
                                               firstSequence: preConvertSequence,
                                               firstSample: preConvertSample
                                           ) {
                                            // The gap is anchored at the
                                            // cursor observed before
                                            // conversion, before any frames
                                            // produced from the new timestamp
                                            // region.
                                            worker?.enqueue(.gap(gap))
                                        }
                                        if CanonicalSystemAudioConverter.containsFiniteNonzeroSignal(item) {
                                            let action = monitor.observeFunctionalNonzeroSignal()
                                            if action != .none,
                                               let operation = stateLock.withLock({
                                                   lifecycleState == .running && currentGeneration == item.generation
                                                       ? lifecycleEpoch : nil
                                               }) {
                                                pendingAction = (action, item.generation, operation)
                                            }
                                        } else {
                                            let action = monitor.observeSilentNonempty()
                                            if action != .none,
                                               let operation = stateLock.withLock({
                                                   lifecycleState == .running && currentGeneration == item.generation
                                                       ? lifecycleEpoch : nil
                                               }) {
                                                pendingAction = (action, item.generation, operation)
                                            }
                                        }
                                        for frame in frames { worker?.enqueue(.frame(frame)) }
                                    } catch {
                                        if let operation = stateLock.withLock({
                                            lifecycleState == .running && currentGeneration == item.generation ? lifecycleEpoch : nil
                                        }) {
                                            pendingAction = (
                                                monitor.observeFailure(Self.errorMessage(error)),
                                                item.generation,
                                                operation
                                            )
                                        }
                                    }
                                }
                            }
                        } else if let producerWriteIndex = stateLock.withLock({ pendingOverflowBoundaries.first?.producerWriteIndex }) {
                            // Limit this pop to the absolute write cursor
                            // captured for the episode.  A producer that
                            // publishes after metadata polling therefore
                            // remains for the post-marker drain turn.
                            let preConvertSequence = converter.nextSequenceForGap
                            let preConvertSample = converter.nextSampleForGap
                            let popped = popRingItem(
                                ring,
                                decode: true,
                                throughWriteIndex: producerWriteIndex
                            )
                            if popped.0 == -2 {
                                // A later producer episode reached the
                                // consumer cursor while this earlier episode
                                // was being drained.  Keep the C-side FIFO
                                // boundary uncrossed and consume metadata
                                // before retrying the retained audio.
                                pollOverflowBoundaries(ring)
                                _ = emitPendingOverflowBoundaryIfReady(
                                    ring,
                                    converter: converter,
                                    worker: worker,
                                    didWork: &didWork
                                )
                            }
                            if popped.0 == 1 {
                                didWork = true
                                // Recheck after the pop as well as before it.
                                // A format listener may have fenced the
                                // generation while this slot was copied out.
                                if TarsRealtimeAudioRingGeneration(ring) == expectedGeneration,
                                   let item = popped.1 {
                                    do {
                                        let conversionHook = stateLock.withLock { conversionHookForTesting }
                                        conversionHook?()
                                        let frames = try converter.convert(item)
                                        if converter.takeLastDiscontinuity() != nil,
                                           let gap = makeTimestampUnknownEndGap(
                                               identity: converter.identity,
                                               firstSequence: preConvertSequence,
                                               firstSample: preConvertSample
                                           ) {
                                            worker?.enqueue(.gap(gap))
                                        }
                                        if CanonicalSystemAudioConverter.containsFiniteNonzeroSignal(item) {
                                            let action = monitor.observeFunctionalNonzeroSignal()
                                            if action != .none,
                                               let operation = stateLock.withLock({
                                                   lifecycleState == .running && currentGeneration == item.generation
                                                       ? lifecycleEpoch : nil
                                               }) {
                                                pendingAction = (action, item.generation, operation)
                                            }
                                        } else {
                                            let action = monitor.observeSilentNonempty()
                                            if action != .none,
                                               let operation = stateLock.withLock({
                                                   lifecycleState == .running && currentGeneration == item.generation
                                                       ? lifecycleEpoch : nil
                                               }) {
                                                pendingAction = (action, item.generation, operation)
                                            }
                                        }
                                        for frame in frames { worker?.enqueue(.frame(frame)) }
                                    } catch {
                                        if let operation = stateLock.withLock({
                                            lifecycleState == .running && currentGeneration == item.generation ? lifecycleEpoch : nil
                                        }) {
                                            pendingAction = (
                                                monitor.observeFailure(Self.errorMessage(error)),
                                                item.generation,
                                                operation
                                            )
                                        }
                                    }
                                }
                            }
                            _ = emitPendingOverflowBoundaryIfReady(
                                ring,
                                converter: converter,
                                worker: worker,
                                didWork: &didWork
                            )
                        }
                    }
                }
            }
            if pendingAction == nil,
               let worker,
               worker.evidenceOverflowed,
               let operation = stateLock.withLock({
                   lifecycleState == .running && currentGeneration == expectedGeneration ? lifecycleEpoch : nil
               }) {
                // A bounded per-identity sidecar cannot represent an
                // unbounded number of identities.  Once it is full, fail
                // loudly through the normal terminal-evidence path instead
                // of silently dropping a boundary or inventing a cursor.
                pendingAction = (
                    monitor.observeFailure("a fila de evidência de entrega excedeu a capacidade por identidade"),
                    expectedGeneration,
                    operation
                )
            }
        }
        ringUseLock.unlock()
        drainLock.unlock()
        if let pendingAction {
            applyMonitorAction(
                pendingAction.action,
                generation: pendingAction.generation,
                operation: pendingAction.operation
            )
        }
        return didWork
    }

    @discardableResult
    private func emitPendingOverflowBoundaryIfReady(
        _ ring: OpaquePointer,
        converter: CanonicalSystemAudioConverter,
        worker: ProcessTapDeliveryWorker?,
        didWork: inout Bool
    ) -> Bool {
        guard let boundary = stateLock.withLock({ pendingOverflowBoundaries.first }),
              TarsRealtimeAudioRingReadIndex(ring) >= boundary.producerWriteIndex else {
            return false
        }
        if let gap = makeTimestampUnknownEndGap(
            identity: converter.identity,
            firstSequence: converter.nextSequenceForGap,
            firstSample: converter.nextSampleForGap,
            reason: .overflow
        ) {
            worker?.enqueue(.gap(gap))
        }
        _ = stateLock.withLock { pendingOverflowBoundaries.removeFirst() }
        didWork = true
        return true
    }

    private func pollOverflowBoundaries(_ ring: OpaquePointer) {
        var boundary = TarsRealtimeOverflowBoundary(
            producerWriteIndex: 0,
            producerReadIndex: 0,
            episodeNumber: 0,
            retainedSlotCount: 0
        )
        while TarsRealtimeAudioRingPopOverflowBoundary(ring, &boundary) {
            stateLock.withLock {
                pendingOverflowBoundaries.append(PendingOverflowBoundary(
                    producerWriteIndex: boundary.producerWriteIndex,
                    producerReadIndex: boundary.producerReadIndex,
                    episodeNumber: boundary.episodeNumber
                ))
            }
        }
    }

    private func popRingItem(
        _ ring: OpaquePointer,
        decode: Bool,
        throughWriteIndex: UInt64? = nil
    ) -> (Int32, ProcessTapPCMBuffer?) {
        var item: ProcessTapPCMBuffer?
        var result: Int32 = 0
        stateLock.withLock {
            drainScratch.withUnsafeMutableBytes { bytes in
                var output = TarsRealtimeSlotOutput(
                    bufferCount: 0,
                    bufferByteSizes: (0, 0, 0, 0, 0, 0, 0, 0),
                    bufferChannels: (0, 0, 0, 0, 0, 0, 0, 0),
                    totalBytes: 0,
                    asbd: TarsRealtimeASBDSnapshot(sampleRate: 0, formatID: 0, formatFlags: 0, bytesPerPacket: 0, framesPerPacket: 0, bytesPerFrame: 0, channelsPerFrame: 0, bitsPerChannel: 0, isInterleaved: 0),
                    sampleTime: 0,
                    hostTime: 0,
                    timestampFlags: 0,
                    generation: 0,
                    bytes: bytes.baseAddress?.assumingMemoryBound(to: UInt8.self),
                    byteCapacity: UInt32(bytes.count)
                )
                if let throughWriteIndex {
                    result = TarsRealtimeAudioRingPopThrough(ring, throughWriteIndex, &output)
                } else {
                    result = TarsRealtimeAudioRingPop(ring, &output)
                }
                if decode, result == 1 {
                    item = ProcessTapPCMBuffer(output: output)
                }
            }
        }
        return (result, item)
    }

    private func applyMonitorAction(
        _ action: SystemAudioCaptureMonitorAction,
        generation: UInt64,
        operation: UInt64? = nil
    ) {
        switch action {
        case .granted:
            publishRunningIfOwned(
                SourceHealth(permission: .granted, route: .healthy, deviceIdentity: configuration.deviceIdentity ?? "ProcessTap.SystemAudio"),
                generation: generation,
                operation: operation
            )
        case .denied(let message), .ambiguous(let message), .failed(let message):
            _ = beginTerminalFailure(message: message, generation: generation, operation: operation)
        default:
            break
        }
    }

    @discardableResult
    private func abortStartingWithFailure(message: String, generation: UInt64, operation: UInt64? = nil) -> Bool {
        stateLock.withLock {
            guard lifecycleState == .starting,
                  currentGeneration == generation,
                  (operation.map { lifecycleEpoch == $0 } ?? true) else { return false }
            startupAbort = (
                generation: generation,
                operation: lifecycleEpoch,
            reason: "callback-failure: \(message)"
            )
            lifecycleEpoch &+= 1
            lifecycleState = .stopping
            return true
        }
    }

    private func publish(_ newStatus: CaptureSourceStatus, generation: UInt64) {
        let updates: [CaptureSourceHealthObserver] = stateLock.withLock {
            currentStatus = newStatus
            return Array(observers.values)
        }
        let update = CaptureSourceHealthUpdate(source: source, generation: generation, status: newStatus)
        for observer in updates { observer(update) }
    }

    /// Running health is a lease on one lifecycle epoch.  An asynchronous
    /// converter/monitor action may finish after a terminal failure has taken
    /// ownership of the graph; it is then stale and must not resurrect the
    /// visible running state.
    private func publishRunningIfOwned(_ health: SourceHealth, generation: UInt64, operation: UInt64?) {
        publishOwned(.running(health), generation: generation, operation: operation)
    }

    private func publishOwned(_ newStatus: CaptureSourceStatus, generation: UInt64, operation: UInt64?) {
        let updates: [CaptureSourceHealthObserver]? = stateLock.withLock {
            guard lifecycleState == .running,
                  currentGeneration == generation,
                  operation.map({ lifecycleEpoch == $0 }) ?? false else { return nil }
            currentStatus = newStatus
            return Array(observers.values)
        }
        guard let updates else { return }
        let update = CaptureSourceHealthUpdate(source: source, generation: generation, status: newStatus)
        for observer in updates { observer(update) }
    }

    private func recordCleanupFailure(_ error: Error) {
        let message = Self.errorMessage(error)
        stateLock.withLock {
            let combined = [cleanupDiagnostic, message].compactMap { $0 }.joined(separator: "; ")
            cleanupDiagnostic = String(combined.prefix(2_048))
        }
    }

    private func makeIdentity(generation: UInt64) -> SourceIdentity {
        (try? SourceIdentity(
            sessionID: configuration.identity.sessionID,
            streamID: configuration.identity.streamID,
            captureGeneration: generation,
            source: .systemAudio,
            sampleRate: 16_000,
            channelCount: 1
        )) ?? configuration.identity
    }

    private func makeUnknownEndGap(
        identity: SourceIdentity,
        firstSequence: UInt64?,
        firstSample: UInt64?,
        reason: GapReason
    ) -> CoverageGap? {
        try? CoverageGap(
            identity: identity,
            firstSample: firstSample,
            lastSampleExclusive: nil,
            reason: reason,
            firstSequence: firstSequence,
            deviceID: configuration.deviceIdentity ?? "ProcessTap.SystemAudio",
            firstCapturedAtMonotonicNs: monotonicClock(),
            firstCapturedAtWallClockMs: wallClock(),
            boundary: .unknownEnd
        )
    }

    /// Converts every discarded delivery boundary into evidence without
    /// comparing sequence/sample cursors from different identities.  The
    /// worker's boundary array is already in stable causal queue order; the
    /// fallback converter cursor is merged only with its matching identity.
    private func makeDiscardedGaps(
        boundaries: [ProcessTapDeliveryWorker.DiscardedDeliveryBoundary],
        fallback: (identity: SourceIdentity, firstSequence: UInt64, firstSample: UInt64),
        reason: GapReason,
        includeFallback: Bool
    ) -> [CoverageGap] {
        var gaps: [CoverageGap] = []
        var indexes: [SourceIdentity: Int] = [:]
        for boundary in boundaries {
            var firstSequence = boundary.firstSequence
            var firstSample = boundary.firstSample
            if boundary.identity == fallback.identity {
                let fallbackKey = (fallback.firstSequence, fallback.firstSample)
                let boundaryKey = (firstSequence ?? UInt64.max, firstSample ?? UInt64.max)
                if fallbackKey < boundaryKey {
                    firstSequence = fallback.firstSequence
                    firstSample = fallback.firstSample
                }
            }
            guard let gap = makeUnknownEndGap(
                identity: boundary.identity,
                firstSequence: firstSequence,
                firstSample: firstSample,
                reason: reason
            ) else { continue }
            if let index = indexes[boundary.identity] {
                // Boundaries are normally deduplicated by the worker.  Keep
                // this defensive merge identity-local if that invariant ever
                // changes; never order one identity against another by cursor.
                let existing = gaps[index]
                let existingKey = (existing.firstSequence ?? UInt64.max, existing.firstSample ?? UInt64.max)
                let gapKey = (gap.firstSequence ?? UInt64.max, gap.firstSample ?? UInt64.max)
                if gapKey < existingKey { gaps[index] = gap }
            } else {
                indexes[boundary.identity] = gaps.count
                gaps.append(gap)
            }
        }
        if includeFallback, indexes[fallback.identity] == nil,
           let fallbackGap = makeUnknownEndGap(
               identity: fallback.identity,
               firstSequence: fallback.firstSequence,
               firstSample: fallback.firstSample,
               reason: reason
           ) {
            gaps.append(fallbackGap)
        }
        return gaps
    }

    private func makeTimestampUnknownEndGap(
        identity: SourceIdentity,
        firstSequence: UInt64,
        firstSample: UInt64,
        reason: GapReason = .unknownEnd
    ) -> CoverageGap? {
        try? CoverageGap(
            identity: identity,
            firstSample: firstSample,
            lastSampleExclusive: nil,
            reason: reason,
            firstSequence: firstSequence,
            deviceID: configuration.deviceIdentity ?? "ProcessTap.SystemAudio",
            firstCapturedAtMonotonicNs: monotonicClock(),
            firstCapturedAtWallClockMs: wallClock(),
            boundary: .unknownEnd
        )
    }

    private static func snapshot(_ format: ProcessTapPCMFormat) -> TarsRealtimeASBDSnapshot {
        TarsRealtimeASBDSnapshot(
            sampleRate: format.sampleRate,
            formatID: format.formatID,
            formatFlags: format.formatFlags,
            bytesPerPacket: UInt32(format.bytesPerPacket),
            framesPerPacket: UInt32(format.framesPerPacket),
            bytesPerFrame: UInt32(format.bytesPerFrame),
            channelsPerFrame: UInt32(format.channelCount),
            bitsPerChannel: UInt32(format.bitsPerChannel),
            isInterleaved: format.isInterleaved ? 1 : 0
        )
    }

    private static func counterSnapshot(_ counters: TarsRealtimeCounters) -> SystemAudioCaptureCounterSnapshot {
        SystemAudioCaptureCounterSnapshot(
            callbackArrivals: counters.callbackArrivals,
            validNonemptyArrivals: counters.validNonemptyArrivals,
            emptyArrivals: counters.emptyArrivals,
            malformedArrivals: counters.malformedArrivals,
            capacityRejectedArrivals: counters.capacityRejectedArrivals,
            ringOverflowCount: counters.ringOverflowCount,
            ringOverflowMetadataDrops: counters.ringOverflowMetadataDrops,
            cursorOverflow: counters.cursorOverflow
        )
    }

    private static func errorMessage(_ error: Error) -> String {
        let raw = String(describing: error)
        let sanitized = raw
            .replacingOccurrences(of: "\n", with: " ")
            .replacingOccurrences(of: "\r", with: " ")
        return String(sanitized.prefix(512))
    }

    private static func eventName(_ event: ProcessTapHALEvent) -> String {
        switch event {
        case .sleep: return "sleep"
        case .wake: return "wake"
        case .serviceReset: return "service-reset"
        case .tapListChanged: return "tap-list"
        case .deviceAlive: return "device"
        case .deviceAliveReadFailed(let status): return "device-read-failure-\(status)"
        case .tapFormatChanged: return "tap-format"
        }
    }

    private static func startupAbortReason(for event: ProcessTapHALEvent) -> String {
        if case .deviceAliveReadFailed(let status) = event,
           status == kAudioDevicePermissionsError {
            return permissionDeniedStartupMarker
        }
        return eventName(event)
    }

    private func withUnsafeBytesForInputs<T>(_ data: [Data], _ body: ([UnsafePointer<UInt8>?]) -> T) -> T {
        var pointers = Array<UnsafePointer<UInt8>?>(repeating: nil, count: data.count)
        func recurse(_ index: Int) -> T {
            guard index < data.count else { return body(pointers) }
            return data[index].withUnsafeBytes { raw in
                pointers[index] = raw.baseAddress?.assumingMemoryBound(to: UInt8.self)
                return recurse(index + 1)
            }
        }
        return recurse(0)
    }
}

// Internal for the deterministic offline saturation fixture; production code
// reaches it only through ProcessTapDeliveryWorker.
final class ProcessTapDeliveryQueue: @unchecked Sendable {
    enum Item {
        case frame(AudioFrame)
        case gap(CoverageGap)
    }

    private struct QueuedItem {
        let item: Item
        let order: UInt64
    }

    private struct PendingLoss {
        var gap: CoverageGap
        var order: UInt64
    }

    private let lock = NSLock()
    private var items: [QueuedItem] = []
    // A bounded sidecar retains causal loss markers that do not fit in the
    // delivery queue itself.  This keeps the queue's physical count at or
    // below capacity while preserving one earliest marker for each source
    // identity when an eviction spans generations.
    private var pendingLosses: [SourceIdentity: PendingLoss] = [:]
    private var nextOrder: UInt64 = 0
    private var lossEvidenceOverflowed = false
    private let capacity: Int
    private let pendingLossCapacity: Int

    init(capacity: Int = 256) {
        self.capacity = max(2, capacity)
        self.pendingLossCapacity = 64
    }

    deinit { removeAll() }

    func enqueue(_ item: Item) {
        lock.lock()
        defer { lock.unlock() }
        guard nextOrder != UInt64.max else {
            // Causal ordering is part of the delivery contract.  Once the
            // order token itself is exhausted, retaining an item with a
            // recycled cursor would silently make two unrelated boundaries
            // indistinguishable.  Surface a bounded evidence failure and
            // release the incoming payload instead.
            lossEvidenceOverflowed = true
            Self.zeroize(item)
            return
        }
        let order = nextOrder
        nextOrder += 1

        // Compute marker presence before planning any eviction.  The plan is
        // then recomputed against the retained prefix after each candidate is
        // removed: if that candidate was the only physical marker for an
        // identity, its replacement is reserved before the incoming item is
        // admitted.  Existing sidecar markers already own their logical place
        // and need no new physical slot.
        let markerIdentitiesBeforeEviction = Set(items.compactMap { entry -> SourceIdentity? in
            guard case .gap(let gap) = entry.item, gap.reason == .overflow else { return nil }
            return gap.identity
        })
        var removed: [QueuedItem] = []
        var retained = items
        var markerAlreadyPresent = markerIdentitiesBeforeEviction
        while true {
            let removedIdentities = Set(removed.compactMap { Self.overflowGap(from: $0.item)?.identity })
            let replacementIdentities = removedIdentities
                .subtracting(markerAlreadyPresent)
                .subtracting(pendingLosses.keys)
            guard retained.count + 1 + replacementIdentities.count > capacity,
                  !retained.isEmpty else { break }
            removed.append(retained.removeFirst())
            markerAlreadyPresent = Set(retained.compactMap { entry -> SourceIdentity? in
                guard case .gap(let gap) = entry.item, gap.reason == .overflow else { return nil }
                return gap.identity
            })
        }
        items = retained

        // A marker may have been removed even though it was present in the
        // initial reservation set.  Fold all evictions into one earliest
        // boundary per identity, then materialize as many markers as fit in
        // the physical queue and retain the remainder in the bounded
        // sidecar.  The physical queue never exceeds capacity at any step.
        var losses: [SourceIdentity: PendingLoss] = [:]
        for entry in removed {
            if let candidate = Self.overflowGap(from: entry.item) {
                mergeLoss(candidate, order: entry.order, into: &losses)
            }
            Self.zeroize(entry.item)
        }

        items.append(QueuedItem(item: item, order: order))
        var freeMarkerSlots = max(0, capacity - items.count)
        for loss in losses.values.sorted(by: { $0.order < $1.order }) {
            let identity = loss.gap.identity
            if let index = items.firstIndex(where: {
                guard case .gap(let gap) = $0.item else { return false }
                return gap.reason == .overflow && gap.identity == identity
            }),
               case .gap(let existingGap) = items[index].item {
                let merged = Self.mergeSameIdentity(existingGap, loss.gap)
                let markerOrder = min(items[index].order, loss.order)
                items[index] = QueuedItem(item: .gap(merged), order: markerOrder)
                continue
            }
            if var existing = pendingLosses[identity] {
                existing.gap = Self.mergeSameIdentity(existing.gap, loss.gap)
                existing.order = min(existing.order, loss.order)
                pendingLosses[identity] = existing
                continue
            }
            if freeMarkerSlots > 0 {
                let index = items.firstIndex { $0.order > loss.order } ?? items.count
                items.insert(QueuedItem(item: .gap(loss.gap), order: loss.order), at: index)
                freeMarkerSlots -= 1
            } else if pendingLosses.count < pendingLossCapacity {
                pendingLosses[identity] = loss
            } else {
                // The sidecar is intentionally bounded.  Preserve the fact
                // that evidence could not fit so a caller can fail loudly;
                // never silently collapse a different SourceIdentity.
                lossEvidenceOverflowed = true
            }
        }
        items.sort { $0.order < $1.order }
    }

    func dequeue() -> Item? {
        lock.withLock {
            guard !items.isEmpty || !pendingLosses.isEmpty else { return nil }
            let firstLoss = pendingLosses.min { $0.value.order < $1.value.order }
            if let firstLoss,
               items.first.map({ firstLoss.value.order <= $0.order }) ?? true {
                pendingLosses.removeValue(forKey: firstLoss.key)
                return .gap(firstLoss.value.gap)
            }
            return items.removeFirst().item
        }
    }

    func removeAll() {
        _ = removeAllItems()
    }

    func removeAllItems() -> [Item] {
        let removed = lock.withLock { () -> [Item] in
            let entries = items.map { ($0.order, $0.item) } + pendingLosses.values.map { ($0.order, Item.gap($0.gap)) }
            items.removeAll(keepingCapacity: true)
            pendingLosses.removeAll(keepingCapacity: true)
            return entries.sorted { $0.0 < $1.0 }.map(\.1)
        }
        removed.forEach(Self.zeroize)
        return removed
    }

    var count: Int { lock.withLock { items.count } }
    var evidenceOverflowed: Bool { lock.withLock { lossEvidenceOverflowed } }

    private func mergeLoss(_ candidate: CoverageGap, order: UInt64, into losses: inout [SourceIdentity: PendingLoss]) {
        let identity = candidate.identity
        if var existing = losses[identity] {
            existing.gap = Self.mergeSameIdentity(existing.gap, candidate)
            existing.order = min(existing.order, order)
            losses[identity] = existing
        } else {
            losses[identity] = PendingLoss(
                gap: Self.overflowGap(from: candidate) ?? candidate,
                order: order
            )
        }
    }

    private static func mergeSameIdentity(_ lhs: CoverageGap, _ rhs: CoverageGap) -> CoverageGap {
        // This helper is called only after the identity dictionary key has
        // matched.  Keeping the guard makes that invariant explicit and
        // prevents accidental sequence/sample comparison across generations.
        guard lhs.identity == rhs.identity else { return lhs }
        let lhsKey = (lhs.firstSequence ?? UInt64.max, lhs.firstSample ?? UInt64.max)
        let rhsKey = (rhs.firstSequence ?? UInt64.max, rhs.firstSample ?? UInt64.max)
        return rhsKey < lhsKey ? (Self.overflowGap(from: rhs) ?? rhs) : (Self.overflowGap(from: lhs) ?? lhs)
    }

    private static func identity(of item: Item) -> SourceIdentity {
        switch item {
        case .frame(let frame): return frame.identity
        case .gap(let gap): return gap.identity
        }
    }

    private static func overflowGap(from item: Item) -> CoverageGap? {
        switch item {
        case .frame(let frame): return overflowGap(from: frame)
        case .gap(let gap): return overflowGap(from: gap)
        }
    }

    private static func overflowGap(from gap: CoverageGap) -> CoverageGap? {
        return try? CoverageGap(
            identity: gap.identity,
            firstSample: gap.firstSample,
            lastSampleExclusive: nil,
            reason: .overflow,
            firstSequence: gap.firstSequence,
            deviceID: gap.deviceID,
            firstCapturedAtMonotonicNs: gap.firstCapturedAtMonotonicNs,
            firstCapturedAtWallClockMs: gap.firstCapturedAtWallClockMs,
            boundary: .unknownEnd
        )
    }

    private static func overflowGap(from frame: AudioFrame) -> CoverageGap? {
        try? CoverageGap(
            identity: frame.identity,
            firstSample: frame.firstSample,
            lastSampleExclusive: nil,
            reason: .overflow,
            firstSequence: frame.sequence,
            deviceID: frame.eventContext.deviceID,
            firstCapturedAtMonotonicNs: frame.eventContext.capturedAtMonotonicNs,
            firstCapturedAtWallClockMs: frame.eventContext.capturedAtWallClockMs,
            boundary: .unknownEnd
        )
    }

    fileprivate static func zeroize(_ item: Item) {
        if case .frame(let frame) = item { frame.payload.zeroize() }
    }
}

private final class ProcessTapDeliveryInvocation: @unchecked Sendable {
    let item: ProcessTapDeliveryQueue.Item
    private let completion = CompletionLatch()
    private let task: Task<Void, Never>
    private let adoptedLock = NSLock()
    private var adopted = false

    init(item: ProcessTapDeliveryQueue.Item, sink: CaptureFrameSink) {
        self.item = item
        self.task = Task { [completion] in
            switch item {
            case .frame(let frame): try? await sink.receive(frame)
            case .gap(let gap): try? await sink.receiveGap(gap)
            }
            if case .frame(let frame) = item {
                frame.payload.zeroize()
            }
            completion.finish()
        }
    }

    func wait() async { await completion.wait() }

    var isFinished: Bool { completion.isFinished }

    func wait(timeoutNanoseconds: UInt64) async -> Bool {
        if completion.isFinished { return true }
        let start = DispatchTime.now().uptimeNanoseconds
        while !completion.isFinished {
            if Task.isCancelled { return completion.isFinished }
            let elapsed = DispatchTime.now().uptimeNanoseconds &- start
            if elapsed >= timeoutNanoseconds { return completion.isFinished }
            let remaining = timeoutNanoseconds - elapsed
            try? await Task.sleep(nanoseconds: min(remaining, 1_000_000))
        }
        return true
    }

    func adoptOnce() -> Bool { adoptedLock.withLock { if adopted { return false }; adopted = true; return true } }
}

private final class CompletionLatch: @unchecked Sendable {
    private let lock = NSLock()
    private var finished = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func finish() {
        let continuations = lock.withLock { () -> [CheckedContinuation<Void, Never>] in
            guard !finished else { return [] }
            finished = true
            let result = waiters
            waiters.removeAll()
            return result
        }
        continuations.forEach { $0.resume() }
    }

    var isFinished: Bool { lock.withLock { finished } }

    func wait() async {
        await withCheckedContinuation { continuation in
            let resumeNow = lock.withLock { () -> Bool in
                if finished { return true }
                waiters.append(continuation)
                return false
            }
            if resumeNow { continuation.resume() }
        }
    }
}

private final class ProcessTapQuarantineRegistry: @unchecked Sendable {
    private let lock = NSLock()
    private var invocations: [ObjectIdentifier: ProcessTapDeliveryInvocation] = [:]
    private var emptyHookForTesting: (@Sendable () -> Void)?

    var isEmpty: Bool { lock.withLock { invocations.isEmpty } }
    var hasActive: Bool { !isEmpty }

    var emptyHook: (@Sendable () -> Void)? {
        get { lock.withLock { emptyHookForTesting } }
        set { lock.withLock { emptyHookForTesting = newValue } }
    }

    func adopt(_ invocation: ProcessTapDeliveryInvocation) {
        guard invocation.adoptOnce() else { return }
        let id = ObjectIdentifier(invocation)
        lock.withLock { invocations[id] = invocation }
        Task { [weak self, weak invocation] in
            await invocation?.wait()
            guard let self else { return }
            let becameEmpty = self.lock.withLock { () -> Bool in
                self.invocations.removeValue(forKey: id)
                return self.invocations.isEmpty
            }
            if becameEmpty { self.emptyHook?() }
        }
    }
}

final class ProcessTapDeliveryWorker: @unchecked Sendable {
    struct DiscardedDeliveryBoundary: Sendable {
        let identity: SourceIdentity
        let firstSequence: UInt64?
        let firstSample: UInt64?
    }

    fileprivate enum StopOutcome {
        case complete
        case inFlight(ProcessTapDeliveryInvocation)
    }

    private let queue: ProcessTapDeliveryQueue
    private let sink: CaptureFrameSink?
    private let registry: ProcessTapQuarantineRegistry
    private let lock = NSLock()
    private var task: Task<Void, Never>?
    private var inFlight: ProcessTapDeliveryInvocation?
    private var stopping = false
    private var activated = false
    private var suspendedForTesting = false
    private var suspensionGate: CompletionLatch?
    private let activationGate = CompletionLatch()
    private var beforeDeliveryAdmissionHookForTesting: (@Sendable () -> Void)?

    fileprivate init(sink: CaptureFrameSink?, registry: ProcessTapQuarantineRegistry, queueCapacity: Int) {
        self.sink = sink
        self.registry = registry
        self.queue = ProcessTapDeliveryQueue(capacity: queueCapacity)
    }

    /// Installs the worker task under the same lock that owns the fenced
    /// state.  A start observed after `fenceAndDiscard` therefore refuses to
    /// create a late uncancellable consumer.
    @discardableResult
    func start() -> Bool {
        guard sink != nil else { return true }
        return lock.withLock {
            guard !stopping, task == nil else { return false }
            let task = Task { [weak self] in
                guard let self else { return }
                await self.activationGate.wait()
                guard self.lock.withLock({ self.activated && !self.stopping }) else { return }
                await self.run()
            }
            self.task = task
            return true
        }
    }

    /// Commits the worker-side activation lease without releasing the
    /// suspended task.  The source commits its lifecycle epoch under its own
    /// state lock, then calls `releaseActivation()` after that edge.
    @discardableResult
    func prepareActivation() -> Bool {
        lock.withLock {
            guard !stopping else { return false }
            guard sink == nil || task != nil else { return false }
            activated = true
            return true
        }
    }

    /// Opens the worker task after the source has published its running
    /// lifecycle edge.  If a stop/fence won the intervening race, the gate is
    /// already finished by that fence and the task will observe `stopping`
    /// without invoking the sink.
    func releaseActivation() {
        let shouldRelease = lock.withLock { activated && !stopping }
        if shouldRelease { activationGate.finish() }
    }

    /// Test-only lifecycle barrier. It is installed before the worker is
    /// activated so a queued item can be held without relying on scheduler
    /// timing; production never sets this flag.
    func suspendForTesting() {
        lock.withLock {
            guard !stopping else { return }
            suspendedForTesting = true
            if suspensionGate == nil { suspensionGate = CompletionLatch() }
        }
    }

    func releaseSuspensionForTesting() {
        let gate = lock.withLock { () -> CompletionLatch? in
            suspendedForTesting = false
            let gate = suspensionGate
            suspensionGate = nil
            return gate
        }
        gate?.finish()
    }

    /// Admission is owned by the same lock as the stop/fence transition.
    /// A drain that completed conversion just before a terminal fence cannot
    /// leave a late item behind: it is rejected and zeroized once `stopping`
    /// is visible, instead of being silently retained for a future task.
    @discardableResult
    func enqueue(_ item: ProcessTapDeliveryQueue.Item) -> Bool {
        let accepted = lock.withLock { () -> Bool in
            guard !stopping else { return false }
            queue.enqueue(item)
            return true
        }
        if !accepted { ProcessTapDeliveryQueue.zeroize(item) }
        return accepted
    }

    var evidenceOverflowed: Bool { queue.evidenceOverflowed }
    var queuedItemCount: Int { lock.withLock { queue.count } }
    var beforeDeliveryAdmissionHook: (@Sendable () -> Void)? {
        get { lock.withLock { beforeDeliveryAdmissionHookForTesting } }
        set { lock.withLock { beforeDeliveryAdmissionHookForTesting = newValue } }
    }
    @discardableResult
    func discardQueuedItems() -> [DiscardedDeliveryBoundary] {
        lock.withLock { Self.boundaries(in: queue.removeAllItems()) }
    }

    /// Fences new sink invocations synchronously for a terminal failure or
    /// graph replacement.  An invocation already holding the sink remains
    /// eligible for the normal quarantine deadline; queued items are
    /// discarded immediately.
    @discardableResult
    func fenceAndDiscard() -> [DiscardedDeliveryBoundary] {
        let result = lock.withLock { () -> (Task<Void, Never>?, CompletionLatch?, [DiscardedDeliveryBoundary]) in
            stopping = true
            activated = false
            suspendedForTesting = false
            let suspensionGate = self.suspensionGate
            self.suspensionGate = nil
            let task = self.task
            self.task = nil
            activationGate.finish()
            let boundaries = Self.boundaries(in: queue.removeAllItems())
            return (task, suspensionGate, boundaries)
        }
        result.0?.cancel()
        result.1?.finish()
        return result.2
    }

    /// Delivers terminal queued-loss evidence after the worker has been
    /// fenced.  Existing in-flight work is given the same bounded deadline;
    /// an uncooperative invocation is adopted by the quarantine registry, so
    /// terminal cleanup never waits indefinitely or runs two ordered sink
    /// calls concurrently.  The gap invocations are sequential and bounded.
    func deliverTerminalGaps(
        _ gaps: [CoverageGap],
        deadlineNanoseconds: UInt64
    ) async {
        guard let sink, !gaps.isEmpty else { return }
        let start = DispatchTime.now().uptimeNanoseconds
        let deadlineResult = start.addingReportingOverflow(deadlineNanoseconds)
        let deadline = deadlineResult.overflow ? UInt64.max : deadlineResult.partialValue

        if let invocation = lock.withLock({ inFlight }) {
            let remaining = Self.remaining(until: deadline)
            if !(await invocation.wait(timeoutNanoseconds: remaining)) {
                // Keep the uncooperative owner in quarantine and stop the
                // ordered emission here.  Starting a gap invocation while
                // the frame call is still running would let terminal
                // evidence overtake the original sink owner.  The caller has
                // already retained every gap in terminalEvidenceGaps, so the
                // evidence remains available without an unsafe concurrent
                // sink call.
                registry.adopt(invocation)
                return
            }
            lock.withLock {
                if inFlight === invocation { inFlight = nil }
            }
        }

        for gap in gaps {
            let remaining = Self.remaining(until: deadline)
            guard remaining > 0 else { break }
            let invocation = ProcessTapDeliveryInvocation(item: .gap(gap), sink: sink)
            if !(await invocation.wait(timeoutNanoseconds: remaining)) {
                registry.adopt(invocation)
                break
            }
        }
    }

    fileprivate func stop(deadlineNanoseconds: UInt64) async -> StopOutcome {
        let taskAndSuspension = lock.withLock { () -> (Task<Void, Never>?, CompletionLatch?) in
            stopping = true
            activated = false
            suspendedForTesting = false
            let suspensionGate = self.suspensionGate
            self.suspensionGate = nil
            let task = self.task
            self.task = nil
            activationGate.finish()
            return (task, suspensionGate)
        }
        taskAndSuspension.0?.cancel()
        taskAndSuspension.1?.finish()
        let start = DispatchTime.now().uptimeNanoseconds
        let deadlineResult = start.addingReportingOverflow(deadlineNanoseconds)
        let deadline = deadlineResult.overflow ? UInt64.max : deadlineResult.partialValue

        if let invocation = lock.withLock({ inFlight }) {
            let remaining = Self.remaining(until: deadline)
            if !(await invocation.wait(timeoutNanoseconds: remaining)) {
                queue.removeAll()
                return .inFlight(invocation)
            }
            lock.withLock {
                if inFlight === invocation { inFlight = nil }
            }
        }

        // A normal user stop may finish already-converted items, but only
        // inside the finite deadline.  Failure/rebuild callers clear this
        // queue before reaching this path.
        while let invocation = admitNextItem(
            allowStopping: true,
            deadlineNanoseconds: deadline
        ) {
            let remaining = Self.remaining(until: deadline)
            if remaining == 0 {
                queue.removeAll()
                // The deadline may cross immediately after the lock-owned
                // dequeue/admission edge. Keep that invocation as an owned
                // in-flight result so teardown can quarantine it; returning
                // complete here would orphan a sink call and permit an unsafe
                // restart while it still owns the old graph.
                return invocation.isFinished ? .complete : .inFlight(invocation)
            }
            if !(await invocation.wait(timeoutNanoseconds: remaining)) {
                queue.removeAll()
                return .inFlight(invocation)
            }
            lock.withLock {
                if inFlight === invocation { inFlight = nil }
            }
        }
        return .complete
    }

    private static func remaining(until deadline: UInt64) -> UInt64 {
        let now = DispatchTime.now().uptimeNanoseconds
        return now >= deadline ? 0 : deadline - now
    }

    /// Dequeue and sink admission are one worker-lock-owned transition.  The
    /// optional fixture hook runs while that lock is held, allowing a test to
    /// request a concurrent fence at the exact old implementation's gap; the
    /// fence must wait until this item is either in `inFlight` or returned as
    /// a queued boundary.
    private func admitNextItem(
        allowStopping: Bool,
        deadlineNanoseconds: UInt64? = nil
    ) -> ProcessTapDeliveryInvocation? {
        lock.withLock {
            guard allowStopping || !stopping else { return nil }
            if let deadlineNanoseconds,
               Self.remaining(until: deadlineNanoseconds) == 0 {
                // Check expiry while still owning the same lock transition as
                // dequeue and inFlight publication. An expired queue item
                // therefore cannot start a new sink invocation during stop.
                return nil
            }
            guard let sink, let item = queue.dequeue() else { return nil }
            beforeDeliveryAdmissionHookForTesting?()
            let invocation = ProcessTapDeliveryInvocation(item: item, sink: sink)
            inFlight = invocation
            return invocation
        }
    }

    private static func boundaries(in items: [ProcessTapDeliveryQueue.Item]) -> [DiscardedDeliveryBoundary] {
        var result: [DiscardedDeliveryBoundary] = []
        var indexByIdentity: [SourceIdentity: Int] = [:]
        for item in items {
            let boundary: DiscardedDeliveryBoundary?
            switch item {
            case .frame(let frame):
                boundary = DiscardedDeliveryBoundary(
                    identity: frame.identity,
                    firstSequence: frame.sequence,
                    firstSample: frame.firstSample
                )
            case .gap(let gap):
                boundary = DiscardedDeliveryBoundary(
                    identity: gap.identity,
                    firstSequence: gap.firstSequence,
                    firstSample: gap.firstSample
                )
            }
            guard let boundary else { continue }
            if let index = indexByIdentity[boundary.identity] {
                let existing = result[index]
                let existingKey = (existing.firstSequence ?? UInt64.max, existing.firstSample ?? UInt64.max)
                let boundaryKey = (boundary.firstSequence ?? UInt64.max, boundary.firstSample ?? UInt64.max)
                if boundaryKey < existingKey { result[index] = boundary }
            } else {
                indexByIdentity[boundary.identity] = result.count
                result.append(boundary)
            }
        }
        return result
    }

    private func run() async {
        while !Task.isCancelled {
            if let suspensionGate = lock.withLock({ suspendedForTesting ? self.suspensionGate : nil }) {
                await suspensionGate.wait()
                continue
            }
            guard let invocation = admitNextItem(allowStopping: false) else {
                if lock.withLock({ stopping }) { return }
                try? await Task.sleep(nanoseconds: 1_000_000)
                continue
            }
            await invocation.wait()
            lock.withLock {
                if inFlight === invocation { inFlight = nil }
            }
        }
    }
}
