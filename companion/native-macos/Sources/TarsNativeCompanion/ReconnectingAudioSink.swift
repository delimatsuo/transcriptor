import Foundation

/// One live connection to the gateway. A transport is single-use: the sink
/// asks its factory for a fresh instance every time it (re)connects, so an
/// implementation never has to model reconnection itself.
public protocol AudioStreamTransport: Sendable {
    func connect() async throws
    func send(_ data: Data) async throws
    func sendText(_ text: String) async throws
    func cancel()
}

/// Raised when a transport operation outlives its deadline. The sink handles it
/// exactly like any other transport failure — re-queue at the head, reconnect —
/// which is the whole point: a half-open socket (laptop sleep, Wi-Fi hand-off)
/// stops looking like a healthy connection that simply never finishes.
public struct AudioStreamTimeout: Error, Equatable, Sendable, CustomStringConvertible {
    public let operation: String
    public let seconds: Double

    public var description: String {
        "gateway \(operation) exceeded \(seconds)s"
    }
}

/// A `CaptureFrameSink` that survives gateway outages instead of silently
/// dying on the first send error.
///
/// Capture threads never block: `receive` encodes the frame and appends it to a
/// bounded FIFO. A single sender task owns the socket, drains that FIFO in
/// order, and reconnects behind an escalating backoff whenever a send fails —
/// so a short outage costs nothing but latency, and the buffered audio is
/// replayed in capture order once the gateway comes back.
///
/// When an outage outlasts the buffer the loss is real, and the sink reports it
/// rather than hiding it: the OLDEST frames are dropped (the newest audio is
/// the audio worth keeping) and each contiguous run of drops produces exactly
/// one `buffer_exhaustion` gap message on the wire, right after the send that
/// reopened the stream.
public final class ReconnectingAudioSink: CaptureFrameSink, @unchecked Sendable {

    /// Called on every transition between connected and disconnected — never
    /// on repeats — so the CLI can print one line per state change instead of
    /// one per failed send.
    public typealias StateChangeHandler = @Sendable (Bool) -> Void

    private enum Outbound {
        case frame(source: AudioSource, firstSample: UInt64, packet: Data)
        case text(String)
    }

    /// A contiguous run of dropped frames for one source, uninterrupted by a
    /// successful send. `firstSample` is the first dropped frame's, which is
    /// where the audible hole starts.
    private struct DropRun {
        let source: AudioSource
        let firstSample: UInt64
        var frames: Int
    }

    /// Upper bound on how long `stop()` waits for the sender task to finish its
    /// final drain. Deliberately real-clock (not the injected `sleep`): it is a
    /// safety net against a wedged socket, not part of the reconnect schedule.
    private static let stopTimeoutSeconds: Double = 2.0

    public let sessionID: String

    private let transportFactory: @Sendable () -> AudioStreamTransport
    private let bufferCapacityFrames: Int
    private let reconnectDelaysSeconds: [Double]
    private let connectTimeoutSeconds: Double
    private let sendTimeoutSeconds: Double
    private let sleepHandler: @Sendable (Double) async -> Void

    private let lock = NSLock()

    // All of the following are lock-guarded. The lock is never held across an
    // `await`: it protects bookkeeping only, never an in-flight send.
    private var queue: [Outbound] = []
    private var dropRuns: [AudioSource: DropRun] = [:]
    private var frameCounts: [AudioSource: Int] = [:]
    private var backoffIndex = 0
    private var hasAttemptedConnect = false
    private var connected = false
    private var lastNotifiedConnected: Bool?
    private var started = false
    private var stopping = false
    private var senderFinished = false
    private var senderTask: Task<Void, Never>?
    private var workWaiter: CheckedContinuation<Void, Never>?
    private var pendingWake = false
    private var exitWaiters: [CheckedContinuation<Void, Never>] = []
    private var stateChangeHandler: StateChangeHandler?

    /// Owned exclusively by the sender task after `start()`; every read and
    /// write still goes through the lock so `stop()` can tear it down.
    private var transport: AudioStreamTransport?

