import CryptoKit
import Foundation
#if canImport(Darwin)
import Darwin
#endif

/// The Python harness and the Swift client deliberately share this exact
/// byte fixture.  The Python policy module reads the literal from this file
/// instead of maintaining a second expectation that could drift.  Keep this
/// payload ASCII and in the canonical key order emitted by
/// ``liveHarnessJSONData`` below.
public enum LiveHarnessCanonicalFixtures {
    public static let sessionCommandPayload = Data(#"{"gateway":"ws://127.0.0.1","launch_nonce":"golden-nonce","session_id":"golden-session","stream_key":"TASK11-GOLDEN-STREAM-KEY-0123456789abcdefgh","type":"session","version":2}"#.utf8)
}

private func liveHarnessJSONData(_ object: Any) throws -> Data {
    // Foundation escapes '/' by default on some macOS releases.  Python's
    // json.dumps does not, so this option is part of the cross-language wire
    // contract rather than an implementation detail.
    try JSONSerialization.data(
        withJSONObject: object,
        options: [.sortedKeys, .withoutEscapingSlashes]
    )
}

/// The local control protocol is deliberately small.  The stream key only
/// exists in a decoded session command; no event or evidence type has a field
/// in which it could be copied accidentally.
public enum LiveHarnessProtocolError: Error, Equatable, Sendable, CustomStringConvertible {
    case invalidFrame(String)
    case invalidMessage(String)
    case unsupportedVersion(UInt64)
    case duplicateCommand
    case duplicateWaiter
    case incompleteFrame
    case peerRejected(String)
    case timeout
    case controlLost

    public var description: String {
        switch self {
        case .invalidFrame(let message): return "invalid live-harness frame: \(message)"
        case .invalidMessage(let message): return "invalid live-harness message: \(message)"
        case .unsupportedVersion(let version): return "unsupported live-harness version: \(version)"
        case .duplicateCommand: return "duplicate live-harness command"
        case .duplicateWaiter: return "duplicate live-harness control waiter"
        case .incompleteFrame: return "incomplete live-harness frame"
        case .peerRejected(let message): return "live-harness peer rejected: \(message)"
        case .timeout: return "live-harness control timeout"
        case .controlLost: return "live-harness control connection lost"
        }
    }
}

/// Canonical websocket gateway-base policy shared with the offline Python
/// harness.  The gateway is deliberately keyless: credentials are carried
/// only by the separately supplied WebSocket subprotocol and can never be
/// interpolated into this URL.
public enum LiveHarnessGatewayBase {
    public static let maximumLength = 2_048
    public static let maximumPathLength = 1_024

    private static func isASCIIAlphaNumeric(_ scalar: UnicodeScalar) -> Bool {
        (scalar.value >= 48 && scalar.value <= 57)
            || (scalar.value >= 65 && scalar.value <= 90)
            || (scalar.value >= 97 && scalar.value <= 122)
    }

    private static func validHostLabel(_ label: String) -> Bool {
        let scalars = Array(label.unicodeScalars)
        guard !scalars.isEmpty, scalars.count <= 63,
              isASCIIAlphaNumeric(scalars[0]), isASCIIAlphaNumeric(scalars[scalars.count - 1]) else {
            return false
        }
        return scalars.allSatisfy { isASCIIAlphaNumeric($0) || $0 == "-" }
    }

    private static func validPort(_ port: String) -> Bool {
        guard !port.isEmpty, port.allSatisfy({ $0.isNumber && $0.isASCII }),
              let number = Int(port) else { return false }
        return (1...65_535).contains(number)
    }

    /// Validate one absolute, canonical ``ws``/``wss`` URL base without any
    /// credential material.  Percent encoding is rejected in its entirety so
    /// no later URL decoder can change the authority or path semantics.
    @discardableResult
    public static func validate(_ value: String) throws -> String {
        guard !value.isEmpty, value.utf8.count <= maximumLength else {
            throw LiveHarnessProtocolError.invalidMessage("gateway base is outside bounds")
        }
        guard value.unicodeScalars.allSatisfy({ $0.value >= 0x21 && $0.value <= 0x7E }),
              !value.contains("\\"), !value.contains("%"),
              !value.contains("?"), !value.contains("#") else {
            throw LiveHarnessProtocolError.invalidMessage("gateway base contains ambiguous characters")
        }
        let schemeLength: Int
        if value.hasPrefix("ws://") { schemeLength = 5 }
        else if value.hasPrefix("wss://") { schemeLength = 6 }
        else { throw LiveHarnessProtocolError.invalidMessage("gateway base scheme is invalid") }
        let afterScheme = value.index(value.startIndex, offsetBy: schemeLength)
        let remainder = String(value[afterScheme...])
        guard !remainder.isEmpty else {
            throw LiveHarnessProtocolError.invalidMessage("gateway base host is missing")
        }
        let components = remainder.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: false)
        let authority = String(components[0])
        let path = components.count == 2 ? "/\(components[1])" : nil
        guard !authority.isEmpty, !authority.contains("@") else {
            throw LiveHarnessProtocolError.invalidMessage("gateway base userinfo is forbidden")
        }

        if authority.hasPrefix("[") {
            guard let close = authority.firstIndex(of: "]"), close > authority.startIndex else {
                throw LiveHarnessProtocolError.invalidMessage("gateway base IPv6 host is invalid")
            }
            let host = String(authority[authority.index(after: authority.startIndex)..<close])
            guard host.contains(":"), host.unicodeScalars.allSatisfy({
                ($0.value >= 48 && $0.value <= 57)
                    || ($0.value >= 65 && $0.value <= 70)
                    || ($0.value >= 97 && $0.value <= 102)
                    || $0 == ":"
            }) else {
                throw LiveHarnessProtocolError.invalidMessage("gateway base IPv6 host is invalid")
            }
            let suffix = String(authority[authority.index(after: close)...])
            if !suffix.isEmpty {
                guard suffix.first == ":", validPort(String(suffix.dropFirst())) else {
                    throw LiveHarnessProtocolError.invalidMessage("gateway base port is invalid")
                }
            }
        } else {
            guard !authority.contains("[") && !authority.contains("]"), authority.filter({ $0 == ":" }).count <= 1 else {
                throw LiveHarnessProtocolError.invalidMessage("gateway base authority is invalid")
            }
            let authorityParts = authority.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
            let host = String(authorityParts[0])
            guard !host.isEmpty, host == host.lowercased(), host.utf8.count <= 253,
                  host.split(separator: ".", omittingEmptySubsequences: false).allSatisfy({ validHostLabel(String($0)) }) else {
                throw LiveHarnessProtocolError.invalidMessage("gateway base host is invalid")
            }
            if authorityParts.count == 2, !validPort(String(authorityParts[1])) {
                throw LiveHarnessProtocolError.invalidMessage("gateway base port is invalid")
            }
        }
        if let path {
            let pathBytes = Set("/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~!$&'()*+,;=:@-".utf8)
            guard path.utf8.count <= maximumPathLength,
                  path.utf8.allSatisfy({ pathBytes.contains($0) }),
                  !path.contains("//"), path != "/", !path.hasSuffix("/") else {
                throw LiveHarnessProtocolError.invalidMessage("gateway base path is invalid")
            }
        }
        return value
    }

    /// Validate a gateway while proving the separately carried stream key is
    /// absent from every URL spelling admitted by the grammar.
    @discardableResult
    public static func validateForSession(_ value: String, streamKey: String) throws -> String {
        try validate(value)
        guard validStreamKey(streamKey), !value.contains(streamKey) else {
            throw LiveHarnessProtocolError.invalidMessage("stream key material entered gateway base")
        }
        // ``validate`` rejects percent signs, so percent-decoded key material
        // has no alternate spelling at this boundary.
        guard !value.contains("%") else {
            throw LiveHarnessProtocolError.invalidMessage("encoded stream key material entered gateway base")
        }
        return value
    }
}

public enum LiveHarnessMessageType: String, Codable, Sendable {
    case session
}

public struct LiveHarnessSessionCommand: Equatable, Sendable {
    public static let protocolVersion: UInt64 = 2
    public let version: UInt64
    public let type: LiveHarnessMessageType
    public let sessionID: String
    public let streamKey: String
    public let gateway: String
    public let launchNonce: String

    public init(
        version: UInt64 = Self.protocolVersion,
        type: LiveHarnessMessageType = .session,
        sessionID: String,
        streamKey: String,
        gateway: String,
        launchNonce: String
    ) throws {
        guard version == Self.protocolVersion else { throw LiveHarnessProtocolError.unsupportedVersion(version) }
        guard type == .session else { throw LiveHarnessProtocolError.invalidMessage("unsupported message type") }
        guard validIdentifier(sessionID), validIdentifier(launchNonce), validStreamKey(streamKey) else {
            throw LiveHarnessProtocolError.invalidMessage("session command value is outside bounds")
        }
        guard !sessionID.contains(streamKey), !launchNonce.contains(streamKey) else {
            throw LiveHarnessProtocolError.invalidMessage("stream key material entered session command identity")
        }
        try LiveHarnessGatewayBase.validateForSession(gateway, streamKey: streamKey)
        self.version = version
        self.type = type
        self.sessionID = sessionID
        self.streamKey = streamKey
        self.gateway = gateway
        self.launchNonce = launchNonce
    }

    /// Canonical JSON is constructed explicitly to avoid Codable's freedom to
    /// add fields or change key order.  Sorting keys makes the wire bytes
    /// stable for offline fixtures and makes duplicate/trailing detection
    /// observable at the decoder boundary.
    public func canonicalPayload() -> Data {
        let object: [String: Any] = [
            "gateway": gateway,
            "launch_nonce": launchNonce,
            "session_id": sessionID,
            "stream_key": streamKey,
            "type": type.rawValue,
            "version": version
        ]
        return (try? liveHarnessJSONData(object)) ?? Data()
    }

