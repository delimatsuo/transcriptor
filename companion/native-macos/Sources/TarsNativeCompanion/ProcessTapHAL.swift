import AppKit
import CoreAudio
import Foundation
import TarsRealtimeAudioBridge

public struct ProcessTapDescription: Equatable, Sendable {
    public let processes: [UInt32]
    public let isMono: Bool
    public let isExclusive: Bool
    public let isPrivate: Bool
    public let isMuted: Bool

    public init(
        processes: [UInt32],
        isMono: Bool = true,
        isExclusive: Bool = true,
        isPrivate: Bool = true,
        isMuted: Bool = false
    ) {
        self.processes = processes
        self.isMono = isMono
        self.isExclusive = isExclusive
        self.isPrivate = isPrivate
        self.isMuted = isMuted
    }
}

public struct ProcessTapCaptureScope: Equatable, Sendable {
    public enum Kind: String, Equatable, Sendable {
        case globalExclusive
    }

    public let kind: Kind
    public let excludedProcessObjectID: UInt32

    public init(excludedProcessObjectID: UInt32) throws {
        guard excludedProcessObjectID != UInt32(kAudioObjectUnknown) else {
            throw CompanionError.invalid("o objeto Core Audio do processo do TarsCompanion é desconhecido")
        }
        self.kind = .globalExclusive
        self.excludedProcessObjectID = excludedProcessObjectID
    }

    public static func globalExclusive(excluding processObjectID: UInt32) throws -> ProcessTapCaptureScope {
        try ProcessTapCaptureScope(excludedProcessObjectID: processObjectID)
    }

    public var description: ProcessTapDescription {
        ProcessTapDescription(processes: [excludedProcessObjectID])
    }
}

public enum ProcessTapHALEvent: Equatable, Sendable {
    case sleep
    case wake
    case serviceReset
    case tapListChanged
    case deviceAlive(Bool)
    case deviceAliveReadFailed(OSStatus)
    case tapFormatChanged
}

public enum ProcessTapHALListenerKind: String, CaseIterable, Equatable, Sendable {
    case sleepWake
    case serviceReset
    case tapList
    case deviceAlive
    case tapFormat
}

public struct ProcessTapPCMDescriptor: Equatable, Sendable {
    public let sampleRate: Double
    public let channels: Int
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
        channels: Int,
        isFloat: Bool,
        isInterleaved: Bool,
        bitsPerChannel: Int,
        bytesPerFrame: Int,
        bytesPerPacket: Int? = nil,
        framesPerPacket: Int = 1,
        formatID: UInt32 = 0x6C70636D,
        formatFlags: UInt32? = nil
    ) {
        self.sampleRate = sampleRate
        self.channels = channels
        self.isFloat = isFloat
        self.isInterleaved = isInterleaved
        self.bitsPerChannel = bitsPerChannel
        self.bytesPerFrame = bytesPerFrame
        let defaultPacket = bytesPerFrame.multipliedReportingOverflow(by: max(1, framesPerPacket))
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

    public var converterFormat: ProcessTapPCMFormat {
        ProcessTapPCMFormat(
            sampleRate: sampleRate,
            channelCount: channels,
            isFloat: isFloat,
            isInterleaved: isInterleaved,
            bitsPerChannel: bitsPerChannel,
            bytesPerFrame: bytesPerFrame,
            bytesPerPacket: bytesPerPacket,
            framesPerPacket: framesPerPacket,
            formatID: formatID,
            formatFlags: formatFlagsWereExplicit ? formatFlags : nil
        )
    }
}

public enum ProcessTapHALError: Error, Equatable, Sendable, CustomStringConvertible {
    case osStatus(operation: String, status: OSStatus)
    case unknownProcessObject
    case missingTapUID

    public var description: String {
        switch self {
        case .osStatus(let operation, let status): return "\(operation) falhou (OSStatus \(status))"
        case .unknownProcessObject: return "o processo do TarsCompanion não possui objeto Core Audio"
        case .missingTapUID: return "o Process Tap não publicou um UID"
        }
    }
}

/// Production HAL boundary.  Tests inject a fake implementation here and can
/// therefore exercise every acquisition and teardown edge without touching a
/// real device, TCC or Core Audio service.
public protocol ProcessTapHALBoundary: AnyObject, Sendable {
    var currentProcessID: Int32 { get }