    /// - Parameters:
    ///   - connectTimeoutSeconds: deadline for one `connect()` attempt.
    ///   - sendTimeoutSeconds: deadline for one `send`/`sendText`. Both are
    ///     measured with the injected `sleep`, so tests drive them without a
    ///     wall clock. Pass `0` to disable a deadline.
    public init(
        sessionID: String,
        transportFactory: @escaping @Sendable () -> AudioStreamTransport,
        bufferCapacityFrames: Int = 600,
        reconnectDelaysSeconds: [Double] = [1, 2, 4, 8, 16, 30],
        connectTimeoutSeconds: Double = 5,
        sendTimeoutSeconds: Double = 5,
        sleep: @escaping @Sendable (Double) async -> Void = {
            try? await Task.sleep(nanoseconds: UInt64($0 * 1_000_000_000))
        }
    ) {
        self.sessionID = sessionID
        self.transportFactory = transportFactory
        self.bufferCapacityFrames = max(1, bufferCapacityFrames)
        self.reconnectDelaysSeconds = reconnectDelaysSeconds
        self.connectTimeoutSeconds = connectTimeoutSeconds
        self.sendTimeoutSeconds = sendTimeoutSeconds
        self.sleepHandler = sleep
    }

    // MARK: - Public surface

    public var onStateChange: StateChangeHandler? {
        get { lock.withLock { stateChangeHandler } }
        set { lock.withLock { stateChangeHandler = newValue } }
    }

    public var isConnected: Bool { lock.withLock { connected } }

    /// Frames and gap messages still waiting for the wire.
    public var bufferedItemCount: Int { lock.withLock { queue.count } }

    /// Thread-safe count of frames this sink has actually delivered to the
    /// gateway for `source`. Backs the CLI's zero-frame advisory, which needs
    /// to tell "capture started but no audio is flowing" (TCC granted, nothing
    /// playing / muted input) apart from "capture never started".
    public func framesSent(for source: AudioSource) -> Int {
        lock.withLock { frameCounts[source, default: 0] }
    }

    /// Spawns the sender task. Construction alone performs no I/O, so callers
    /// can build the sink, wire it into capture sources, and start streaming
    /// when they are ready.
    public func start() {
        let shouldStart: Bool = lock.withLock {
            guard !started, !stopping else { return false }
            started = true
            return true
        }
        guard shouldStart else { return }

        let task = Task { [self] in await runSender() }
        let cancelImmediately: Bool = lock.withLock {
            senderTask = task
            return stopping
        }
        if cancelImmediately { task.cancel() }
    }

    /// Ends the sink's life: no more work is accepted, the sender task is
    /// cancelled (which pops it straight out of any reconnect backoff), it gets
    /// a short best-effort window to flush what is already queued over a live
    /// connection, and the transport is then cancelled.
    public func stop() async {
        let alreadyStopping: Bool = lock.withLock {
            let wasStopping = stopping
            stopping = true
            return wasStopping
        }
        guard !alreadyStopping else { return }

        let task: Task<Void, Never>? = lock.withLock { senderTask }
        wakeWorkWaiter()
        task?.cancel()

        if task != nil {
            // Whichever comes first: the sender task finishing its drain, or
            // the drain budget expiring because a socket wedged mid-send.
            let watchdog = Task { [self] in
                do {
                    try await Task.sleep(nanoseconds: UInt64(Self.stopTimeoutSeconds * 1_000_000_000))
                } catch {
                    return
                }
                finishSender()
            }
            await waitForSenderExit()
            watchdog.cancel()
        }

        let liveTransport: AudioStreamTransport? = lock.withLock {
            let current = transport
            transport = nil
            connected = false
            senderTask = nil
            return current
        }
        liveTransport?.cancel()
    }

    // MARK: - CaptureFrameSink

    public func receive(_ frame: AudioFrame) async throws {
        guard let packet = Self.encodePacket(frame: frame, sessionID: sessionID) else { return }
        enqueue(.frame(source: frame.identity.source, firstSample: frame.firstSample, packet: packet))
    }

    public func receiveGap(_ gap: CoverageGap) async throws {
        guard let text = Self.encodeGapText(
            source: gap.identity.source.rawValue,
            reason: gap.reason.rawValue,
            firstSample: gap.firstSample ?? 0
        ) else { return }
        // Queued rather than sent out-of-band so a gap keeps its position
        // relative to the audio it describes.
        enqueue(.text(text))
    }

    // MARK: - Encoding

