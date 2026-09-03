import Foundation

/// Public facade for the testable control core.  Protocol and value schemas
/// live in ``LiveHarnessProtocol``; this file is the intentionally removable
/// app-side control seam that owns authentication and one-session admission.
public final class LiveHarnessControl: @unchecked Sendable {
    private let core: LiveHarnessControlCore

    public init(peerPolicy: LiveHarnessPeerPolicy, launchNonce: String) throws {
        core = try LiveHarnessControlCore(peerPolicy: peerPolicy, launchNonce: launchNonce)
    }

    public func authenticateClient(_ identity: LiveHarnessPeerIdentity) throws {
        try core.authenticateClient(identity)
    }

    public func authenticateServer(euid: Int32) throws {
        try core.acceptServer(euid: euid)
    }

    public func acceptOneSession(_ command: LiveHarnessSessionCommand) throws -> LiveHarnessSessionCommand {
        try core.consume(command: command)
    }

    public var hasConsumedSession: Bool { core.hasConsumedCommand }
}

/// Owns the terminal lifecycle of one harness-mode app invocation.  The
/// control client can fail before a command is received (including malformed
/// argv, connect/accept timeout, EOF, or nonce rejection), but entering
/// harness mode still owns the app lifecycle.  Keeping the ordered actions in
/// this production seam makes that invariant executable without launching an
/// app in tests.
@MainActor
public final class LiveHarnessLifecycleFinalizer {
    private let stopController: () async -> Void
    private let clearObserver: () -> Void
    private let closeAndJoinConnection: () async -> Void
    private let terminateApplication: () -> Void
    private var didFinalize = false

    public init(
        stopController: @escaping () async -> Void,
        clearObserver: @escaping () -> Void,
        closeAndJoinConnection: @escaping () async -> Void,
        terminateApplication: @escaping () -> Void
    ) {
        self.stopController = stopController
        self.clearObserver = clearObserver
        self.closeAndJoinConnection = closeAndJoinConnection
        self.terminateApplication = terminateApplication
    }

    /// Stop the source/sink, retire callbacks, join/close the connection
    /// owner, and then complete the ``open -W`` lifecycle exactly once.
    public func finalize() async {
        guard !didFinalize else { return }
        didFinalize = true
        await stopController()
        clearObserver()
        await closeAndJoinConnection()
        terminateApplication()
    }
}

/// Coordinates the authenticated control-loss waiter with the controller's
/// potentially suspending start owner.  The waiter is launched first, so EOF,
/// duplicate/trailing input, or a writer-side close can revoke the session
/// while a source is still inside `start()`.  The stop closure is awaited on
/// the caller's actor; cancellation of the start task is only advisory because
/// a source may be blocked in a non-cooperative system call.
public final class LiveHarnessControlCoordinator: @unchecked Sendable {
    private enum Event: Sendable {
        case waiterReady
        case startEntered
        case startFinished
        case controlLost
    }

    private final class EventRelay: @unchecked Sendable {
        private let lock = NSLock()
        private var continuation: AsyncStream<Event>.Continuation?

        func install(_ continuation: AsyncStream<Event>.Continuation) {
            lock.withLock { self.continuation = continuation }
        }

        func yield(_ event: Event) {
            _ = lock.withLock { continuation?.yield(event) }
        }
    }

    /// Owns the one transition from a scheduled start task to the controller
    /// start call.  Cancellation alone is advisory: a task can be canceled
    /// while it is still waiting to be scheduled.  The coordinator therefore
    /// waits for this owner to enter, then explicitly permits or denies the
    /// call.  A pending control loss denies the owner instead of letting a
    /// late task begin after stop has completed.
    private final class StartOwnership: @unchecked Sendable {
        private let lock = NSLock()
        private let onEntered: @Sendable () -> Void
        private var decision: Bool?
        private var continuation: CheckedContinuation<Bool, Never>?

        init(onEntered: @escaping @Sendable () -> Void) {
            self.onEntered = onEntered
        }

        func waitForPermission() async -> Bool {
            await withCheckedContinuation { continuation in
                let immediateDecision = lock.withLock { () -> Bool? in
                    if let decision {
                        return decision
                    }
                    self.continuation = continuation
                    return nil
                }
                onEntered()
                if let immediateDecision {
                    continuation.resume(returning: immediateDecision)
                }
            }
        }