    func translatePIDToProcessObject(pid: Int32) throws -> UInt32
    func createProcessTap(description: ProcessTapDescription) throws -> UInt32
    func tapUID(tapID: UInt32) throws -> String
    func readTapFormat(tapID: UInt32) throws -> ProcessTapPCMDescriptor
    func createAggregate(tapID: UInt32, tapUID: String, uid: String, name: String) throws -> UInt32
    func createIOProc(aggregateID: UInt32, ringAddress: UInt64) throws -> UInt64
    func start(aggregateID: UInt32, ioProc: UInt64) throws
    func stop(aggregateID: UInt32, ioProc: UInt64) throws
    func destroyIOProc(aggregateID: UInt32, ioProc: UInt64) throws
    func detachTap(tapID: UInt32, aggregateID: UInt32) throws
    func destroyAggregate(aggregateID: UInt32) throws
    func destroyProcessTap(tapID: UInt32) throws
    func installListener(kind: ProcessTapHALListenerKind, aggregateID: UInt32?, tapID: UInt32?, handler: @escaping @Sendable (ProcessTapHALEvent) -> Void) throws -> UInt64
    func removeListener(_ token: UInt64) throws
}

/// Thin Core Audio implementation used only by the signed app.  It never
/// performs a permission preflight; the tap start result and subsequent PCM
/// probe are the only evidence available to this API.
@available(macOS 14.2, *)
public final class CoreAudioProcessTapHAL: ProcessTapHALBoundary, @unchecked Sendable {
    public let currentProcessID: Int32
    private let listenerQueue = DispatchQueue(label: "com.tars.companion.process-tap.listeners")
    private let lock = NSLock()
    private var nextToken: UInt64 = 1
    private struct Listener {
        let kind: ProcessTapHALListenerKind
        let objectID: AudioObjectID?
        let address: AudioObjectPropertyAddress?
        let queue: DispatchQueue?
        let block: AudioObjectPropertyListenerBlock?
        let notifications: [NSObjectProtocol]
    }
    private var listeners: [UInt64: Listener] = [:]

    /// Core Audio can report its permission status from more than
    /// `AudioDeviceStart` (for example during a property read or graph
    /// acquisition).  Normalize that one status at the HAL boundary so the
    /// source/controller always expose the approved denial copy, while every
    /// other status retains its operation-specific diagnostic.
    static func normalizedError(operation: String, status: OSStatus) -> Error {
        if status == kAudioDevicePermissionsError {
            return SystemAudioCaptureFailure.denied
        }
        return ProcessTapHALError.osStatus(operation: operation, status: status)
    }

    public init(currentProcessID: Int32 = Int32(getpid())) {
        self.currentProcessID = currentProcessID
    }

    /// Converts the property read result into an event without ever
    /// fabricating liveness on an OS read failure.  The raw OSStatus is kept
    /// in the failure event, including the permission status, so the source
    /// can distinguish terminal denial from recoverable route failures.
    /// The helper is internal so injected tests can prove both Bool values and
    /// the loud failure path.
    static func deviceAliveEvent(status: OSStatus, value: UInt32) -> ProcessTapHALEvent {
        guard status == noErr else { return .deviceAliveReadFailed(status) }
        return .deviceAlive(value != 0)
    }

    public func translatePIDToProcessObject(pid: Int32) throws -> UInt32 {
        var pidValue = pid_t(pid)
        var processObject = AudioObjectID(kAudioObjectUnknown)
        var dataSize = UInt32(MemoryLayout<AudioObjectID>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyTranslatePIDToProcessObject,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            UInt32(MemoryLayout<pid_t>.size),
            &pidValue,
            &dataSize,
            &processObject
        )
        guard status == noErr else { throw Self.normalizedError(operation: "translatePIDToProcessObject", status: status) }
        guard processObject != kAudioObjectUnknown else { throw ProcessTapHALError.unknownProcessObject }
        return UInt32(processObject)
    }

    public func createProcessTap(description: ProcessTapDescription) throws -> UInt32 {
        let processIDs = description.processes.map { AudioObjectID($0) }
        let tapDescription = CATapDescription(monoGlobalTapButExcludeProcesses: processIDs)
        tapDescription.isMono = description.isMono
        tapDescription.isExclusive = description.isExclusive
        tapDescription.isPrivate = description.isPrivate
        tapDescription.muteBehavior = CATapMuteBehavior(rawValue: description.isMuted ? 1 : 0)!
        var tapID = AudioObjectID(kAudioObjectUnknown)
        let status = AudioHardwareCreateProcessTap(tapDescription, &tapID)
        guard status == noErr else { throw Self.normalizedError(operation: "AudioHardwareCreateProcessTap", status: status) }
        guard tapID != kAudioObjectUnknown else { throw ProcessTapHALError.unknownProcessObject }
        return UInt32(tapID)
    }