    /// 4-byte big-endian header length + header JSON + raw PCM. Byte-identical
    /// to what the gateway's native stream endpoint already parses.
    static func encodePacket(frame: AudioFrame, sessionID: String) -> Data? {
        let headerDict: [String: Any] = [
            "session_id": sessionID,
            "source": frame.identity.source.rawValue,
            "sequence": frame.sequence,
            "first_sample": frame.firstSample,
            "captured_at_ms": frame.capturedAtMs,
            "sample_rate": frame.identity.sampleRate,
            "channel_count": frame.identity.channelCount,
            "duration_ms": frame.durationMs
        ]
        guard let headerJson = try? JSONSerialization.data(withJSONObject: headerDict) else { return nil }
        var headerLength = UInt32(headerJson.count).bigEndian

        var packet = Data()
        withUnsafeBytes(of: &headerLength) { packet.append(contentsOf: $0) }
        packet.append(headerJson)
        // The payload is copied here, at enqueue time, because custody may
        // zeroize the frame's buffer long before the sender drains the queue.
        packet.append(frame.payload.copyData())
        return packet
    }

    static func encodeGapText(source: String, reason: String, firstSample: UInt64) -> String? {
        let gapDict: [String: Any] = [
            "type": "gap",
            "source": source,
            "reason": reason,
            "first_sample": firstSample
        ]
        guard let json = try? JSONSerialization.data(withJSONObject: gapDict) else { return nil }
        return String(data: json, encoding: .utf8)
    }

    // MARK: - Queue

    private func enqueue(_ item: Outbound) {
        lock.lock()
        guard !stopping else {
            lock.unlock()
            return
        }
        queue.append(item)
        trimToCapacityLocked()
        let waiter = takeWorkWaiterLocked()
        lock.unlock()
        waiter?.resume()
    }

    /// Drops the oldest queued frame until the buffer is back within capacity.
    /// Gap messages are kept in preference to audio: they are tiny, and
    /// dropping the report of a loss to make room is exactly the dishonesty
    /// this sink exists to avoid.
    private func trimToCapacityLocked() {
        while queue.count > bufferCapacityFrames {
            let frameIndex = queue.firstIndex { item in
                if case .frame = item { return true }
                return false
            }
            guard let index = frameIndex else {
                queue.removeFirst()
                continue
            }
            let dropped = queue.remove(at: index)
            guard case .frame(let source, let firstSample, _) = dropped else { continue }
            if var run = dropRuns[source] {
                run.frames += 1
                dropRuns[source] = run
            } else {
                dropRuns[source] = DropRun(source: source, firstSample: firstSample, frames: 1)
            }
        }
    }

    private func dequeue() -> Outbound? {
        lock.withLock { queue.isEmpty ? nil : queue.removeFirst() }
    }

    /// Puts a frame back where it came from after a failed send. If producers
    /// filled the buffer while the send was in flight, this re-queued frame is
    /// once again the oldest — so it is the one dropped, and the loss is
    /// recorded honestly instead of being silently swallowed.
    private func requeueAtHead(_ item: Outbound) {
        lock.lock()
        queue.insert(item, at: 0)
        trimToCapacityLocked()
        lock.unlock()
    }

    private func takeDropRuns() -> [DropRun] {
        lock.withLock {
            let runs = dropRuns.values.sorted { $0.source.rawValue < $1.source.rawValue }
            dropRuns.removeAll()
            return runs
        }
    }

    /// Returns unreported runs to the pending set after a failed gap send, so a
    /// drop-run is reported once — not zero times, not twice.
    private func restoreDropRuns(_ runs: [DropRun]) {
        lock.withLock {
            for run in runs {
                if let existing = dropRuns[run.source] {
                    dropRuns[run.source] = DropRun(
                        source: run.source,
                        firstSample: min(existing.firstSample, run.firstSample),
                        frames: existing.frames + run.frames
                    )
                } else {
                    dropRuns[run.source] = run
                }
            }
        }
    }

    // MARK: - Sender task

    private func runSender() async {
        await withTaskCancellationHandler {
            await senderLoop()
        } onCancel: {
            // Cancellation has to pop the sender out of `waitForWork`, which
            // is otherwise only woken by new work.
            self.wakeWorkWaiter()
        }
        await finalDrain()
        finishSender()
    }

    private func senderLoop() async {
        while !isStopping(), !Task.isCancelled {
            guard let transport = currentTransport() else {
                await connectOnce()
                continue
            }
            guard let item = dequeue() else {
                await waitForWork()
                continue
            }
            do {
                try await deliver(item, over: transport)
            } catch {
                requeueAtHead(item)
                dropConnection(transport)
                continue
            }
            noteDelivered(item)
            await flushDropRuns(over: transport)
        }
    }