        func allow() {
            resolve(true)
        }

        func cancel() {
            resolve(false)
        }

        private func resolve(_ value: Bool) {
            let waiter = lock.withLock { () -> CheckedContinuation<Bool, Never>? in
                guard decision == nil else { return nil }
                decision = value
                let waiter = continuation
                continuation = nil
                return waiter
            }
            waiter?.resume(returning: value)
        }
    }

    public init() {}

    public func run(
        start: @escaping @Sendable () async -> Void,
        stop: @escaping @Sendable () async -> Void,
        waitForControlLoss: @escaping @Sendable (@escaping @Sendable () -> Void) -> Void,
        beforeStartEntry: @escaping @Sendable () async -> Void = {},
        onStartEntered: @escaping @Sendable () -> Void = {}
    ) async {
        let relay = EventRelay()
        let stream = AsyncStream<Event> { continuation in
            relay.install(continuation)
        }

        // Create the control-loss owner before the potentially suspended start
        // owner.  The connection invokes the supplied callback only after its
        // sole waiter is atomically registered at the real recv boundary.
        // Every return (EOF, duplicate bytes, or a real socket error) is a
        // loss, and no start owner is created before that rendezvous.
        let controlTask = Task.detached { @Sendable in
            waitForControlLoss {
                relay.yield(.waiterReady)
            }
            relay.yield(.controlLost)
        }

        var iterator = stream.makeAsyncIterator()
        var startTask: Task<Void, Never>?
        var startOwner: StartOwnership?
        var startEntered = false
        var pendingControlLoss = false
        while let event = await iterator.next() {
            switch event {
            case .waiterReady:
                guard startTask == nil else { continue }
                let owner = StartOwnership { @Sendable in
                    onStartEntered()
                    relay.yield(.startEntered)
                }
                startOwner = owner
                startTask = Task { @Sendable in
                    // This hook is deliberately before the ownership edge so
                    // tests can force a loss while the task is scheduled but
                    // has not yet entered its cancellation-aware boundary.
                    await beforeStartEntry()
                    let permitted = await owner.waitForPermission()
                    guard permitted, !Task.isCancelled else {
                        relay.yield(.startFinished)
                        return
                    }
                    await start()
                    relay.yield(.startFinished)
                }
            case .startFinished:
                // Keep the authenticated control waiter alive for the normal
                // running session; only its terminal loss ends this method.
                continue
            case .startEntered:
                // A loss observed before this event is pending rather than
                // complete: stop cannot be considered finished until the
                // scheduled start owner has entered and been denied/joined.
                startEntered = true
                if pendingControlLoss {
                    startOwner?.cancel()
                    startTask?.cancel()
                    await stop()
                    controlTask.cancel()
                    if let startTask {
                        await startTask.value
                    }
                    await controlTask.value
                    return
                }
                startOwner?.allow()
            case .controlLost:
                // Do not stop-and-complete until a created start task has
                // crossed its ownership boundary.  This closes the race in
                // which cancellation wins before the task is ever scheduled.
                guard startTask != nil else {
                    await stop()
                    controlTask.cancel()
                    await controlTask.value
                    return
                }
                pendingControlLoss = true
                guard startEntered else { continue }
                // Cancellation cannot force a non-cooperative source out of
                // start(), so stop is invoked immediately and awaited.  The
                // controller's attempt fence rejects any later stale return.
                startOwner?.cancel()
                startTask?.cancel()
                await stop()
                controlTask.cancel()
                // Do not return until the stale start owner has actually
                // unwound.  A non-cooperative fixture may require the test to
                // release its gate after observing stop; production sources
                // similarly retain ownership until their start call returns.
                if let startTask {
                    await startTask.value
                }
                // The waiter has already returned before yielding controlLost;
                // joining it here makes the subsequent close the sole owner
                // of descriptor retirement.
                await controlTask.value
                return
            }
        }

        // The stream cannot normally finish, but retain the fail-closed
        // behavior if its continuation is ever terminated unexpectedly.
        startTask?.cancel()
        await stop()
        controlTask.cancel()
        if let startTask {
            await startTask.value
        }
        await controlTask.value
    }
}