    public func tapUID(tapID: UInt32) throws -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var uid: Unmanaged<CFString>?
        var dataSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let status = withUnsafeMutablePointer(to: &uid) { pointer in
            AudioObjectGetPropertyData(AudioObjectID(tapID), &address, 0, nil, &dataSize, pointer)
        }
        guard status == noErr else { throw Self.normalizedError(operation: "kAudioTapPropertyUID", status: status) }
        guard let uid else { throw ProcessTapHALError.missingTapUID }
        let value = uid.takeRetainedValue() as String
        return value
    }

    public func readTapFormat(tapID: UInt32) throws -> ProcessTapPCMDescriptor {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var asbd = AudioStreamBasicDescription()
        var dataSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        let status = AudioObjectGetPropertyData(AudioObjectID(tapID), &address, 0, nil, &dataSize, &asbd)
        guard status == noErr else { throw Self.normalizedError(operation: "kAudioTapPropertyFormat", status: status) }
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) == 0
        return ProcessTapPCMDescriptor(
            sampleRate: asbd.mSampleRate,
            channels: Int(asbd.mChannelsPerFrame),
            isFloat: isFloat,
            isInterleaved: isInterleaved,
            bitsPerChannel: Int(asbd.mBitsPerChannel),
            bytesPerFrame: Int(asbd.mBytesPerFrame),
            bytesPerPacket: Int(asbd.mBytesPerPacket),
            framesPerPacket: Int(asbd.mFramesPerPacket),
            formatID: asbd.mFormatID,
            formatFlags: asbd.mFormatFlags
        )
    }

    public func createAggregate(tapID: UInt32, tapUID: String, uid: String, name: String) throws -> UInt32 {
        let tapDescription: [String: Any] = ["uid": tapUID]
        let aggregateDescription: [String: Any] = [
            "uid": uid,
            "name": name,
            "private": true,
            "taps": [tapDescription]
        ]
        var aggregateID = AudioObjectID(kAudioObjectUnknown)
        let status = AudioHardwareCreateAggregateDevice(aggregateDescription as CFDictionary, &aggregateID)
        guard status == noErr else { throw Self.normalizedError(operation: "AudioHardwareCreateAggregateDevice", status: status) }
        guard aggregateID != kAudioObjectUnknown else { throw ProcessTapHALError.unknownProcessObject }
        return UInt32(aggregateID)
    }

    public func createIOProc(aggregateID: UInt32, ringAddress: UInt64) throws -> UInt64 {
        let ring = UnsafeMutableRawPointer(bitPattern: UInt(ringAddress))
        var token: UInt64 = 0
        let status = TarsRealtimeAudioCreateIOProc(AudioObjectID(aggregateID), ring, &token)
        guard status == noErr else { throw Self.normalizedError(operation: "AudioDeviceCreateIOProcID", status: status) }
        return token
    }

    public func start(aggregateID: UInt32, ioProc: UInt64) throws {
        let status = TarsRealtimeAudioStartIOProc(AudioObjectID(aggregateID), ioProc)
        guard status == noErr else {
            throw Self.normalizedError(operation: "AudioDeviceStart", status: status)
        }
    }

    public func stop(aggregateID: UInt32, ioProc: UInt64) throws {
        let status = TarsRealtimeAudioStopIOProc(AudioObjectID(aggregateID), ioProc)
        guard status == noErr else { throw Self.normalizedError(operation: "AudioDeviceStop", status: status) }
    }

    public func destroyIOProc(aggregateID: UInt32, ioProc: UInt64) throws {
        let status = TarsRealtimeAudioDestroyIOProc(AudioObjectID(aggregateID), ioProc)
        guard status == noErr else { throw Self.normalizedError(operation: "AudioDeviceDestroyIOProcID", status: status) }
    }

    public func detachTap(tapID: UInt32, aggregateID: UInt32) throws {
        // The aggregate composition owns the attachment.  There is no public
        // detach routine; destroying the aggregate removes this composition.
        _ = tapID
        _ = aggregateID
    }

    public func destroyAggregate(aggregateID: UInt32) throws {
        let status = AudioHardwareDestroyAggregateDevice(AudioObjectID(aggregateID))
        guard status == noErr else { throw Self.normalizedError(operation: "AudioHardwareDestroyAggregateDevice", status: status) }
    }

    public func destroyProcessTap(tapID: UInt32) throws {
        let status = AudioHardwareDestroyProcessTap(AudioObjectID(tapID))
        guard status == noErr else { throw Self.normalizedError(operation: "AudioHardwareDestroyProcessTap", status: status) }
    }

    public func installListener(
        kind: ProcessTapHALListenerKind,
        aggregateID: UInt32?,
        tapID: UInt32?,
        handler: @escaping @Sendable (ProcessTapHALEvent) -> Void
    ) throws -> UInt64 {
        if kind == .sleepWake {
            let center = NSWorkspace.shared.notificationCenter
            let sleepToken = center.addObserver(forName: NSWorkspace.willSleepNotification, object: nil, queue: nil) { _ in handler(.sleep) }
            let wakeToken = center.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: nil) { _ in handler(.wake) }
            let token = allocateToken()
            lock.withLock {
                // One public token owns both notification registrations, so
                // source teardown can remove every sleep/wake edge exactly
                // once without leaking the wake observer.
                listeners[token] = Listener(
                    kind: kind,
                    objectID: nil,
                    address: nil,
                    queue: nil,
                    block: nil,
                    notifications: [sleepToken, wakeToken]
                )
            }
            return token
        }

        let objectID: AudioObjectID
        let selector: AudioObjectPropertySelector
        switch kind {
        case .serviceReset:
            objectID = AudioObjectID(kAudioObjectSystemObject)
            selector = kAudioHardwarePropertyServiceRestarted
        case .tapList:
            objectID = AudioObjectID(kAudioObjectSystemObject)
            selector = kAudioHardwarePropertyTapList
        case .deviceAlive:
            guard let aggregateID else { throw ProcessTapHALError.osStatus(operation: "deviceAlive listener", status: -1) }
            objectID = AudioObjectID(aggregateID)
            selector = kAudioDevicePropertyDeviceIsAlive
        case .tapFormat:
            guard let tapID else { throw ProcessTapHALError.osStatus(operation: "tapFormat listener", status: -1) }
            objectID = AudioObjectID(tapID)
            selector = kAudioTapPropertyFormat
        case .sleepWake:
            fatalError("sleepWake handled above")
        }
        var address = AudioObjectPropertyAddress(
            mSelector: selector,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let queue = listenerQueue
        let block: AudioObjectPropertyListenerBlock = { _, _ in
            switch kind {
            case .serviceReset: handler(.serviceReset)
            case .tapList: handler(.tapListChanged)
            case .deviceAlive:
                var alive: UInt32 = 0
                var size = UInt32(MemoryLayout<UInt32>.size)
                var aliveAddress = AudioObjectPropertyAddress(
                    mSelector: kAudioDevicePropertyDeviceIsAlive,
                    mScope: kAudioObjectPropertyScopeGlobal,
                    mElement: kAudioObjectPropertyElementMain
                )
                let status = AudioObjectGetPropertyData(
                    objectID,
                    &aliveAddress,
                    0,
                    nil,
                    &size,
                    &alive
                )
                handler(Self.deviceAliveEvent(status: status, value: alive))
            case .tapFormat: handler(.tapFormatChanged)
            case .sleepWake: break
            }
        }
        let status = AudioObjectAddPropertyListenerBlock(objectID, &address, queue, block)
        guard status == noErr else { throw Self.normalizedError(operation: "AudioObjectAddPropertyListenerBlock", status: status) }
        let token = allocateToken()
        lock.withLock {
            listeners[token] = Listener(
                kind: kind,
                objectID: objectID,
                address: address,
                queue: queue,
                block: block,
                notifications: []
            )
        }
        return token
    }

    public func removeListener(_ token: UInt64) throws {
        guard let listener = lock.withLock({ listeners[token] }) else { return }
        if !listener.notifications.isEmpty {
            let center = NSWorkspace.shared.notificationCenter
            for notification in listener.notifications {
                center.removeObserver(notification)
            }
            _ = lock.withLock { listeners.removeValue(forKey: token) }
            return
        }
        guard let objectID = listener.objectID, var address = listener.address, let queue = listener.queue, let block = listener.block else { return }
        let status = AudioObjectRemovePropertyListenerBlock(objectID, &address, queue, block)
        guard status == noErr else { throw Self.normalizedError(operation: "AudioObjectRemovePropertyListenerBlock", status: status) }
        // Retain the registration until the OS confirms removal.  A failed
        // removal remains retryable and cannot be mistaken for a cleaned edge.
        _ = lock.withLock { listeners.removeValue(forKey: token) }
    }

    private func allocateToken() -> UInt64 {
        lock.withLock {
            defer { nextToken &+= 1 }
            return nextToken
        }
    }
}