    public func framed() throws -> Data { try LiveHarnessFrameCodec.frame(payload: canonicalPayload()) }

    public static func decode(canonicalPayload payload: Data) throws -> LiveHarnessSessionCommand {
        let object = try LiveHarnessFrameCodec.decodeCanonicalObject(payload)
        let allowed: Set<String> = ["gateway", "launch_nonce", "session_id", "stream_key", "type", "version"]
        guard Set(object.keys) == allowed else {
            let unknown = Set(object.keys).subtracting(allowed)
            let missing = allowed.subtracting(object.keys)
            throw LiveHarnessProtocolError.invalidMessage(
                "field allowlist violation unknown=\(unknown.sorted()) missing=\(missing.sorted())"
            )
        }
        guard let versionNumber = object["version"] as? NSNumber,
              let version = exactUnsignedIntegerField("version", in: payload),
              let typeRaw = object["type"] as? String,
              let type = LiveHarnessMessageType(rawValue: typeRaw),
              let sessionID = object["session_id"] as? String,
              let streamKey = object["stream_key"] as? String,
              let gateway = object["gateway"] as? String,
              let launchNonce = object["launch_nonce"] as? String else {
            throw LiveHarnessProtocolError.invalidMessage("field type violation")
        }
        if CFGetTypeID(versionNumber) == CFBooleanGetTypeID() {
            throw LiveHarnessProtocolError.invalidMessage("version must be an unsigned integer")
        }
        guard version == Self.protocolVersion else {
            throw LiveHarnessProtocolError.invalidMessage("version must be an unsigned integer")
        }
        return try LiveHarnessSessionCommand(
            version: version,
            type: type,
            sessionID: sessionID,
            streamKey: streamKey,
            gateway: gateway,
            launchNonce: launchNonce
        )
    }
}

/// Non-secret, domain-separated references used by the stop handshake.  The
/// wire never carries the raw session ID, launch nonce, or stream key.
public enum LiveHarnessControlBinding {
    private static let sessionLabel = Data("tars-live-harness/session-binding/v2".utf8)
    private static let shutdownLabel = Data("tars-live-harness/shutdown-binding/v1".utf8)

    public static func sessionBinding(sessionID: String, launchNonce: String) -> String {
        "sb1_" + digest(label: sessionLabel, values: [sessionID, launchNonce])
    }

    public static func shutdownBinding(sessionBinding: String, shutdownNonce: String) -> String {
        "sd1_" + digest(label: shutdownLabel, values: [sessionBinding, shutdownNonce])
    }