    private func deliver(_ item: Outbound, over transport: AudioStreamTransport) async throws {
        try await withDeadline(sendTimeoutSeconds, operation: "send", transport: transport) {
            switch item {
            case .frame(_, _, let packet):
                try await transport.send(packet)
            case .text(let text):
                try await transport.sendText(text)
            }
        }
    }

    /// Runs `body` against a deadline measured with the injected `sleep`.
    ///
    /// On expiry the transport is cancelled — which is what unblocks a wedged
    /// socket, since URLSession completes a cancelled task's pending handlers
    /// with an error — and an `AudioStreamTimeout` is thrown so the caller's
    /// normal failure path (re-queue at the head, reconnect) runs.
    ///
    /// Deliberately *not* a `withThrowingTaskGroup`: a group awaits all of its
    /// children at scope exit, and the child here is parked in a continuation
    /// that cancellation alone cannot interrupt. A wedged socket would
    /// therefore hang the group — precisely the failure this deadline exists to
    /// prevent. The losing arm is cancelled and abandoned instead; it unblocks
    /// on the transport cancellation moments later. A late success cannot
    /// resurrect the operation: the outcome box resolves exactly once, so the
    /// frame stays re-queued and is never counted as delivered twice.
    private func withDeadline(
        _ seconds: Double,
        operation: String,
        transport: AudioStreamTransport,
        body: @escaping @Sendable () async throws -> Void
    ) async throws {
        guard seconds > 0 else {
            try await body()
            return
        }
        let outcome = FirstOutcomeBox()
        let work = Task {
            do {
                try await body()
                outcome.finish(.success(()))
            } catch {
                outcome.finish(.failure(error))
            }
        }
        let deadline = Task { [sleepHandler] in
            await sleepHandler(seconds)
            guard !Task.isCancelled else { return }
            // Only the arm that actually won may tear the socket down: an
            // operation that succeeded microseconds before the deadline elapsed
            // must not have its healthy connection cancelled out from under it.
            guard outcome.finish(.failure(AudioStreamTimeout(operation: operation, seconds: seconds))) else { return }
            transport.cancel()
        }
        let result = await outcome.value()
        deadline.cancel()
        work.cancel()
        try result.get()
    }

    private func noteDelivered(_ item: Outbound) {
        lock.withLock {
            // A delivered byte is the only proof the gateway is really back, so
            // it — not a bare successful connect — is what resets the backoff.
            // A gateway that accepts connections and then drops every send
            // therefore still escalates instead of hammering at one second.
            backoffIndex = 0
            guard case .frame(let source, _, _) = item else { return }
            frameCounts[source, default: 0] += 1
        }
    }

    /// Emits one gap message per completed drop-run, immediately after the send
    /// that proved the stream is usable again.
    private func flushDropRuns(over transport: AudioStreamTransport) async {
        var remaining = takeDropRuns()
        while let run = remaining.first {
            guard let text = Self.encodeGapText(
                source: run.source.rawValue,
                reason: "buffer_exhaustion",
                firstSample: run.firstSample
            ) else {
                remaining.removeFirst()
                continue
            }
            do {
                try await deliver(.text(text), over: transport)
            } catch {
                restoreDropRuns(remaining)
                dropConnection(transport)
                return
            }
            remaining.removeFirst()
        }
    }

    private func connectOnce() async {
        if let delay = nextBackoffDelay() {
            await sleepHandler(delay)
        }
        // Keeps the reconnect loop cooperative even when the injected sleep
        // returns instantly (tests) and makes cancellation land promptly.
        await Task.yield()
        guard !isStopping(), !Task.isCancelled else { return }

        let candidate = transportFactory()
        do {
            try await withDeadline(connectTimeoutSeconds, operation: "connect", transport: candidate) {
                try await candidate.connect()
            }
        } catch {
            candidate.cancel()
            notifyStateChange(false)
            return
        }

        let accepted: Bool = lock.withLock {
            guard !stopping else { return false }
            transport = candidate
            connected = true
            return true
        }
        guard accepted else {
            candidate.cancel()
            return
        }
        notifyStateChange(true)
    }

