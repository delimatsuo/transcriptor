import Foundation

public struct Diagnostics: Sendable {
    private static let maxEvents = 256
    private static let maxCodeBytes = 256
    private var events: [DiagnosticEvent] = []

    public init() {}

    public var snapshot: [DiagnosticEvent] { events }

    public mutating func record(_ event: DiagnosticEvent) {
        guard event.code.utf8.count <= Self.maxCodeBytes else { return }
        events.append(event)
        if events.count > Self.maxEvents {
            events.removeFirst(events.count - Self.maxEvents)
        }
    }

    public mutating func recordSourceHealth(_ health: SourceHealth, source: AudioSource, generation: UInt64) {
        let code: String
        if health.permission == .denied || health.permission == .revoked {
            code = "permission_unavailable"
        } else if health.route == .unavailable || health.route == .ambiguous {
            code = "route_unavailable"
        } else if health.overflowed {
            code = "buffer_overflow"
        } else if health.interruption == .interrupted {
            code = "interrupted"
        } else if health.sleep == .sleeping {
            code = "sleeping"
        } else {
            code = health.isHealthy ? "source_healthy" : "source_degraded"
        }
        record(DiagnosticEvent(code: code, source: source, generation: generation))
    }
}