    private static func digest(label: Data, values: [String]) -> String {
        var data = label
        for value in values {
            let bytes = Data(value.utf8)
            var length = UInt32(bytes.count).bigEndian
            withUnsafeBytes(of: &length) { data.append(contentsOf: $0) }
            data.append(bytes)
        }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

public struct LiveHarnessShutdownRequest: Equatable, Sendable {
    public static let protocolVersion: UInt64 = 2
    public let version: UInt64
    public let type: String
    public let shutdownNonce: String
    public let shutdownBinding: String

    public init(
        version: UInt64 = Self.protocolVersion,
        shutdownNonce: String,
        shutdownBinding: String
    ) throws {
        guard version == Self.protocolVersion,
              shutdownNonce.range(of: #"^sn1_[0-9a-f]{32}$"#, options: .regularExpression) != nil,
              shutdownBinding.range(of: #"^sd1_[0-9a-f]{64}$"#, options: .regularExpression) != nil else {
            throw LiveHarnessProtocolError.invalidMessage("shutdown request fields are outside bounds")
        }
        self.version = version
        self.type = "shutdown"
        self.shutdownNonce = shutdownNonce
        self.shutdownBinding = shutdownBinding
    }

    public func canonicalPayload() -> Data {
        let object: [String: Any] = [
            "shutdown_binding": shutdownBinding,
            "shutdown_nonce": shutdownNonce,
            "type": type,
            "version": version
        ]
        return (try? liveHarnessJSONData(object)) ?? Data()
    }

    public func framed() throws -> Data { try LiveHarnessFrameCodec.frame(payload: canonicalPayload()) }

    public static func decode(canonicalPayload payload: Data) throws -> LiveHarnessShutdownRequest {
        let object = try LiveHarnessFrameCodec.decodeCanonicalObject(payload)
        let allowed: Set<String> = ["shutdown_binding", "shutdown_nonce", "type", "version"]
        guard Set(object.keys) == allowed,
              let versionNumber = object["version"] as? NSNumber,
              CFGetTypeID(versionNumber) != CFBooleanGetTypeID(),
              let version = exactUnsignedIntegerField("version", in: payload),
              let shutdownNonce = object["shutdown_nonce"] as? String,
              let shutdownBinding = object["shutdown_binding"] as? String,
              object["type"] as? String == "shutdown" else {
            throw LiveHarnessProtocolError.invalidMessage("shutdown request field type violation")
        }
        return try Self(version: version, shutdownNonce: shutdownNonce, shutdownBinding: shutdownBinding)
    }
}

public struct LiveHarnessShutdownAcknowledgement: Equatable, Sendable {
    public static let protocolVersion: UInt64 = 2
    public let version: UInt64
    public let type: String
    public let status: String
    public let shutdownNonce: String
    public let shutdownBinding: String

    public init(
        version: UInt64 = Self.protocolVersion,
        shutdownNonce: String,
        shutdownBinding: String,
        status: String = "stopped"
    ) throws {
        guard version == Self.protocolVersion, status == "stopped",
              shutdownNonce.range(of: #"^sn1_[0-9a-f]{32}$"#, options: .regularExpression) != nil,
              shutdownBinding.range(of: #"^sd1_[0-9a-f]{64}$"#, options: .regularExpression) != nil else {
            throw LiveHarnessProtocolError.invalidMessage("shutdown acknowledgement fields are outside bounds")
        }
        self.version = version
        self.type = "shutdown_ack"
        self.status = status
        self.shutdownNonce = shutdownNonce
        self.shutdownBinding = shutdownBinding
    }

    public func canonicalPayload() -> Data {
        let object: [String: Any] = [
            "shutdown_binding": shutdownBinding,
            "shutdown_nonce": shutdownNonce,
            "status": status,
            "type": type,
            "version": version
        ]
        return (try? liveHarnessJSONData(object)) ?? Data()
    }

    public func framed() throws -> Data { try LiveHarnessFrameCodec.frame(payload: canonicalPayload()) }

    public static func decode(canonicalPayload payload: Data) throws -> LiveHarnessShutdownAcknowledgement {
        let object = try LiveHarnessFrameCodec.decodeCanonicalObject(payload)
        let allowed: Set<String> = ["shutdown_binding", "shutdown_nonce", "status", "type", "version"]
        guard Set(object.keys) == allowed,
              let versionNumber = object["version"] as? NSNumber,
              CFGetTypeID(versionNumber) != CFBooleanGetTypeID(),
              let version = exactUnsignedIntegerField("version", in: payload),
              let shutdownNonce = object["shutdown_nonce"] as? String,
              let shutdownBinding = object["shutdown_binding"] as? String,
              object["type"] as? String == "shutdown_ack",
              object["status"] as? String == "stopped" else {
            throw LiveHarnessProtocolError.invalidMessage("shutdown acknowledgement field type violation")
        }
        return try Self(version: version, shutdownNonce: shutdownNonce, shutdownBinding: shutdownBinding)
    }
}

public enum LiveHarnessEventKind: String, Codable, Sendable {
    case activation
    case health
}

/// Closed failure vocabulary for the health wire schema.  Local capture
/// implementations may have a useful diagnostic, but that diagnostic is not
/// a protocol value and is never retained by a harness event.
public enum LiveHarnessFailureCode: String, Codable, Sendable {
    case permissionDenied = "permission-denied"
    case captureFailed = "capture-failed"
}

/// Event-owned health facts.  This is deliberately distinct from
/// ``SourceHealth``: the source type carries a free-form device identity, but
/// an event retains only the closed engine-bound status fields below.
public struct LiveHarnessEventHealth: Equatable, Sendable {
    public let permission: PermissionState
    public let route: RouteState
    public let interruption: InterruptionState
    public let sleep: SleepState
    public let overflowed: Bool

    private init(_ health: SourceHealth, actualEngine: ResolvedSystemAudioEngine) throws {
        let expected = actualEngine == .processTap ? "ProcessTap.SystemAudio" : "ScreenCaptureKit.SystemAudio"
        guard health.deviceIdentity == expected else {
            throw LiveHarnessProtocolError.invalidMessage("health device identity does not match actual engine")
        }
        self.permission = health.permission
        self.route = health.route
        self.interruption = health.interruption
        self.sleep = health.sleep
        self.overflowed = health.overflowed
    }

    fileprivate static func from(_ health: SourceHealth, actualEngine: ResolvedSystemAudioEngine) throws -> Self {
        try Self(health, actualEngine: actualEngine)
    }
}

/// Event-owned health status.  Unlike ``CaptureSourceStatus``, its failed
/// case has no associated free text and therefore cannot retain a local
/// source diagnostic.  Non-failed cases retain only ``LiveHarnessEventHealth``
/// and never the raw ``SourceHealth.deviceIdentity``.
public enum LiveHarnessEventStatus: Equatable, Sendable {
    case idle
    case ready(LiveHarnessEventHealth)
    case running(LiveHarnessEventHealth)
    case stopped(LiveHarnessEventHealth)
    case failed(permission: PermissionState, code: LiveHarnessFailureCode)
}

/// Harness events are safe to retain: they contain no stream key and carry
/// only the source/token fence needed to bind an observation to one graph.
public struct LiveHarnessEvent: Equatable, Sendable {
    public let kind: LiveHarnessEventKind
    public let attemptID: String
    public let launchNonce: String
    public let sessionBinding: String
    public let generation: UInt64
    public let requestedEngine: ResolvedSystemAudioEngine
    public let resolvedEngine: ResolvedSystemAudioEngine
    public let actualEngine: ResolvedSystemAudioEngine
    public let sourceBinding: String
    public let observerBinding: String
    public let status: LiveHarnessEventStatus?
    public let failureCode: LiveHarnessFailureCode?

    /// Raw producer identities are accepted only ephemerally and converted to
    /// role-bound references before the event is retained.
    internal init(
        kind: LiveHarnessEventKind,
        attemptID: String,
        launchNonce: String,
        sessionID: String,
        generation: UInt64,
        requestedEngine: ResolvedSystemAudioEngine,
        resolvedEngine: ResolvedSystemAudioEngine,
        actualEngine: ResolvedSystemAudioEngine,
        sourceObjectID: String,
        observerTokenID: String,
        status: CaptureSourceStatus? = nil,
        failedPermission: PermissionState? = nil,
        failureCode: LiveHarnessFailureCode? = nil,
        activeStreamKey: String
    ) throws {
        guard validStreamKey(activeStreamKey) else {
            throw LiveHarnessProtocolError.invalidMessage("event encoder requires the exact active stream key")
        }
        guard UUID(uuidString: attemptID) != nil,
              validIdentifier(launchNonce),
              validIdentifier(sessionID),
              sourceObjectID.range(of: #"^ObjectIdentifier\(0x[0-9A-Fa-f]{1,32}\)$"#, options: .regularExpression) != nil,
              UUID(uuidString: observerTokenID) != nil,
              generation > 0,
              requestedEngine == .processTap || requestedEngine == .screenCaptureKit,
              resolvedEngine == .processTap || resolvedEngine == .screenCaptureKit,
              actualEngine == .processTap || actualEngine == .screenCaptureKit else {
            throw LiveHarnessProtocolError.invalidMessage("event identity or generation is outside bounds")
        }

        // Derive every retained value before touching ``self``.  The active
        // stream key is scanned across both raw inputs and the complete
        // derived projection, including the closed health/failure object.
        let derivedAttemptID = "at1_" + Self.binding128(
            label: "tars-live-harness/attempt-binding/v2",
            value: attemptID.lowercased()
        )
        let derivedLaunchNonce = Self.launchBinding(for: launchNonce)
        let derivedSessionBinding = try Self.sessionBinding(for: sessionID, launchNonce: launchNonce)
        let derivedSourceBinding = "so1_" + Self.binding128(
            label: "tars-live-harness/source-binding/v2",
            value: sourceObjectID
        )
        let derivedObserverBinding = "ob1_" + Self.binding128(
            label: "tars-live-harness/observer-binding/v2",
            value: observerTokenID.lowercased()
        )
        let derivedStatus: LiveHarnessEventStatus?
        let derivedFailureCode: LiveHarnessFailureCode?
        if case .failed? = status {
            guard kind == .health else {
                throw LiveHarnessProtocolError.invalidMessage("activation event cannot be failed health")
            }
            let permission = failedPermission ?? .unknown
            let code = failureCode ?? (permission == .denied ? .permissionDenied : .captureFailed)
            guard permission == .unknown || permission == .denied,
                  (permission == .denied) == (code == .permissionDenied) else {
                throw LiveHarnessProtocolError.invalidMessage("failure permission/code mismatch")
            }
            derivedStatus = .failed(permission: permission, code: code)
            derivedFailureCode = code
        } else {
            guard kind == .activation ? status == nil : status != nil else {
                throw LiveHarnessProtocolError.invalidMessage("event kind/status mismatch")
            }
            guard failedPermission == nil, failureCode == nil else {
                throw LiveHarnessProtocolError.invalidMessage("failure fields require failed health")
            }
            derivedStatus = try status.map {
                switch $0 {
                case .idle: return .idle
                case .ready(let health): return .ready(try LiveHarnessEventHealth.from(health, actualEngine: actualEngine))
                case .running(let health): return .running(try LiveHarnessEventHealth.from(health, actualEngine: actualEngine))
                case .stopped(let health): return .stopped(try LiveHarnessEventHealth.from(health, actualEngine: actualEngine))
                case .failed: return .failed(permission: .unknown, code: .captureFailed)
                }
            }
            derivedFailureCode = nil
        }
        let derivedStatusObject = try derivedStatus.map {
            try Self.statusObject($0, actualEngine: actualEngine)
        }
        var retainedValues: [Any] = [
            kind.rawValue, attemptID, launchNonce, sessionID, generation,
            requestedEngine.rawValue, resolvedEngine.rawValue, actualEngine.rawValue,
            sourceObjectID, observerTokenID, derivedAttemptID, derivedLaunchNonce,
            derivedSessionBinding, derivedSourceBinding, derivedObserverBinding
        ]
        if let status {
            switch status {
            case .ready(let health), .running(let health), .stopped(let health):
                retainedValues.append(contentsOf: [
                    health.permission.rawValue, health.route.rawValue,
                    health.interruption.rawValue, health.sleep.rawValue,
                    health.overflowed
                ])
                if let deviceIdentity = health.deviceIdentity { retainedValues.append(deviceIdentity) }
            case .failed(let message):
                retainedValues.append(message)
            case .idle:
                break
            }
        }
        if let derivedStatusObject { retainedValues.append(derivedStatusObject) }
        guard !retainedValues.contains(where: {
            Self.containsActiveStreamKey($0, key: activeStreamKey)
        }) else {
            throw LiveHarnessProtocolError.invalidMessage("stream key material entered event identity")
        }
        self.kind = kind
        self.attemptID = derivedAttemptID
        self.launchNonce = derivedLaunchNonce
        self.sessionBinding = derivedSessionBinding
        self.generation = generation
        self.requestedEngine = requestedEngine
        self.resolvedEngine = resolvedEngine
        self.actualEngine = actualEngine
        self.sourceBinding = derivedSourceBinding
        self.observerBinding = derivedObserverBinding
        self.status = derivedStatus
        self.failureCode = derivedFailureCode
    }

    public var redactedFields: [String: String] {
        var fields: [String: String] = [
            "actual_engine": actualEngine.rawValue,
            "attempt_id": attemptID,
            "generation": String(generation),
            "kind": kind.rawValue,
            "launch_nonce": launchNonce,
            "observer_binding": observerBinding,
            "requested_engine": requestedEngine.rawValue,
            "resolved_engine": resolvedEngine.rawValue,
            "session_binding": sessionBinding,
            "source_binding": sourceBinding
        ]
        if let status {
            switch status {
            case .idle: fields["status"] = "idle"
            case .ready: fields["status"] = "ready"
            case .running: fields["status"] = "running"
            case .stopped: fields["status"] = "stopped"
            case .failed(_, let code):
                fields["status"] = "failed"
                fields["failure_code"] = code.rawValue
            }
        }
        return fields
    }

    public func canonicalPayload(activeStreamKey: String) throws -> Data {
        guard Self.validAttemptBinding(attemptID), Self.validLaunchBinding(launchNonce),
              Self.validSessionBinding(sessionBinding), Self.validSourceBinding(sourceBinding),
              Self.validObserverBinding(observerBinding), generation > 0 else {
            throw LiveHarnessProtocolError.invalidMessage("event identity or generation is outside bounds")
        }
        var object: [String: Any] = [
            "actual_engine": actualEngine.rawValue,
            "attempt_id": attemptID,
            "generation": generation,
            "kind": kind.rawValue,
            "launch_nonce": launchNonce,
            "observer_binding": observerBinding,
            "requested_engine": requestedEngine.rawValue,
            "resolved_engine": resolvedEngine.rawValue,
            "session_binding": sessionBinding,
            "source_binding": sourceBinding,
            "type": "event",
            "version": LiveHarnessSessionCommand.protocolVersion
        ]
        if let status {
            guard kind == .health else {
                throw LiveHarnessProtocolError.invalidMessage("activation event must not contain health status")
            }
            object["status"] = try LiveHarnessEvent.statusObject(status, actualEngine: actualEngine)
        } else if kind == .health {
            throw LiveHarnessProtocolError.invalidMessage("health event is missing status")
        }
        let payload = try liveHarnessJSONData(object)
        try Self.rejectActiveStreamKey(activeStreamKey, from: payload, object: object)
        return payload
    }

    public func framed(activeStreamKey: String) throws -> Data {
        try LiveHarnessFrameCodec.frame(payload: canonicalPayload(activeStreamKey: activeStreamKey))
    }

    private static func statusObject(_ status: LiveHarnessEventStatus, actualEngine: ResolvedSystemAudioEngine) throws -> [String: Any] {
        switch status {
        case .idle:
            return [
                "kind": "idle", "permission": PermissionState.unknown.rawValue,
                "route": RouteState.unknown.rawValue, "interruption": InterruptionState.clear.rawValue,
                "sleep": SleepState.awake.rawValue, "overflowed": false
            ]
        case .ready(let health): return healthObject(kind: "ready", health: health)
        case .running(let health): return healthObject(kind: "running", health: health)
        case .stopped(let health): return healthObject(kind: "stopped", health: health)
        case .failed(let permission, let code):
            guard permission == .unknown || permission == .denied,
                  (permission == .denied) == (code == .permissionDenied) else {
                throw LiveHarnessProtocolError.invalidMessage("failure permission/code mismatch")
            }
            return [
                "kind": "failed", "permission": permission.rawValue,
                "route": RouteState.unknown.rawValue, "interruption": InterruptionState.clear.rawValue,
                "sleep": SleepState.awake.rawValue, "overflowed": false,
                "failure_code": code.rawValue
            ]
        }
    }

    private static func healthObject(kind: String, health: LiveHarnessEventHealth) -> [String: Any] {
        return [
            "kind": kind, "permission": health.permission.rawValue,
            "route": health.route.rawValue, "interruption": health.interruption.rawValue,
            "sleep": health.sleep.rawValue, "overflowed": health.overflowed
        ]
    }

    private static func validAttemptBinding(_ value: String) -> Bool { value.range(of: #"^at1_[0-9a-f]{32}$"#, options: .regularExpression) != nil }
    private static func validLaunchBinding(_ value: String) -> Bool { value.range(of: #"^ln1_[0-9a-f]{32}$"#, options: .regularExpression) != nil }
    private static func validSessionBinding(_ value: String) -> Bool { value.range(of: #"^sb1_[0-9a-f]{64}$"#, options: .regularExpression) != nil }
    private static func validSourceBinding(_ value: String) -> Bool { value.range(of: #"^so1_[0-9a-f]{32}$"#, options: .regularExpression) != nil }
    private static func validObserverBinding(_ value: String) -> Bool { value.range(of: #"^ob1_[0-9a-f]{32}$"#, options: .regularExpression) != nil }

    private static func rejectActiveStreamKey(
        _ activeStreamKey: String,
        from payload: Data,
        object: [String: Any]
    ) throws {
        guard validStreamKey(activeStreamKey) else {
            throw LiveHarnessProtocolError.invalidMessage("event boundary requires the exact active stream key")
        }
        let bytes = Data(activeStreamKey.utf8)
        let raw = [UInt8](payload)
        let needle = [UInt8](bytes)
        if needle.count <= raw.count, !needle.isEmpty {
            for start in 0...(raw.count - needle.count) {
                if raw[start..<(start + needle.count)].elementsEqual(needle) {
                    throw LiveHarnessProtocolError.invalidMessage("stream key material entered event wire")
                }
            }
        }
        if containsActiveStreamKey(object, key: activeStreamKey) {
            throw LiveHarnessProtocolError.invalidMessage("stream key material entered event value")
        }
    }

    private static func containsActiveStreamKey(_ value: Any, key: String) -> Bool {
        if let string = value as? String { return string.contains(key) }
        if let mapping = value as? [String: Any] {
            for (name, item) in mapping {
                if name.contains(key) || containsActiveStreamKey(item, key: key) {
                    return true
                }
            }
            return false
        }
        if let values = value as? [Any] {
            return values.contains { containsActiveStreamKey($0, key: key) }
        }
        return false
    }

    private static func binding128(label: String, value: String) -> String {
        var data = Data(label.utf8)
        let bytes = Data(value.utf8)
        var length = UInt32(bytes.count).bigEndian
        withUnsafeBytes(of: &length) { data.append(contentsOf: $0) }
        data.append(bytes)
        return SHA256.hash(data: data).prefix(16).map { String(format: "%02x", $0) }.joined()
    }

    private static func launchBinding(for launchNonce: String) -> String {
        "ln1_" + binding128(label: "tars-live-harness/launch-nonce-binding/v2", value: launchNonce)
    }

    public static func sessionBinding(for sessionID: String, launchNonce: String) throws -> String {
        guard validIdentifier(sessionID), validIdentifier(launchNonce) else {
            throw LiveHarnessProtocolError.invalidMessage("session binding inputs are not producer identifiers")
        }
        var data = Data("tars-live-harness/session-binding/v2".utf8)
        let session = Data(sessionID.utf8)
        let nonce = Data(launchNonce.utf8)
        var sessionLength = UInt32(session.count).bigEndian
        var nonceLength = UInt32(nonce.count).bigEndian
        withUnsafeBytes(of: &sessionLength) { data.append(contentsOf: $0) }
        data.append(session)
        withUnsafeBytes(of: &nonceLength) { data.append(contentsOf: $0) }
        data.append(nonce)
        return "sb1_" + SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}

public extension LiveHarnessEvent {
    /// Strict decoder used by offline control-boundary tests.  The app only
    /// writes events, but keeping the schema validator in the library makes
    /// the bidirectional contract auditable by either side of the socket.
    static func decode(canonicalPayload payload: Data, activeStreamKey: String) throws -> [String: Any] {
        guard validStreamKey(activeStreamKey) else {
            throw LiveHarnessProtocolError.invalidMessage("event decoder requires the exact active stream key")
        }
        try Self.rejectActiveStreamKey(activeStreamKey, from: payload, object: [:])
        let object = try LiveHarnessFrameCodec.decodeCanonicalObject(payload)
        let base: Set<String> = [
            "actual_engine", "attempt_id", "generation", "kind", "launch_nonce",
            "observer_binding", "requested_engine", "resolved_engine", "session_binding", "source_binding",
            "type", "version"
        ]
        guard let kind = object["kind"] as? String, LiveHarnessEventKind(rawValue: kind) != nil else {
            throw LiveHarnessProtocolError.invalidMessage("invalid event kind")
        }
        let expected = kind == LiveHarnessEventKind.health.rawValue ? base.union(["status"]) : base
        guard Set(object.keys) == expected else {
            throw LiveHarnessProtocolError.invalidMessage("event field allowlist violation")
        }
        guard object["type"] as? String == "event",
              let versionNumber = object["version"] as? NSNumber,
              CFGetTypeID(versionNumber) != CFBooleanGetTypeID(),
              let version = exactUnsignedIntegerField("version", in: payload),
              version == LiveHarnessSessionCommand.protocolVersion,
              let generationNumber = object["generation"] as? NSNumber,
              CFGetTypeID(generationNumber) != CFBooleanGetTypeID(),
              let generation = exactUnsignedIntegerField("generation", in: payload),
              generation > 0,
              let attemptID = object["attempt_id"] as? String,
              attemptID.range(of: #"^at1_[0-9a-f]{32}$"#, options: .regularExpression) != nil,
              let nonce = object["launch_nonce"] as? String,
              nonce.range(of: #"^ln1_[0-9a-f]{32}$"#, options: .regularExpression) != nil,
              let sessionBinding = object["session_binding"] as? String,
              sessionBinding.range(of: #"^sb1_[0-9a-f]{64}$"#, options: .regularExpression) != nil,
              let sourceBinding = object["source_binding"] as? String,
              sourceBinding.range(of: #"^so1_[0-9a-f]{32}$"#, options: .regularExpression) != nil,
              let observerBinding = object["observer_binding"] as? String,
              observerBinding.range(of: #"^ob1_[0-9a-f]{32}$"#, options: .regularExpression) != nil,
              let requested = object["requested_engine"] as? String,
              ResolvedSystemAudioEngine(rawValue: requested) != nil,
              let resolved = object["resolved_engine"] as? String,
              ResolvedSystemAudioEngine(rawValue: resolved) != nil,
              let actual = object["actual_engine"] as? String,
              ResolvedSystemAudioEngine(rawValue: actual) != nil else {
            throw LiveHarnessProtocolError.invalidMessage("event field type/bounds violation")
        }
        if kind == LiveHarnessEventKind.health.rawValue {
            guard let status = object["status"] as? [String: Any] else {
                throw LiveHarnessProtocolError.invalidMessage("health status is not an object")
            }
            let statusFields: Set<String> = [
                "interruption", "kind", "overflowed", "permission", "route", "sleep"
            ]
            let failureFields = statusFields.union(["failure_code"])
            guard let statusKind = status["kind"] as? String,
                  ["idle", "ready", "running", "stopped", "failed"].contains(statusKind),
                  let permission = status["permission"] as? String,
                  PermissionState(rawValue: permission) != nil,
                  let route = status["route"] as? String,
                  RouteState(rawValue: route) != nil,
                  let interruption = status["interruption"] as? String,
                  InterruptionState(rawValue: interruption) != nil,
                  let sleep = status["sleep"] as? String,
                  SleepState(rawValue: sleep) != nil,
                  let overflowed = status["overflowed"] as? NSNumber,
                  CFGetTypeID(overflowed) == CFBooleanGetTypeID() else {
                throw LiveHarnessProtocolError.invalidMessage("health status fields are invalid")
            }
            if statusKind == "failed" {
                guard Set(status.keys) == failureFields,
                      let failureCode = status["failure_code"] as? String,
                      let code = LiveHarnessFailureCode(rawValue: failureCode),
                      permission == PermissionState.unknown.rawValue
                        || permission == PermissionState.denied.rawValue,
                      (permission == PermissionState.denied.rawValue) == (code == .permissionDenied),
                      route == RouteState.unknown.rawValue,
                      interruption == InterruptionState.clear.rawValue,
                      sleep == SleepState.awake.rawValue,
                      overflowed == false else {
                    throw LiveHarnessProtocolError.invalidMessage("failed health status is not canonical")
                }
            } else {
                guard Set(status.keys) == statusFields else {
                    throw LiveHarnessProtocolError.invalidMessage("non-failed health status must not contain a failure code")
                }
            }
        }
        _ = attemptID; _ = nonce; _ = sessionBinding; _ = sourceBinding; _ = observerBinding
        _ = generation
        try Self.rejectActiveStreamKey(activeStreamKey, from: payload, object: object)
        return object
    }
}

public typealias LiveHarnessEventObserver = @Sendable (LiveHarnessEvent) -> Void

public enum LiveHarnessFrameCodec {
    public static let maximumPayloadLength = 64 * 1024

    public static func frame(payload: Data) throws -> Data {
        guard !payload.isEmpty else { throw LiveHarnessProtocolError.invalidFrame("zero-length payload") }
        guard payload.count <= maximumPayloadLength else {
            throw LiveHarnessProtocolError.invalidFrame("payload exceeds maximum")
        }
        var result = Data([
            UInt8((payload.count >> 24) & 0xff),
            UInt8((payload.count >> 16) & 0xff),
            UInt8((payload.count >> 8) & 0xff),
            UInt8(payload.count & 0xff)
        ])
        result.append(payload)
        return result
    }

    /// The payload must be one canonical JSON object.  JSONSerialization is
    /// used only after a byte-for-byte canonical round-trip, which rejects
    /// duplicate keys, whitespace/trailing bytes, noncanonical ordering and
    /// non-object fragments without retaining the raw frame.
    public static func decodeCanonicalObject(_ payload: Data) throws -> [String: Any] {
        guard !payload.isEmpty, payload.count <= maximumPayloadLength else {
            throw LiveHarnessProtocolError.invalidFrame("payload bounds")
        }
        guard let object = try? JSONSerialization.jsonObject(with: payload, options: []) as? [String: Any] else {
            throw LiveHarnessProtocolError.invalidMessage("payload is not a JSON object")
        }
        guard let canonical = try? liveHarnessJSONData(object),
              canonical == payload else {
            throw LiveHarnessProtocolError.invalidMessage("payload is not canonical or contains duplicate/trailing data")
        }
        return object
    }
}

public struct LiveHarnessFrameDecoder: Sendable {
    private var buffer = Data()
    public init() {}

    public mutating func append(_ bytes: Data) throws -> [Data] {
        guard !bytes.isEmpty else { return [] }
        buffer.append(bytes)
        var messages: [Data] = []
        while true {
            guard buffer.count >= 4 else { break }
            let length = (Int(buffer[buffer.startIndex]) << 24)
                | (Int(buffer[buffer.startIndex + 1]) << 16)
                | (Int(buffer[buffer.startIndex + 2]) << 8)
                | Int(buffer[buffer.startIndex + 3])
            guard length > 0 else { throw LiveHarnessProtocolError.invalidFrame("zero-length payload") }
            guard length <= LiveHarnessFrameCodec.maximumPayloadLength else {
                throw LiveHarnessProtocolError.invalidFrame("payload exceeds maximum")
            }
            guard buffer.count >= 4 + length else { break }
            let payload = buffer.subdata(in: 4..<(4 + length))
            buffer.removeSubrange(0..<(4 + length))
            messages.append(payload)
        }
        return messages
    }

    public mutating func finish() throws {
        guard buffer.isEmpty else { throw LiveHarnessProtocolError.incompleteFrame }
    }
}

public struct LiveHarnessPeerIdentity: Equatable, Sendable {
    public let euid: Int32
    public let pid: Int32?
    public let auditToken: String?
    public let executablePath: String?

    public init(euid: Int32, pid: Int32? = nil, auditToken: String? = nil, executablePath: String? = nil) {
        self.euid = euid
        self.pid = pid
        self.auditToken = auditToken
        self.executablePath = executablePath
    }
}

public struct LiveHarnessPeerPolicy: Equatable, Sendable {
    public let expectedClient: LiveHarnessPeerIdentity
    public let expectedServerEUID: Int32

    public init(expectedClient: LiveHarnessPeerIdentity, expectedServerEUID: Int32) {
        self.expectedClient = expectedClient
        self.expectedServerEUID = expectedServerEUID
    }

    public func validateClient(_ identity: LiveHarnessPeerIdentity) throws {
        guard identity.euid == expectedClient.euid else { throw LiveHarnessProtocolError.peerRejected("eUID") }
        if let expected = expectedClient.pid, identity.pid != expected {
            throw LiveHarnessProtocolError.peerRejected("PID")
        }
        if let expected = expectedClient.auditToken, identity.auditToken != expected {
            throw LiveHarnessProtocolError.peerRejected("audit token")
        }
        if let expected = expectedClient.executablePath, identity.executablePath != expected {
            throw LiveHarnessProtocolError.peerRejected("executable path")
        }
    }

    public func validateServerEUID(_ euid: Int32) throws {
        guard euid == expectedServerEUID else { throw LiveHarnessProtocolError.peerRejected("server eUID") }
    }
}

/// One-session ownership and command decoding are kept independent from the
/// socket API so tests can inject kernel identities and fragmented bytes.
public final class LiveHarnessControlCore: @unchecked Sendable {
    public let peerPolicy: LiveHarnessPeerPolicy
    public let launchNonce: String
    private let lock = NSLock()
    private var authenticated = false
    private var commandConsumed = false

    public init(peerPolicy: LiveHarnessPeerPolicy, launchNonce: String) throws {
        guard validIdentifier(launchNonce) else { throw LiveHarnessProtocolError.invalidMessage("invalid launch nonce") }
        self.peerPolicy = peerPolicy
        self.launchNonce = launchNonce
    }

    public func authenticateClient(_ identity: LiveHarnessPeerIdentity) throws {
        try lock.withLock {
            guard !authenticated else { throw LiveHarnessProtocolError.peerRejected("duplicate peer authentication") }
            try peerPolicy.validateClient(identity)
            authenticated = true
        }
    }

    public func acceptServer(euid: Int32) throws { try peerPolicy.validateServerEUID(euid) }

    public func consume(command: LiveHarnessSessionCommand) throws -> LiveHarnessSessionCommand {
        try lock.withLock {
            guard authenticated else { throw LiveHarnessProtocolError.peerRejected("peer not authenticated") }
            guard !commandConsumed else { throw LiveHarnessProtocolError.duplicateCommand }
            guard command.launchNonce == launchNonce else { throw LiveHarnessProtocolError.peerRejected("launch nonce") }
            commandConsumed = true
            return command
        }
    }

    public var hasConsumedCommand: Bool { lock.withLock { commandConsumed } }
}

public protocol LiveHarnessPeerIdentityProvider: Sendable {
    func identity(for fileDescriptor: Int32) throws -> LiveHarnessPeerIdentity
}

/// The production boundary intentionally exposes only the server eUID here.
/// PID/audit/executable identity is authenticated by the Python server through
/// its Darwin peer boundary; tests inject all fields into the control core.
public struct DarwinLiveHarnessPeerIdentityProvider: LiveHarnessPeerIdentityProvider {
    public init() {}

    public func identity(for fileDescriptor: Int32) throws -> LiveHarnessPeerIdentity {
        #if canImport(Darwin)
        var euid: uid_t = 0
        var egid: gid_t = 0
        guard getpeereid(fileDescriptor, &euid, &egid) == 0 else {
            throw LiveHarnessProtocolError.peerRejected("getpeereid failed")
        }
        return LiveHarnessPeerIdentity(euid: Int32(euid))
        #else
        _ = fileDescriptor
        throw LiveHarnessProtocolError.peerRejected("Darwin peer identity unavailable")
        #endif
    }
}

public struct LiveHarnessLaunchConfiguration: Equatable, Sendable {
    public let socketPath: String
    public let launchNonce: String
    public let engine: SystemAudioEnginePreference

    public init(socketPath: String, launchNonce: String, engine: SystemAudioEnginePreference = .processTap) throws {
        guard socketPath.hasPrefix("/"), socketPath.utf8.count <= 255,
              !socketPath.contains("\0"), validIdentifier(launchNonce), engine == .processTap else {
            throw LiveHarnessProtocolError.invalidMessage("invalid harness launch configuration")
        }
        self.socketPath = socketPath
        self.launchNonce = launchNonce
        self.engine = engine
    }

    public static func isHarnessMode(arguments: [String]) -> Bool {
        arguments.dropFirst().contains("--live-harness-socket") || arguments.dropFirst().contains("--live-harness-nonce")
    }

    public static func parse(arguments: [String]) throws -> LiveHarnessLaunchConfiguration {
        guard isHarnessMode(arguments: arguments) else {
            throw LiveHarnessProtocolError.invalidMessage("harness mode is not present")
        }
        var socket: String?
        var nonce: String?
        var engine: SystemAudioEnginePreference = .processTap
        var engineSeen = false
        var index = 1
        while index < arguments.count {
            switch arguments[index] {
            case "--live-harness-socket":
                guard socket == nil, index + 1 < arguments.count else { throw LiveHarnessProtocolError.invalidMessage("duplicate/missing socket") }
                socket = arguments[index + 1]; index += 2
            case "--live-harness-nonce":
                guard nonce == nil, index + 1 < arguments.count else { throw LiveHarnessProtocolError.invalidMessage("duplicate/missing nonce") }
                nonce = arguments[index + 1]; index += 2
            case "--system-audio-engine":
                guard !engineSeen, index + 1 < arguments.count, arguments[index + 1] == "process-tap" else {
                    throw LiveHarnessProtocolError.invalidMessage("harness requires process-tap")
                }
                engineSeen = true
                engine = .processTap; index += 2
            default:
                throw LiveHarnessProtocolError.invalidMessage("unexpected harness argument")
            }
        }
        guard let socket, let nonce, engineSeen else { throw LiveHarnessProtocolError.invalidMessage("missing harness argument") }
        return try LiveHarnessLaunchConfiguration(socketPath: socket, launchNonce: nonce, engine: engine)
    }
}

/// A durable bidirectional AF_UNIX session.  The descriptor remains open after
/// the credential-bearing command is decoded; the app writes activation and
/// health events on this object and independently monitors the server for EOF,
/// duplicate commands, and bounded read deadlines.
public final class LiveHarnessControlConnection: @unchecked Sendable {
    private let descriptor: Int32
    private let readTimeoutNanoseconds: UInt64
    private let writeTimeoutNanoseconds: UInt64
    private let lock = NSLock()
    private var closed = false
    private var decoder = LiveHarnessFrameDecoder()
    private var waiterRegistered = false
    private var waiterActive = false
    private var shutdownRequested = false
    // This is retained only by the credential-bearing control boundary for
    // complete-sentinel rechecks.  It is never copied into an event or
    // evidence value.
    private var activeStreamKey: String?
    private var activeSessionBinding: String?
    private var shutdownRequest: LiveHarnessShutdownRequest?
    // Offline tests may pause the two interleavings that matter for the
    // shutdown/write transaction.  These hooks carry no credential data and
    // are nil for every production construction path.
    private let beforeWriteAdmission: (@Sendable () -> Void)?
    private let beforeShutdownSyscall: (@Sendable () -> Void)?
    private let afterControlLossRetired: (@Sendable () -> Void)?
    private let afterShutdownRequestDecoded: (@Sendable () -> Void)?

    public convenience init(descriptor: Int32, readTimeoutNanoseconds: UInt64, writeTimeoutNanoseconds: UInt64) throws {
        try self.init(
            descriptor: descriptor,
            readTimeoutNanoseconds: readTimeoutNanoseconds,
            writeTimeoutNanoseconds: writeTimeoutNanoseconds,
            beforeWriteAdmission: nil,
            beforeShutdownSyscall: nil,
            afterControlLossRetired: nil,
            afterShutdownRequestDecoded: nil
        )
    }

    internal init(
        descriptor: Int32,
        readTimeoutNanoseconds: UInt64,
        writeTimeoutNanoseconds: UInt64,
        beforeWriteAdmission: (@Sendable () -> Void)?,
        beforeShutdownSyscall: (@Sendable () -> Void)?,
        afterControlLossRetired: (@Sendable () -> Void)? = nil,
        afterShutdownRequestDecoded: (@Sendable () -> Void)? = nil
    ) throws {
        self.descriptor = descriptor
        self.readTimeoutNanoseconds = max(1, readTimeoutNanoseconds)
        self.writeTimeoutNanoseconds = max(1, writeTimeoutNanoseconds)
        self.beforeWriteAdmission = beforeWriteAdmission
        self.beforeShutdownSyscall = beforeShutdownSyscall
        self.afterControlLossRetired = afterControlLossRetired
        self.afterShutdownRequestDecoded = afterShutdownRequestDecoded
        // An injected descriptor must receive the same close-on-exec
        // treatment as the descriptor created by ``openSession``.  Read the
        // complete existing descriptor flags and write them back with only
        // FD_CLOEXEC added; this is idempotent and preserves caller flags.
        try Self.installCloseOnExec(descriptor: descriptor)
        try Self.installTimeouts(
            descriptor: descriptor,
            readTimeoutNanoseconds: self.readTimeoutNanoseconds,
            writeTimeoutNanoseconds: self.writeTimeoutNanoseconds
        )
    }

    deinit { close() }

    public func close() {
        let action = lock.withLock { () -> (close: Bool, shutdown: Bool) in
            if closed {
                activeStreamKey = nil
                activeSessionBinding = nil
                return (false, false)
            }
            // A writer or unrelated cleanup edge must never close a
            // descriptor while the sole recv owner is inside Darwin.recv.
            // Requesting shutdown is safe and wakes that owner; the caller
            // may close only after the waiter has returned and been joined.
            if waiterActive {
                // Retire the credential in the same atomic transition that
                // requests shutdown.  A concurrent writer can therefore
                // never observe a live key after close begins.
                shutdownRequested = true
                activeStreamKey = nil
                activeSessionBinding = nil
                return (false, true)
            }
            closed = true
            shutdownRequested = true
            activeStreamKey = nil
            activeSessionBinding = nil
            return (true, false)
        }
        if action.shutdown {
            // The lock transition above already marked shutdownRequested;
            // issue the wake directly so the waiter is not left blocked by a
            // second guard that quite correctly sees the flag as set.
            #if canImport(Darwin)
            _ = Darwin.shutdown(descriptor, SHUT_RDWR)
            #endif
        }
        if action.close {
            #if canImport(Darwin)
            _ = Darwin.close(descriptor)
            #endif
        }
    }

    /// Wake the sole control-loss waiter without closing or reusing the file
    /// descriptor.  The waiter owner performs the eventual idempotent close
    /// after its task has returned; this edge is safe for event-writer races.
    public func requestShutdown() {
        let shouldShutdown = lock.withLock { () -> Bool in
            guard !closed, !shutdownRequested else { return false }
            shutdownRequested = true
            activeStreamKey = nil
            activeSessionBinding = nil
            return true
        }
        guard shouldShutdown else { return }
        beforeShutdownSyscall?()
        #if canImport(Darwin)
        _ = Darwin.shutdown(descriptor, SHUT_RDWR)
        #endif
    }

    /// Read exactly one session command and leave the connection open for
    /// subsequent event writes and control-liveness monitoring.
    public func receiveOneCommand() throws -> LiveHarnessSessionCommand {
        // The initial command is one bounded transaction.  A peer that drips
        // one valid fragment just before every socket timeout must not be able
        // to extend authentication forever by resetting the per-syscall
        // timeout.  Durable post-command liveness below deliberately remains
        // polling and has no corresponding absolute deadline.
        let deadline = Self.monotonicDeadline(after: readTimeoutNanoseconds)
        var command: LiveHarnessSessionCommand?
        while command == nil {
            do {
                let payloads = try readPayloads(until: deadline)
                for payload in payloads {
                    guard command == nil else { throw LiveHarnessProtocolError.duplicateCommand }
                    command = try LiveHarnessSessionCommand.decode(canonicalPayload: payload)
                }
            } catch LiveHarnessProtocolError.timeout {
                guard Self.remainingNanoseconds(until: deadline) != nil else {
                    throw LiveHarnessProtocolError.timeout
                }
            }
        }
        // A second complete frame or any partial trailing frame in the same
        // read is a protocol violation, not a deferred command.
        try lock.withLock { try decoder.finish() }
        guard let command else { throw LiveHarnessProtocolError.controlLost }
        lock.withLock {
            activeStreamKey = command.streamKey
            activeSessionBinding = LiveHarnessControlBinding.sessionBinding(
                sessionID: command.sessionID,
                launchNonce: command.launchNonce
            )
            shutdownRequest = nil
        }
        return command
    }

    /// Wait for exactly one nonce-bound stop request after the session command.
    /// Fragmentation is accepted; any malformed, duplicate, trailing, stale,
    /// or incorrectly bound input retires the connection before the error.
    public func waitForShutdownRequest(onReady: @escaping @Sendable () -> Void = {}) throws -> LiveHarnessShutdownRequest {
        try lock.withLock {
            guard !closed else { throw LiveHarnessProtocolError.controlLost }
            guard !waiterRegistered else { throw LiveHarnessProtocolError.duplicateWaiter }
            waiterRegistered = true
            waiterActive = true
        }
        onReady()
        defer {
            lock.withLock {
                waiterActive = false
                // A valid request is not terminal until its acknowledgement
                // crosses the serialized writer.  Keep both binding slots
                // live while queued event writes drain; failure retirement
                // and sendShutdownAcknowledgement clear them atomically.
                if shutdownRequest == nil {
                    activeStreamKey = nil
                }
            }
        }
        // Waiting for the stop request is post-command liveness: a healthy
        // peer may remain quiet across any number of read timeouts while
        // capture/TCC is still in progress.  The absolute deadline applies
        // only after a request is decoded, to bound the trailing-byte check.
        var decodedRequest: LiveHarnessShutdownRequest?
        while decodedRequest == nil {
            do {
                #if canImport(Darwin)
                try Self.setSocketTimeout(descriptor: descriptor, option: SO_RCVTIMEO, nanoseconds: readTimeoutNanoseconds)
                var bytes = [UInt8](repeating: 0, count: 8192)
                let count = Darwin.recv(descriptor, &bytes, bytes.count, 0)
                if count == 0 {
                    retireControlLoss()
                    throw LiveHarnessProtocolError.controlLost
                }
                if count < 0 {
                    if errno == EAGAIN || errno == EWOULDBLOCK {
                        continue
                    }
                    retireControlLoss()
                    throw LiveHarnessProtocolError.controlLost
                }
                let payloads = try lock.withLock { try decoder.append(Data(bytes[0..<count])) }
                guard !payloads.isEmpty else { continue }
                guard payloads.count == 1 else {
                    retireControlLoss()
                    throw LiveHarnessProtocolError.duplicateCommand
                }
                let request = try LiveHarnessShutdownRequest.decode(canonicalPayload: payloads[0])
                try lock.withLock { try decoder.finish() }
                let activeBinding = lock.withLock { self.activeSessionBinding }
                guard let activeBinding else {
                    retireControlLoss()
                    throw LiveHarnessProtocolError.controlLost
                }
                let expected = LiveHarnessControlBinding.shutdownBinding(
                    sessionBinding: activeBinding,
                    shutdownNonce: request.shutdownNonce
                )
                guard request.shutdownBinding == expected else {
                    retireControlLoss()
                    throw LiveHarnessProtocolError.peerRejected("shutdown binding")
                }
                decodedRequest = request
                afterShutdownRequestDecoded?()
                #else
                retireControlLoss()
                throw LiveHarnessProtocolError.controlLost
                #endif
            } catch LiveHarnessProtocolError.timeout {
                continue
            } catch {
                retireControlLoss()
                throw error
            }
        }
        guard let request = decodedRequest else {
            retireControlLoss()
            throw LiveHarnessProtocolError.controlLost
        }
        let deadline = Self.monotonicDeadline(after: readTimeoutNanoseconds)
        while true {
            do {
                guard let remaining = Self.remainingNanoseconds(until: deadline) else {
                    retireControlLoss()
                    throw LiveHarnessProtocolError.timeout
                }
                #if canImport(Darwin)
                try Self.setSocketTimeout(descriptor: descriptor, option: SO_RCVTIMEO, nanoseconds: remaining)
                var bytes = [UInt8](repeating: 0, count: 8192)
                let count = Darwin.recv(descriptor, &bytes, bytes.count, 0)
                if count == 0 {
                    break
                }
                if count < 0 {
                    if errno == EAGAIN || errno == EWOULDBLOCK {
                        guard Self.remainingNanoseconds(until: deadline) != nil else {
                            retireControlLoss()
                            throw LiveHarnessProtocolError.timeout
                        }
                        continue
                    }
                    retireControlLoss()
                    throw LiveHarnessProtocolError.controlLost
                }
                retireControlLoss()
                throw LiveHarnessProtocolError.duplicateCommand
                #else
                retireControlLoss()
                throw LiveHarnessProtocolError.controlLost
                #endif
            } catch LiveHarnessProtocolError.timeout {
                retireControlLoss()
                throw LiveHarnessProtocolError.timeout
            } catch {
                retireControlLoss()
                throw error
            }
        }
        try lock.withLock {
            guard !closed, !shutdownRequested, self.activeSessionBinding != nil else {
                throw LiveHarnessProtocolError.controlLost
            }
            guard self.shutdownRequest == nil else {
                throw LiveHarnessProtocolError.duplicateCommand
            }
            try decoder.finish()
            self.shutdownRequest = request
        }
        return request
    }

    /// Serialize the matching acknowledgement after all queued event writes.
    /// The final state transition retires the stream key only after the bytes
    /// have crossed this sole-writer lock.
    public func sendShutdownAcknowledgement(_ request: LiveHarnessShutdownRequest) throws {
        let acknowledgement = try lock.withLock { () throws -> LiveHarnessShutdownAcknowledgement in
            guard !closed, !shutdownRequested,
                  shutdownRequest == request,
                  let activeSessionBinding else {
                throw LiveHarnessProtocolError.controlLost
            }
            let expectedBinding = LiveHarnessControlBinding.shutdownBinding(
                sessionBinding: activeSessionBinding,
                shutdownNonce: request.shutdownNonce
            )
            guard request.shutdownBinding == expectedBinding else {
                throw LiveHarnessProtocolError.peerRejected("shutdown binding")
            }
            return try LiveHarnessShutdownAcknowledgement(
                shutdownNonce: request.shutdownNonce,
                shutdownBinding: request.shutdownBinding
            )
        }
        let wire = try acknowledgement.framed()
        let deadline = Self.monotonicDeadline(after: writeTimeoutNanoseconds)
        try lock.withLock {
            guard !closed, !shutdownRequested, shutdownRequest == request else {
                throw LiveHarnessProtocolError.controlLost
            }
            do {
                try writeAllLocked(
                    wire,
                    expectedKey: nil,
                    requireActiveKey: false,
                    until: deadline
                )
            } catch {
                // An acknowledgement write failure is terminal.  Retire the
                // request and both binding slots before the original error
                // escapes so no later event/ack attempt can reuse authority.
                shutdownRequested = true
                activeStreamKey = nil
                activeSessionBinding = nil
                shutdownRequest = nil
                throw error
            }
            shutdownRequested = true
            activeStreamKey = nil
            activeSessionBinding = nil
        }
    }

    /// Encode and deadline-write one event.  Event schemas are typed and have
    /// no stream-key field; raw frame bytes never leave this method's scope.
    public func send(event: LiveHarnessEvent) throws {
        let deadline = Self.monotonicDeadline(after: writeTimeoutNanoseconds)
        let key = try lock.withLock { () throws -> String in
            guard !closed, !shutdownRequested else {
                throw LiveHarnessProtocolError.controlLost
            }
            guard let activeStreamKey else { throw LiveHarnessProtocolError.controlLost }
            return activeStreamKey
        }
        let wire = try event.framed(activeStreamKey: key)
        try lock.withLock {
            guard !closed, !shutdownRequested, activeStreamKey == key else {
                throw LiveHarnessProtocolError.controlLost
            }
            if let text = String(data: wire, encoding: .utf8), text.contains(key) {
                throw LiveHarnessProtocolError.invalidMessage("stream key material entered event wire")
            }
        }
        beforeWriteAdmission?()
        try writeAll(wire, expectedKey: key, until: deadline)
    }

    /// Keep the app-side half open while the harness is alive.  Any inbound
    /// bytes after the sole command are rejected as duplicate/trailing input;
    /// EOF and a real socket error are control-loss failures.  A bounded idle
    /// read is only a liveness polling interval, so a healthy peer may remain
    /// quiet across any number of read deadlines.
    public func waitForControlLoss(onReady: @escaping @Sendable () -> Void = {}) throws {
        try lock.withLock {
            guard !closed else { throw LiveHarnessProtocolError.controlLost }
            guard !waiterRegistered else { throw LiveHarnessProtocolError.duplicateWaiter }
            waiterRegistered = true
            waiterActive = true
        }
        // This callback is after the sole-waiter state is atomically recorded
        // and immediately before the first real recv boundary.  A coordinator
        // may therefore release start only after this exact descriptor owns
        // the control-loss wait.
        onReady()
        defer {
            lock.withLock {
                waiterActive = false
                activeStreamKey = nil
            }
        }
        while true {
            do {
                try readPostCommandBytes()
            } catch LiveHarnessProtocolError.timeout {
                continue
            }
        }
    }

    /// After the sole command every byte is forbidden, including a partial
    /// frame.  Do not feed this read into the command decoder: a single byte is
    /// already enough to revoke the control channel.
    private func readPostCommandBytes() throws {
        #if canImport(Darwin)
        var bytes = [UInt8](repeating: 0, count: 8192)
        let count = Darwin.recv(descriptor, &bytes, bytes.count, 0)
        if count == 0 {
            retireControlLoss()
            throw LiveHarnessProtocolError.controlLost
        }
        if count < 0 {
            if errno == EAGAIN || errno == EWOULDBLOCK { throw LiveHarnessProtocolError.timeout }
            retireControlLoss()
            throw LiveHarnessProtocolError.controlLost
        }
        retireControlLoss()
        throw LiveHarnessProtocolError.duplicateCommand
        #else
        retireControlLoss()
        throw LiveHarnessProtocolError.controlLost
        #endif
    }

    private func readPostCommandPayloads() throws -> [Data] {
        #if canImport(Darwin)
        var bytes = [UInt8](repeating: 0, count: 8192)
        let count = Darwin.recv(descriptor, &bytes, bytes.count, 0)
        if count == 0 { throw LiveHarnessProtocolError.controlLost }
        if count < 0 {
            if errno == EAGAIN || errno == EWOULDBLOCK { throw LiveHarnessProtocolError.timeout }
            throw LiveHarnessProtocolError.controlLost
        }
        return try lock.withLock { try decoder.append(Data(bytes[0..<count])) }
        #else
        throw LiveHarnessProtocolError.controlLost
        #endif
    }

    /// Retire the post-command credential before any loss error unwinds.
    /// The hook is intentionally credential-free and exists only for
    /// deterministic offline interleaving tests.
    private func retireControlLoss() {
        lock.withLock {
            shutdownRequested = true
            activeStreamKey = nil
            activeSessionBinding = nil
            shutdownRequest = nil
        }
        afterControlLossRetired?()
    }

    private func readPayloads(until deadline: UInt64) throws -> [Data] {
        #if canImport(Darwin)
        guard let remaining = Self.remainingNanoseconds(until: deadline) else {
            throw LiveHarnessProtocolError.timeout
        }
        try Self.setSocketTimeout(descriptor: descriptor, option: SO_RCVTIMEO, nanoseconds: remaining)
        var bytes = [UInt8](repeating: 0, count: 8192)
        let count = Darwin.recv(descriptor, &bytes, bytes.count, 0)
        if count == 0 { throw LiveHarnessProtocolError.controlLost }
        if count < 0 {
            if errno == EAGAIN || errno == EWOULDBLOCK { throw LiveHarnessProtocolError.timeout }
            throw LiveHarnessProtocolError.controlLost
        }
        return try lock.withLock { try decoder.append(Data(bytes[0..<count])) }
        #else
        throw LiveHarnessProtocolError.controlLost
        #endif
    }

    private func writeAll(_ data: Data, expectedKey: String, until deadline: UInt64) throws {
        try lock.withLock {
            try writeAllLocked(data, expectedKey: expectedKey, requireActiveKey: true, until: deadline)
        }
    }

    private func writeAllLocked(
        _ data: Data,
        expectedKey: String?,
        requireActiveKey: Bool,
        until deadline: UInt64
    ) throws {
        #if canImport(Darwin)
            // Keep the final admission check and the complete write
            // transaction under one lock.  Shutdown can retire the key only
            // before this section begins; once admitted, no concurrent
            // lifecycle edge can interleave fcntl/setsockopt/send calls.
            guard !closed, !shutdownRequested else {
                throw LiveHarnessProtocolError.controlLost
            }
            if requireActiveKey {
                guard let expectedKey, activeStreamKey == expectedKey else {
                    throw LiveHarnessProtocolError.controlLost
                }
            }
            let originalFlags = Darwin.fcntl(descriptor, F_GETFL)
            guard originalFlags >= 0,
                  Darwin.fcntl(descriptor, F_SETFL, originalFlags | O_NONBLOCK) == 0 else {
                throw LiveHarnessProtocolError.controlLost
            }
            defer { _ = Darwin.fcntl(descriptor, F_SETFL, originalFlags) }
            var offset = 0
            try data.withUnsafeBytes { raw in
                guard let base = raw.baseAddress else { throw LiveHarnessProtocolError.controlLost }
                while offset < data.count {
                    guard let remaining = Self.remainingNanoseconds(until: deadline) else {
                        throw LiveHarnessProtocolError.timeout
                    }
                    try Self.setSocketTimeout(descriptor: descriptor, option: SO_SNDTIMEO, nanoseconds: remaining)
                    let count = Darwin.send(
                        descriptor,
                        base.advanced(by: offset),
                        data.count - offset,
                        Int32(MSG_DONTWAIT)
                    )
                    if count <= 0 {
                        if errno == EAGAIN || errno == EWOULDBLOCK {
                            guard Self.remainingNanoseconds(until: deadline) != nil else {
                                throw LiveHarnessProtocolError.timeout
                            }
                            try Self.waitForWritable(descriptor: descriptor, until: deadline)
                            continue
                        }
                        throw LiveHarnessProtocolError.controlLost
                    }
                    offset += count
                }
            }
        #else
        _ = data
        throw LiveHarnessProtocolError.controlLost
        #endif
    }

    private static func monotonicDeadline(after nanoseconds: UInt64) -> UInt64 {
        let now = DispatchTime.now().uptimeNanoseconds
        let duration = max(1, nanoseconds)
        guard now <= UInt64.max - duration else { return UInt64.max }
        return now + duration
    }

    private static func remainingNanoseconds(until deadline: UInt64) -> UInt64? {
        let now = DispatchTime.now().uptimeNanoseconds
        guard now < deadline else { return nil }
        return deadline - now
    }

    #if canImport(Darwin)
    private static func waitForWritable(descriptor: Int32, until deadline: UInt64) throws {
        guard let remaining = remainingNanoseconds(until: deadline) else {
            throw LiveHarnessProtocolError.timeout
        }
        let milliseconds = min(
            Int64(Int32.max),
            max(1, (Int64(remaining) + 999_999) / 1_000_000)
        )
        var pollDescriptor = pollfd(
            fd: descriptor,
            events: Int16(POLLOUT),
            revents: 0
        )
        let result = Darwin.poll(&pollDescriptor, 1, Int32(milliseconds))
        if result < 0 {
            if errno == EINTR { return }
            throw LiveHarnessProtocolError.controlLost
        }
        if pollDescriptor.revents & Int16(POLLNVAL | POLLERR | POLLHUP) != 0 {
            throw LiveHarnessProtocolError.controlLost
        }
    }

    private static func timevalFor(_ nanoseconds: UInt64) -> timeval {
        let rounded = nanoseconds / 1_000 + (nanoseconds % 1_000 == 0 ? 0 : 1)
        let microseconds = max(1, rounded)
        return timeval(
            tv_sec: __darwin_time_t(microseconds / 1_000_000),
            tv_usec: __darwin_suseconds_t(microseconds % 1_000_000)
        )
    }

    private static func setSocketTimeout(descriptor: Int32, option: Int32, nanoseconds: UInt64) throws {
        var timeout = timevalFor(nanoseconds)
        let result = withUnsafePointer(to: &timeout) {
            Darwin.setsockopt(
                descriptor,
                SOL_SOCKET,
                option,
                $0,
                socklen_t(MemoryLayout<timeval>.size)
            )
        }
        guard result == 0 else { throw LiveHarnessProtocolError.controlLost }
    }
    #endif

    private static func installTimeouts(
        descriptor: Int32,
        readTimeoutNanoseconds: UInt64,
        writeTimeoutNanoseconds: UInt64
    ) throws {
        #if canImport(Darwin)
        var noSigPipe: Int32 = 1
        let noSigPipeResult = withUnsafePointer(to: &noSigPipe) {
            Darwin.setsockopt(
                descriptor,
                SOL_SOCKET,
                SO_NOSIGPIPE,
                $0,
                socklen_t(MemoryLayout<Int32>.size)
            )
        }
        guard noSigPipeResult == 0 else {
            throw LiveHarnessProtocolError.controlLost
        }
        try setSocketTimeout(descriptor: descriptor, option: SO_RCVTIMEO, nanoseconds: readTimeoutNanoseconds)
        try setSocketTimeout(descriptor: descriptor, option: SO_SNDTIMEO, nanoseconds: writeTimeoutNanoseconds)
        #else
        _ = descriptor; _ = readTimeoutNanoseconds; _ = writeTimeoutNanoseconds
        throw LiveHarnessProtocolError.controlLost
        #endif
    }

    #if canImport(Darwin)
    fileprivate static func installCloseOnExec(descriptor: Int32) throws {
        let existingFlags = Darwin.fcntl(descriptor, F_GETFD)
        guard existingFlags >= 0 else {
            throw LiveHarnessProtocolError.controlLost
        }
        let preservedFlags = existingFlags | FD_CLOEXEC
        guard Darwin.fcntl(descriptor, F_SETFD, preservedFlags) == 0 else {
            throw LiveHarnessProtocolError.controlLost
        }
    }
    #else
    fileprivate static func installCloseOnExec(descriptor: Int32) throws {
        _ = descriptor
        throw LiveHarnessProtocolError.controlLost
    }
    #endif
}

/// Thin AF_UNIX client used by the menu-bar app.  It authenticates the server
/// eUID before reading the one credential-bearing command and returns a durable
/// connection rather than closing the descriptor after the first read.
public final class LiveHarnessControlClient: @unchecked Sendable {
    private let configuration: LiveHarnessLaunchConfiguration
    private let expectedServerEUID: Int32
    private let peerProvider: LiveHarnessPeerIdentityProvider
    private let readTimeoutNanoseconds: UInt64
    private let writeTimeoutNanoseconds: UInt64

    public init(
        configuration: LiveHarnessLaunchConfiguration,
        expectedServerEUID: Int32,
        peerProvider: LiveHarnessPeerIdentityProvider = DarwinLiveHarnessPeerIdentityProvider(),
        readTimeoutNanoseconds: UInt64 = 15_000_000_000,
        writeTimeoutNanoseconds: UInt64 = 15_000_000_000
    ) {
        self.configuration = configuration
        self.expectedServerEUID = expectedServerEUID
        self.peerProvider = peerProvider
        self.readTimeoutNanoseconds = readTimeoutNanoseconds
        self.writeTimeoutNanoseconds = writeTimeoutNanoseconds
    }

    public var launchNonce: String { configuration.launchNonce }

    public func openSession() throws -> LiveHarnessControlConnection {
        #if canImport(Darwin)
        let descriptor = Darwin.socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw LiveHarnessProtocolError.controlLost }
        do {
            // Set CLOEXEC at the descriptor-creation edge.  In particular,
            // this precedes address construction and connect so no child or
            // connected socket can escape when fcntl fails.
            try LiveHarnessControlConnection.installCloseOnExec(descriptor: descriptor)
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            let maxPath = MemoryLayout.size(ofValue: address.sun_path)
            let encodedPath = Array(configuration.socketPath.utf8) + [0]
            guard encodedPath.count <= maxPath else {
                throw LiveHarnessProtocolError.invalidMessage("socket path too long")
            }
            withUnsafeMutableBytes(of: &address.sun_path) { raw in
                raw.initializeMemory(as: UInt8.self, repeating: 0)
                for (offset, byte) in encodedPath.enumerated() { raw[offset] = byte }
            }
            let addressLength = socklen_t(MemoryLayout<sa_family_t>.size + encodedPath.count)
            let connected = withUnsafePointer(to: &address) { pointer in
                pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.connect(descriptor, $0, addressLength)
                }
            }
            guard connected == 0 else { throw LiveHarnessProtocolError.controlLost }
            let server = try peerProvider.identity(for: descriptor)
            guard server.euid == expectedServerEUID else {
                throw LiveHarnessProtocolError.peerRejected("server eUID")
            }
            return try LiveHarnessControlConnection(
                descriptor: descriptor,
                readTimeoutNanoseconds: readTimeoutNanoseconds,
                writeTimeoutNanoseconds: writeTimeoutNanoseconds
            )
        } catch {
            _ = Darwin.close(descriptor)
            throw error
        }
        #else
        throw LiveHarnessProtocolError.controlLost
        #endif
    }

    /// Compatibility convenience for callers that only need the command.  A
    /// durable caller must use ``openSession`` and retain the returned object.
    public func receiveOneCommand() async throws -> LiveHarnessSessionCommand {
        let connection = try openSession()
        defer { connection.close() }
        let command = try connection.receiveOneCommand()
        guard command.launchNonce == configuration.launchNonce else {
            throw LiveHarnessProtocolError.peerRejected("launch nonce")
        }
        return command
    }
}

private func validIdentifier(_ value: String) -> Bool {
    let bytes = Array(value.utf8)
    guard let first = bytes.first, bytes.count <= 128 else { return false }
    guard (48...57).contains(first) || (65...90).contains(first) || (97...122).contains(first) else { return false }
    return bytes.dropFirst().allSatisfy {
        (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || [45, 46, 58, 95].contains($0)
    }
}

private func validStreamKey(_ value: String) -> Bool {
    let bytes = Array(value.utf8)
    guard bytes.count == 43 else { return false }
    return bytes.allSatisfy {
        (48...57).contains($0) || (65...90).contains($0)
            || (97...122).contains($0) || $0 == 45 || $0 == 95
    }
}

/// Foundation's NSNumber bridge can round an out-of-range JSON integer to a
/// Double whose UInt64 conversion appears valid (notably 2^64).  Recover the
/// canonical decimal token from the already-validated top-level JSON bytes so
/// both bounds and integer spelling remain exact at the wire boundary.
private func exactUnsignedIntegerField(_ key: String, in payload: Data) -> UInt64? {
    guard let text = String(data: payload, encoding: .utf8),
          let keyRange = text.range(of: "\"\(key)\":") else {
        return nil
    }
    let valueStart = keyRange.upperBound
    guard let valueEnd = text[valueStart...].firstIndex(where: { $0 == "," || $0 == "}" }) else {
        return nil
    }
    let token = String(text[valueStart..<valueEnd])
    guard !token.isEmpty, token.utf8.allSatisfy({ (48...57).contains($0) }),
          let value = UInt64(token), String(value) == token else {
        return nil
    }
    return value
}