    private func dropConnection(_ failed: AudioStreamTransport) {
        lock.withLock {
            transport = nil
            connected = false
        }
        failed.cancel()
        notifyStateChange(false)
    }

    /// The first attempt is immediate — a companion should not idle for a
    /// second before its very first connect. Every later attempt walks the
    /// delay list and clamps at the last entry.
    private func nextBackoffDelay() -> Double? {
        lock.withLock {
            guard hasAttemptedConnect else {
                hasAttemptedConnect = true
                return nil
            }
            guard !reconnectDelaysSeconds.isEmpty else { return nil }
            let delay = reconnectDelaysSeconds[min(backoffIndex, reconnectDelaysSeconds.count - 1)]
            backoffIndex = min(backoffIndex + 1, reconnectDelaysSeconds.count - 1)
            return delay
        }
    }

    /// Best-effort flush on shutdown: whatever is already queued goes out over
    /// the live connection until the queue empties, a send fails, or the drain
    /// budget expires. No reconnects here — `stop()` means stop.
    private func finalDrain() async {
        guard let transport = currentTransport() else { return }
        let deadline = Date().addingTimeInterval(Self.stopTimeoutSeconds)
        while Date() < deadline, let item = dequeue() {
            do {
                try await deliver(item, over: transport)
            } catch {
                requeueAtHead(item)
                return
            }
            noteDelivered(item)
        }
    }

    // MARK: - Lock-guarded helpers

    private func isStopping() -> Bool { lock.withLock { stopping } }

    private func currentTransport() -> AudioStreamTransport? { lock.withLock { transport } }

    private func notifyStateChange(_ isConnected: Bool) {
        let handler: StateChangeHandler? = lock.withLock {
            guard lastNotifiedConnected != isConnected else { return nil }
            lastNotifiedConnected = isConnected
            return stateChangeHandler
        }
        // Never called under the lock: handlers print, and a caller-supplied
        // closure must not be able to deadlock the sink.
        handler?(isConnected)
    }

    private func takeWorkWaiterLocked() -> CheckedContinuation<Void, Never>? {
        let waiter = workWaiter
        workWaiter = nil
        if waiter == nil { pendingWake = true }
        return waiter
    }

    /// Suspends the sender until there is work, or until `stop()`/cancellation
    /// wakes it. `pendingWake` closes the race where a wake arrives between the
    /// emptiness check and the continuation being parked.
    private func waitForWork() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            lock.lock()
            if pendingWake || stopping || !queue.isEmpty {
                pendingWake = false
                lock.unlock()
                continuation.resume()
                return
            }
            workWaiter = continuation
            lock.unlock()
        }
    }

    private func wakeWorkWaiter() {
        lock.lock()
        let waiter = takeWorkWaiterLocked()
        lock.unlock()
        waiter?.resume()
    }

    private func waitForSenderExit() async {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
            lock.lock()
            if senderFinished {
                lock.unlock()
                continuation.resume()
                return
            }
            exitWaiters.append(continuation)
            lock.unlock()
        }
    }

    private func finishSender() {
        lock.lock()
        senderFinished = true
        let waiters = exitWaiters
        exitWaiters.removeAll()
        lock.unlock()
        waiters.forEach { $0.resume() }
    }
}

/// Resolves exactly once — whichever racing arm finishes first wins, and every
/// later arm is a no-op. This is what makes a late send-completion harmless:
/// it cannot overturn a timeout that has already been reported, so the frame is
/// neither counted twice nor un-re-queued.
private final class FirstOutcomeBox: @unchecked Sendable {
    private let lock = NSLock()
    private var outcome: Result<Void, Error>?
    private var waiter: CheckedContinuation<Result<Void, Error>, Never>?

    /// - Returns: `true` if this call is the one that resolved the box.
    @discardableResult
    func finish(_ result: Result<Void, Error>) -> Bool {
        lock.lock()
        guard outcome == nil else {
            lock.unlock()
            return false
        }
        outcome = result
        let pending = waiter
        waiter = nil
        lock.unlock()
        pending?.resume(returning: result)
        return true
    }

    func value() async -> Result<Void, Error> {
        await withCheckedContinuation { (continuation: CheckedContinuation<Result<Void, Error>, Never>) in
            lock.lock()
            if let outcome {
                lock.unlock()
                continuation.resume(returning: outcome)
                return
            }
            waiter = continuation
            lock.unlock()
        }
    }
}
