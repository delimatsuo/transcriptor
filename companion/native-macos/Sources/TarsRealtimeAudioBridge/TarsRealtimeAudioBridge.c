#include "TarsRealtimeAudioBridge.h"

#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

typedef struct TarsRealtimeAudioSlot {
    uint32_t bufferCount;
    uint32_t bufferByteSizes[TARS_REALTIME_MAX_BUFFERS];
    uint32_t bufferChannels[TARS_REALTIME_MAX_BUFFERS];
    uint32_t totalBytes;
    TarsRealtimeASBDSnapshot asbd;
    double sampleTime;
    uint64_t hostTime;
    uint32_t timestampFlags;
    uint64_t generation;
    uint8_t *bytes;
} TarsRealtimeAudioSlot;

typedef struct TarsRealtimeOverflowMetadataSlot {
    TarsRealtimeOverflowBoundary boundary;
} TarsRealtimeOverflowMetadataSlot;

struct TarsRealtimeAudioRing {
    uint32_t slotCount;
    uint32_t slotCapacity;
    uint32_t expectedChannels;
    bool expectedInterleaved;
    TarsRealtimeASBDSnapshot expectedASBD;
    TarsRealtimeAudioSlot *slots;
    _Atomic uint64_t writeIndex;
    _Atomic uint64_t readIndex;
    _Atomic uint64_t activeGeneration;
    _Atomic uint64_t callbackArrivals;
    _Atomic uint64_t validNonemptyArrivals;
    _Atomic uint64_t emptyArrivals;
    _Atomic uint64_t malformedArrivals;
    _Atomic uint64_t capacityRejectedArrivals;
    _Atomic uint64_t staleGenerationArrivals;
    _Atomic uint64_t ringOverflowCount;
    _Atomic uint64_t ringOverflowEpisodes;
    _Atomic uint64_t enqueuedCount;
    _Atomic uint64_t poppedCount;
    _Atomic uint64_t ringOverflowMetadataDrops;
    _Atomic uint64_t cursorOverflow;
    _Atomic bool overflowEpisodeActive;
    _Atomic bool fenceBeforeNextPushPublicationForTesting;
    /* The high bit closes admission; the low bits count callbacks that have
     * entered the bounded publication protocol.  The callback performs one
     * compare/exchange and never waits.  A non-realtime generation fence
     * closes admission, stores generation zero, and waits for this count to
     * reach zero before reopening it for a new generation. */
    _Atomic uint64_t publicationGate;
    _Atomic bool publicationFenceStartedForTesting;
    _Atomic bool terminalRetirementActive;
    _Atomic bool terminalRetirementHadAdmittedLoss;
    _Atomic uint64_t terminalRetirementLossReadIndex;
    _Atomic uint64_t terminalRetirementLossWriteIndex;
    _Atomic bool holdNextFinalPublicationForTesting;
    _Atomic bool heldFinalPublicationReadyForTesting;
    _Atomic uint64_t heldFinalPublicationWriteIndexForTesting;
    _Atomic uint64_t heldFinalPublicationReadIndexForTesting;
    _Atomic uint32_t heldFinalPublicationByteCountForTesting;
    _Atomic uint64_t heldFinalPublicationGenerationForTesting;
    _Atomic bool terminalRetirementCompleted;
    _Atomic bool terminalRetirementClaimed;
    TarsRealtimeOverflowMetadataSlot overflowMetadata[TARS_REALTIME_MAX_OVERFLOW_EPISODES];
    _Atomic uint64_t overflowMetadataWriteIndex;
    _Atomic uint64_t overflowMetadataReadIndex;
    TarsRealtimeStaleCleanupHook staleCleanupHookForTesting;
    void *staleCleanupContextForTesting;
    TarsRealtimePublicationFenceHook publicationFenceHookForTesting;
    void *publicationFenceContextForTesting;
    TarsRealtimePublicationFenceHook terminalRetirementHookForTesting;
    void *terminalRetirementContextForTesting;
    TarsRealtimeZeroizationHook zeroizationHook;
    void *zeroizationContext;
};

#define TARS_REALTIME_PUBLICATION_CLOSED UINT64_C(0x8000000000000000)
/* The gate is a single lock-free token: bit 63 closes admission, bits 31..62
 * are a monotonically advancing close/reopen epoch, and bits 0..30 count
 * admitted callbacks.  Keeping the epoch in the CAS word prevents a paused
 * producer from succeeding after a close and nonzero reopen has returned the
 * count to zero (the classic 0 -> 1 -> 0 ABA). */
#define TARS_REALTIME_PUBLICATION_EPOCH_SHIFT 31u
#define TARS_REALTIME_PUBLICATION_EPOCH_MASK UINT64_C(0x00000000FFFFFFFF)
#define TARS_REALTIME_PUBLICATION_COUNT_MASK UINT64_C(0x000000007FFFFFFF)
#define TARS_REALTIME_PUBLICATION_EPOCH_MAX UINT64_C(0x00000000FFFFFFFF)

#define TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(gate) \
    (((gate) >> TARS_REALTIME_PUBLICATION_EPOCH_SHIFT) & \
        TARS_REALTIME_PUBLICATION_EPOCH_MASK)

static uint64_t tars_publication_gate_for(uint64_t epoch, uint64_t count, bool closed)
{
    const uint64_t encodedEpoch =
        (epoch & TARS_REALTIME_PUBLICATION_EPOCH_MASK) <<
        TARS_REALTIME_PUBLICATION_EPOCH_SHIFT;
    return encodedEpoch | (count & TARS_REALTIME_PUBLICATION_COUNT_MASK) |
        (closed ? TARS_REALTIME_PUBLICATION_CLOSED : 0u);
}

/* Diagnostic counters must never wrap into an apparently healthy zero.  The
 * bridge has one realtime producer in production, but the one-shot CAS also
 * keeps fixture/concurrent updates from wrapping if an additional producer is
 * introduced.  A failed CAS loses only a diagnostic increment; it never
 * manufactures a smaller counter. */
#define TARS_REALTIME_SATURATING_INCREMENT(counter) do { \
    uint64_t tars_counter_expected = atomic_load_explicit(&(counter), memory_order_relaxed); \
    if (tars_counter_expected != UINT64_MAX) { \
        const uint64_t tars_counter_next = tars_counter_expected + 1u; \
        (void)atomic_compare_exchange_strong_explicit( \
            &(counter), &tars_counter_expected, tars_counter_next, \
            memory_order_relaxed, memory_order_relaxed); \
    } \
} while (0)

#define TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring) \
    atomic_store_explicit(&(ring)->cursorOverflow, 1u, memory_order_release)

static bool tars_try_admit_publication(TarsRealtimeAudioRing *ring,
                                        uint64_t sampledGeneration,
                                        bool *reserved)
{
    if (reserved != NULL) {
        *reserved = false;
    }
    if (ring == NULL || sampledGeneration == 0u) {
        return false;
    }
    uint64_t gate = atomic_load_explicit(&ring->publicationGate, memory_order_acquire);
    if ((gate & TARS_REALTIME_PUBLICATION_CLOSED) != 0u ||
        (gate & TARS_REALTIME_PUBLICATION_COUNT_MASK) == TARS_REALTIME_PUBLICATION_COUNT_MASK) {
        return false;
    }
    const uint64_t sampledEpoch = TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(gate);
    const uint64_t admittedGate = gate + 1u;
    if (!atomic_compare_exchange_strong_explicit(&ring->publicationGate,
                                                  &gate,
                                                  admittedGate,
                                                  memory_order_acq_rel,
                                                  memory_order_acquire)) {
        return false;
    }
    if (reserved != NULL) {
        *reserved = true;
    }

    /* This recheck closes the race where a producer sampled the old
     * generation before a fence but won admission after the fence closed the
     * gate.  It may do bounded work, but it can never publish that old slot. */
    const uint64_t admittedGateAfterCAS = atomic_load_explicit(
        &ring->publicationGate,
        memory_order_acquire);
    const uint64_t admittedGeneration = atomic_load_explicit(
        &ring->activeGeneration,
        memory_order_seq_cst);
    if (TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(admittedGateAfterCAS) != sampledEpoch ||
        admittedGeneration != sampledGeneration || admittedGeneration == 0u) {
        return false;
    }
    return true;
}

static void tars_release_publication(TarsRealtimeAudioRing *ring)
{
    if (ring != NULL) {
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
    }
}

/* Absolute ring cursors are never allowed to wrap.  The modular comparison
 * is retained for a bounded half-range only; production marks the ring
 * exhausted at UINT64_MAX before publishing or consuming another slot. */
static bool tars_cursor_at_or_after(uint64_t current, uint64_t boundary)
{
    const uint64_t delta = current - boundary;
    return current == boundary || delta < (UINT64_MAX / 2u) + 1u;
}

static bool tars_overflow_boundary_pending(const TarsRealtimeAudioRing *ring,
                                           uint64_t consumerReadIndex)
{
    if (ring == NULL) {
        return false;
    }
    const uint64_t metadataRead = atomic_load_explicit(
        &ring->overflowMetadataReadIndex,
        memory_order_relaxed);
    const uint64_t metadataWrite = atomic_load_explicit(
        &ring->overflowMetadataWriteIndex,
        memory_order_acquire);
    if (metadataRead == metadataWrite) {
        return false;
    }
    const TarsRealtimeOverflowBoundary boundary = ring->overflowMetadata[
        metadataRead % TARS_REALTIME_MAX_OVERFLOW_EPISODES].boundary;
    return tars_cursor_at_or_after(consumerReadIndex, boundary.producerWriteIndex);
}

static void tars_record_overflow_boundary(TarsRealtimeAudioRing *ring,
                                          uint64_t producerWriteIndex,
                                          uint64_t producerReadIndex)
{
    if (producerWriteIndex < producerReadIndex) {
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowMetadataDrops);
        return;
    }
    const uint64_t write = atomic_load_explicit(&ring->overflowMetadataWriteIndex, memory_order_relaxed);
    const uint64_t read = atomic_load_explicit(&ring->overflowMetadataReadIndex, memory_order_acquire);
    if (write == UINT64_MAX || write < read) {
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowMetadataDrops);
        return;
    }
    if (write - read >= (uint64_t)TARS_REALTIME_MAX_OVERFLOW_EPISODES) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowMetadataDrops);
        return;
    }
    TarsRealtimeOverflowMetadataSlot *slot =
        &ring->overflowMetadata[write % TARS_REALTIME_MAX_OVERFLOW_EPISODES];
    slot->boundary.producerWriteIndex = producerWriteIndex;
    slot->boundary.producerReadIndex = producerReadIndex;
    slot->boundary.episodeNumber = write + 1u;
    const uint64_t retained = producerWriteIndex - producerReadIndex;
    slot->boundary.retainedSlotCount = retained > UINT32_MAX ? UINT32_MAX : (uint32_t)retained;
    atomic_store_explicit(&ring->overflowMetadataWriteIndex, write + 1u, memory_order_release);
}

static void tars_close_publication_gate(TarsRealtimeAudioRing *ring)
{
    uint64_t gate = atomic_load_explicit(&ring->publicationGate, memory_order_acquire);
    while ((gate & TARS_REALTIME_PUBLICATION_CLOSED) == 0u &&
           !atomic_compare_exchange_weak_explicit(&ring->publicationGate,
                                                  &gate,
                                                  gate | TARS_REALTIME_PUBLICATION_CLOSED,
                                                  memory_order_acq_rel,
                                                  memory_order_acquire)) {
    }
}

static void tars_wait_for_publications(TarsRealtimeAudioRing *ring)
{
    while ((atomic_load_explicit(&ring->publicationGate, memory_order_acquire) &
            TARS_REALTIME_PUBLICATION_COUNT_MASK) != 0u) {
    }
}

/* SetGeneration is a non-realtime ownership boundary.  Once publication
 * admission has quiesced, every queued slot belongs to the retired graph and
 * must be zeroized before a nonzero generation can reuse the ring.  The
 * count guard keeps a malformed fixture cursor from turning this bounded
 * cleanup into an unbounded loop. */
static void tars_zero_slot(TarsRealtimeAudioSlot *slot, uint32_t capacity);

static void tars_retire_queued_slots(TarsRealtimeAudioRing *ring)
{
    if (ring == NULL || ring->slots == NULL || ring->slotCount == 0u) {
        return;
    }
    const uint64_t read = atomic_load_explicit(&ring->readIndex, memory_order_acquire);
    const uint64_t write = atomic_load_explicit(&ring->writeIndex, memory_order_acquire);
    if (write < read || write == UINT64_MAX || write - read > (uint64_t)ring->slotCount) {
        /* A malformed or exhausted absolute cursor cannot identify a bounded
         * retained interval.  Zero every fixed slot and fail closed. */
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        for (uint32_t index = 0u; index < ring->slotCount; ++index) {
            tars_zero_slot(&ring->slots[index], ring->slotCapacity);
        }
    } else {
        for (uint64_t cursor = read; cursor < write; ++cursor) {
            tars_zero_slot(&ring->slots[cursor % ring->slotCount], ring->slotCapacity);
        }
    }
    /* The queue is retired as a whole.  Keep the absolute producer cursor so
     * a new generation cannot invert order, but make the consumer empty. */
    atomic_store_explicit(&ring->readIndex, write, memory_order_release);
}

static TarsRealtimeRingRetirement tars_set_generation_nonrealtime(
    TarsRealtimeAudioRing *ring,
    uint64_t generation,
    bool terminalRetirement)
{
    TarsRealtimeRingRetirement retirement = {
        .hadRetainedSlots = false,
        .firstReadIndex = 0u,
        .writeIndex = 0u,
        .retainedSlotCount = 0u,
        .hadAdmittedLoss = false,
        .admittedLossReadIndex = 0u,
        .admittedLossWriteIndex = 0u
    };
    if (ring == NULL) {
        return retirement;
    }
    if (!terminalRetirement && generation == 0u &&
        atomic_load_explicit(&ring->terminalRetirementCompleted, memory_order_acquire)) {
        /* Terminal retirement already performed the close/wait/purge edge.
         * A later generic teardown or destroy must not refence that retired
         * ring or manufacture a second terminal transition. */
        return retirement;
    }
    if (!terminalRetirement && generation != 0u) {
        atomic_store_explicit(&ring->terminalRetirementCompleted, false, memory_order_release);
        atomic_store_explicit(&ring->terminalRetirementClaimed, false, memory_order_release);
    }
    if (terminalRetirement && ring->terminalRetirementHookForTesting != NULL) {
        ring->terminalRetirementHookForTesting(ring->terminalRetirementContextForTesting);
    }
    tars_close_publication_gate(ring);
    atomic_store_explicit(&ring->activeGeneration, 0u, memory_order_seq_cst);
    atomic_store_explicit(&ring->publicationFenceStartedForTesting, true, memory_order_release);
    if (ring->publicationFenceHookForTesting != NULL) {
        ring->publicationFenceHookForTesting(ring->publicationFenceContextForTesting);
    }
    tars_wait_for_publications(ring);
    const uint64_t read = atomic_load_explicit(&ring->readIndex, memory_order_acquire);
    const uint64_t write = atomic_load_explicit(&ring->writeIndex, memory_order_acquire);
    retirement.firstReadIndex = read;
    retirement.writeIndex = write;
    if (write < read || write == UINT64_MAX || write - read > (uint64_t)ring->slotCount) {
        /* A malformed/exhausted cursor cannot identify a bounded interval.
         * Report conservative raw loss before the existing fail-closed purge
         * so terminal failure still emits evidence instead of silently
         * discarding an admitted callback. */
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        retirement.hadRetainedSlots = write != read;
        retirement.retainedSlotCount = retirement.hadRetainedSlots ? UINT32_MAX : 0u;
    } else {
        const uint64_t retained = write - read;
        retirement.hadRetainedSlots = retained != 0u;
        retirement.retainedSlotCount = retained > UINT32_MAX ? UINT32_MAX : (uint32_t)retained;
    }
    if (terminalRetirement &&
        atomic_load_explicit(&ring->terminalRetirementHadAdmittedLoss, memory_order_acquire)) {
        retirement.hadAdmittedLoss = true;
        retirement.admittedLossReadIndex = atomic_load_explicit(
            &ring->terminalRetirementLossReadIndex,
            memory_order_acquire);
        retirement.admittedLossWriteIndex = atomic_load_explicit(
            &ring->terminalRetirementLossWriteIndex,
            memory_order_acquire);
    }
    tars_retire_queued_slots(ring);
    /* Retiring a generation also retires every producer-side boundary.  The
     * metadata array is fixed-size and non-realtime here, so clear its bytes
     * as well as resetting both cursors before publishing a new generation. */
    (void)memset_s(
        ring->overflowMetadata,
        sizeof(ring->overflowMetadata),
        0,
        sizeof(ring->overflowMetadata));
    atomic_store_explicit(&ring->overflowMetadataReadIndex, 0u, memory_order_release);
    atomic_store_explicit(&ring->overflowMetadataWriteIndex, 0u, memory_order_release);
    atomic_store_explicit(&ring->overflowEpisodeActive, false, memory_order_release);
    const uint64_t previousGate = atomic_load_explicit(&ring->publicationGate, memory_order_acquire);
    const uint64_t previousEpoch = TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(previousGate);
    if (previousEpoch == TARS_REALTIME_PUBLICATION_EPOCH_MAX) {
        /* Publication tokens are deliberately never recycled.  A process
         * that somehow fences/reopens a ring four billion times must stop
         * before token ambiguity instead of reintroducing an ABA window. */
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        atomic_store_explicit(
            &ring->publicationGate,
            tars_publication_gate_for(previousEpoch, 0u, true),
            memory_order_release);
        return retirement;
    }
    const uint64_t nextEpoch = previousEpoch + 1u;
    atomic_store_explicit(
        &ring->publicationGate,
        tars_publication_gate_for(nextEpoch, 0u, true),
        memory_order_release);
    if (generation != 0u &&
        atomic_load_explicit(&ring->cursorOverflow, memory_order_acquire) == 0u) {
        atomic_store_explicit(&ring->activeGeneration, generation, memory_order_seq_cst);
        atomic_store_explicit(
            &ring->publicationGate,
            tars_publication_gate_for(nextEpoch, 0u, false),
            memory_order_release);
        atomic_store_explicit(&ring->publicationFenceStartedForTesting, false, memory_order_release);
    }
    return retirement;
}

static void tars_zero_slot(TarsRealtimeAudioSlot *slot, uint32_t capacity)
{
    if (slot == NULL) {
        return;
    }
    if (slot->bytes != NULL && capacity != 0u) {
        (void)memset_s(slot->bytes, (rsize_t)capacity, 0, (rsize_t)capacity);
    }
    slot->bufferCount = 0u;
    slot->totalBytes = 0u;
    slot->sampleTime = 0.0;
    slot->hostTime = 0u;
    slot->timestampFlags = 0u;
    slot->generation = 0u;
    memset(slot->bufferByteSizes, 0, sizeof(slot->bufferByteSizes));
    memset(slot->bufferChannels, 0, sizeof(slot->bufferChannels));
    memset(&slot->asbd, 0, sizeof(slot->asbd));
}

static TarsRealtimeDescriptorClass tars_classify_and_copy(
    TarsRealtimeAudioRing *ring,
    const TarsRealtimeInputDescriptor *descriptor)
{
    if (ring == NULL || descriptor == NULL) {
        return TARS_REALTIME_DESCRIPTOR_MALFORMED;
    }
    /* Admission is the first ring access after the caller's descriptor
     * pointer.  Every subsequent metadata, slot, and diagnostic-counter
     * access is therefore covered by the same bounded publication lifetime
     * that the non-realtime fence closes before destroying/reopening a ring. */
    bool publicationReserved = false;
    if (!tars_try_admit_publication(ring, descriptor->generation, &publicationReserved)) {
        if (publicationReserved) {
            TARS_REALTIME_SATURATING_INCREMENT(ring->callbackArrivals);
            TARS_REALTIME_SATURATING_INCREMENT(ring->staleGenerationArrivals);
            tars_release_publication(ring);
        }
        return TARS_REALTIME_DESCRIPTOR_STALE_GENERATION;
    }
    /* Every counter and descriptor/slot access below is covered by the same
     * bounded admission as the slot lifetime.  In particular, a non-realtime
     * fence cannot return and destroy the ring while this callback is still
     * recording its arrival or classifying malformed input. */
    TARS_REALTIME_SATURATING_INCREMENT(ring->callbackArrivals);
    if (descriptor->buffers == NULL) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_MALFORMED;
    }
    const TarsRealtimeASBDSnapshot incomingASBD = descriptor->asbd;
    const TarsRealtimeASBDSnapshot expectedASBD = ring->expectedASBD;
    if (incomingASBD.sampleRate != expectedASBD.sampleRate ||
        incomingASBD.formatID != expectedASBD.formatID ||
        incomingASBD.formatFlags != expectedASBD.formatFlags ||
        incomingASBD.bytesPerPacket != expectedASBD.bytesPerPacket ||
        incomingASBD.framesPerPacket != expectedASBD.framesPerPacket ||
        incomingASBD.bytesPerFrame != expectedASBD.bytesPerFrame ||
        incomingASBD.channelsPerFrame != expectedASBD.channelsPerFrame ||
        incomingASBD.bitsPerChannel != expectedASBD.bitsPerChannel ||
        incomingASBD.isInterleaved != expectedASBD.isInterleaved) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_MALFORMED;
    }
    if (descriptor->bufferCount == 0u || descriptor->bufferCount > TARS_REALTIME_MAX_BUFFERS) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_MALFORMED;
    }

    bool shapeValid = true;
    if (ring->expectedInterleaved) {
        shapeValid = descriptor->bufferCount == 1u &&
            descriptor->buffers[0].channels == ring->expectedChannels;
    } else {
        shapeValid = descriptor->bufferCount == ring->expectedChannels;
        for (uint32_t index = 0u; shapeValid && index < descriptor->bufferCount; ++index) {
            shapeValid = descriptor->buffers[index].channels == 1u;
        }
    }
    if (!shapeValid) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_MALFORMED;
    }

    size_t totalBytes = 0u;
    bool allEmpty = true;
    bool layoutValid = ring->expectedASBD.bytesPerFrame != 0u;
    uint32_t firstPlaneBytes = 0u;
    for (uint32_t index = 0u; index < descriptor->bufferCount; ++index) {
        const TarsRealtimeInputBuffer input = descriptor->buffers[index];
        if (ring->expectedASBD.bytesPerFrame == 0u ||
            input.byteSize % ring->expectedASBD.bytesPerFrame != 0u) {
            layoutValid = false;
        }
        if (!ring->expectedInterleaved) {
            if (index == 0u) {
                firstPlaneBytes = input.byteSize;
            } else if (input.byteSize != firstPlaneBytes) {
                layoutValid = false;
            }
        }
        if (input.byteSize != 0u) {
            allEmpty = false;
            if (input.data == NULL) {
                TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
                tars_release_publication(ring);
                return TARS_REALTIME_DESCRIPTOR_MALFORMED;
            }
        }
        if (input.byteSize > SIZE_MAX - totalBytes) {
            TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
            tars_release_publication(ring);
            return TARS_REALTIME_DESCRIPTOR_MALFORMED;
        }
        totalBytes += (size_t)input.byteSize;
    }
    if (!ring->expectedInterleaved) {
        for (uint32_t index = 0u; index < descriptor->bufferCount; ++index) {
            if ((descriptor->buffers[index].byteSize == 0u) != (firstPlaneBytes == 0u)) {
                layoutValid = false;
            }
        }
    }
    if (!layoutValid) {
            TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_MALFORMED;
    }
    if (allEmpty) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->emptyArrivals);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_EMPTY;
    }
    if (totalBytes > (size_t)ring->slotCapacity || totalBytes > UINT32_MAX) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->capacityRejectedArrivals);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_CAPACITY_REJECTED;
    }

    TARS_REALTIME_SATURATING_INCREMENT(ring->validNonemptyArrivals);
    const uint64_t write = atomic_load_explicit(&ring->writeIndex, memory_order_relaxed);
    const uint64_t read = atomic_load_explicit(&ring->readIndex, memory_order_acquire);
    if (write == UINT64_MAX || write < read) {
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_CURSOR_OVERFLOW;
    }
    if (write - read >= (uint64_t)ring->slotCount) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowCount);
        if (!atomic_exchange_explicit(&ring->overflowEpisodeActive, true, memory_order_acq_rel)) {
            tars_record_overflow_boundary(ring, write, read);
            TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowEpisodes);
        }
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY;
    }

    TarsRealtimeAudioSlot *slot = &ring->slots[write % ring->slotCount];
    size_t offset = 0u;
    slot->bufferCount = descriptor->bufferCount;
    slot->totalBytes = (uint32_t)totalBytes;
    slot->asbd = descriptor->asbd;
    slot->sampleTime = descriptor->sampleTime;
    slot->hostTime = descriptor->hostTime;
    slot->timestampFlags = descriptor->timestampFlags;
    slot->generation = descriptor->generation;
    for (uint32_t index = 0u; index < descriptor->bufferCount; ++index) {
        const TarsRealtimeInputBuffer input = descriptor->buffers[index];
        slot->bufferByteSizes[index] = input.byteSize;
        slot->bufferChannels[index] = input.channels;
        if (input.byteSize != 0u) {
            memcpy(slot->bytes + offset, input.data, (size_t)input.byteSize);
        }
        offset += (size_t)input.byteSize;
    }
    atomic_store_explicit(&ring->overflowEpisodeActive, false, memory_order_release);

    /* Recheck at the publication boundary.  The non-realtime fence waits for
     * every admitted publication, so a store that observes the old
     * generation is complete before the fence returns. */
    if (atomic_exchange_explicit(&ring->fenceBeforeNextPushPublicationForTesting, false, memory_order_acq_rel)) {
        atomic_store_explicit(&ring->activeGeneration, 0u, memory_order_seq_cst);
    }
    const uint64_t publicationGeneration = atomic_load_explicit(&ring->activeGeneration, memory_order_seq_cst);
    const bool publish = publicationGeneration != 0u && publicationGeneration == descriptor->generation;
    if (publish) {
        atomic_store_explicit(&ring->writeIndex, write + 1u, memory_order_release);
        TARS_REALTIME_SATURATING_INCREMENT(ring->enqueuedCount);
    }
    if (!publish) {
        if (ring->staleCleanupHookForTesting != NULL) {
            ring->staleCleanupHookForTesting(ring->staleCleanupContextForTesting);
        }
        tars_zero_slot(slot, ring->slotCapacity);
        TARS_REALTIME_SATURATING_INCREMENT(ring->staleGenerationArrivals);
        /* Keep admission held until stale-slot cleanup and its diagnostic
         * counter update are complete.  A reopened generation must not reuse
         * this slot while the old callback is still clearing it. */
        tars_release_publication(ring);
        return TARS_REALTIME_DESCRIPTOR_STALE_GENERATION;
    }
    tars_release_publication(ring);
    return TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY;
}

TarsRealtimeAudioRing *TarsRealtimeAudioRingCreate(uint32_t slotCount,
                                                    uint32_t slotCapacity,
                                                    const TarsRealtimeASBDSnapshot *expectedASBD,
                                                    uint32_t expectedChannels,
                                                    bool expectedInterleaved,
                                                    uint64_t generation)
{
    if (slotCount == 0u || slotCapacity == 0u || expectedChannels == 0u ||
        expectedChannels > TARS_REALTIME_MAX_BUFFERS) {
        return NULL;
    }
    TarsRealtimeAudioRing *ring = (TarsRealtimeAudioRing *)calloc(1u, sizeof(*ring));
    if (ring == NULL) {
        return NULL;
    }
    ring->slotCount = slotCount;
    ring->slotCapacity = slotCapacity;
    ring->expectedChannels = expectedChannels;
    ring->expectedInterleaved = expectedInterleaved;
    if (expectedASBD != NULL) {
        ring->expectedASBD = *expectedASBD;
    }
    ring->slots = (TarsRealtimeAudioSlot *)calloc((size_t)slotCount, sizeof(*ring->slots));
    if (ring->slots == NULL) {
        free(ring);
        return NULL;
    }
    for (uint32_t index = 0u; index < slotCount; ++index) {
        ring->slots[index].bytes = (uint8_t *)calloc(1u, (size_t)slotCapacity);
        if (ring->slots[index].bytes == NULL) {
            for (uint32_t previous = 0u; previous < index; ++previous) {
                if (ring->slots[previous].bytes != NULL) {
                    (void)memset_s(ring->slots[previous].bytes, (rsize_t)slotCapacity, 0, (rsize_t)slotCapacity);
                    free(ring->slots[previous].bytes);
                }
            }
            free(ring->slots);
            free(ring);
            return NULL;
        }
    }
    /* calloc does not initialize an atomic object according to the C memory
     * model.  Every atomic member is explicitly initialized before the ring
     * can be published to an IOProc or a Swift consumer. */
    atomic_init(&ring->writeIndex, 0u);
    atomic_init(&ring->readIndex, 0u);
    atomic_init(&ring->activeGeneration, generation);
    atomic_init(&ring->callbackArrivals, 0u);
    atomic_init(&ring->validNonemptyArrivals, 0u);
    atomic_init(&ring->emptyArrivals, 0u);
    atomic_init(&ring->malformedArrivals, 0u);
    atomic_init(&ring->capacityRejectedArrivals, 0u);
    atomic_init(&ring->staleGenerationArrivals, 0u);
    atomic_init(&ring->ringOverflowCount, 0u);
    atomic_init(&ring->ringOverflowEpisodes, 0u);
    atomic_init(&ring->enqueuedCount, 0u);
    atomic_init(&ring->poppedCount, 0u);
    atomic_init(&ring->ringOverflowMetadataDrops, 0u);
    atomic_init(&ring->cursorOverflow, 0u);
    atomic_init(&ring->overflowEpisodeActive, false);
    atomic_init(&ring->fenceBeforeNextPushPublicationForTesting, false);
    atomic_init(&ring->publicationGate, tars_publication_gate_for(1u, 0u, generation == 0u));
    atomic_init(&ring->publicationFenceStartedForTesting, generation == 0u);
    atomic_init(&ring->terminalRetirementActive, false);
    atomic_init(&ring->terminalRetirementHadAdmittedLoss, false);
    atomic_init(&ring->terminalRetirementLossReadIndex, 0u);
    atomic_init(&ring->terminalRetirementLossWriteIndex, 0u);
    atomic_init(&ring->holdNextFinalPublicationForTesting, false);
    atomic_init(&ring->heldFinalPublicationReadyForTesting, false);
    atomic_init(&ring->heldFinalPublicationWriteIndexForTesting, 0u);
    atomic_init(&ring->heldFinalPublicationReadIndexForTesting, 0u);
    atomic_init(&ring->heldFinalPublicationByteCountForTesting, 0u);
    atomic_init(&ring->heldFinalPublicationGenerationForTesting, 0u);
    atomic_init(&ring->terminalRetirementCompleted, false);
    atomic_init(&ring->terminalRetirementClaimed, false);
    atomic_init(&ring->overflowMetadataWriteIndex, 0u);
    atomic_init(&ring->overflowMetadataReadIndex, 0u);
    return ring;
}

void TarsRealtimeAudioRingDestroy(TarsRealtimeAudioRing *ring)
{
    if (ring == NULL) {
        return;
    }
    if (!atomic_load_explicit(&ring->terminalRetirementCompleted, memory_order_acquire)) {
        (void)tars_set_generation_nonrealtime(ring, 0u, false);
    }
    if (ring->slots != NULL) {
        for (uint32_t index = 0u; index < ring->slotCount; ++index) {
            TarsRealtimeAudioSlot *slot = &ring->slots[index];
            if (slot->bytes != NULL) {
                tars_zero_slot(slot, ring->slotCapacity);
                if (ring->zeroizationHook != NULL) {
                    ring->zeroizationHook(slot->bytes, (size_t)ring->slotCapacity, ring->zeroizationContext);
                }
                (void)memset_s(slot->bytes, (rsize_t)ring->slotCapacity, 0, (rsize_t)ring->slotCapacity);
                free(slot->bytes);
                slot->bytes = NULL;
            }
        }
        (void)memset_s(ring->slots, (rsize_t)((size_t)ring->slotCount * sizeof(*ring->slots)), 0,
                       (rsize_t)((size_t)ring->slotCount * sizeof(*ring->slots)));
        free(ring->slots);
    }
    (void)memset_s(ring, (rsize_t)sizeof(*ring), 0, (rsize_t)sizeof(*ring));
    free(ring);
}

void TarsRealtimeAudioRingSetGeneration(TarsRealtimeAudioRing *ring, uint64_t generation)
{
    if (ring != NULL) {
        (void)tars_set_generation_nonrealtime(ring, generation, false);
    }
}

void TarsRealtimeAudioRingSetTerminalRetirementHookForTesting(
    TarsRealtimeAudioRing *ring,
    TarsRealtimePublicationFenceHook hook,
    void *context)
{
    if (ring != NULL) {
        ring->terminalRetirementHookForTesting = hook;
        ring->terminalRetirementContextForTesting = context;
    }
}

void TarsRealtimeAudioRingHoldNextFinalPublicationForTesting(
    TarsRealtimeAudioRing *ring)
{
    if (ring != NULL) {
        atomic_store_explicit(
            &ring->holdNextFinalPublicationForTesting,
            true,
            memory_order_release);
    }
}

bool TarsRealtimeAudioRingPublicationPauseReadyForTesting(
    const TarsRealtimeAudioRing *ring)
{
    return ring != NULL && atomic_load_explicit(
        &ring->heldFinalPublicationReadyForTesting,
        memory_order_acquire);
}

void TarsRealtimeAudioRingResumeHeldPublicationForTesting(
    TarsRealtimeAudioRing *ring)
{
    if (ring == NULL) {
        return;
    }
    bool expectedReady = true;
    if (!atomic_compare_exchange_strong_explicit(
            &ring->heldFinalPublicationReadyForTesting,
            &expectedReady,
            false,
            memory_order_acq_rel,
            memory_order_acquire)) {
        return;
    }

    /* This is a non-realtime test-control completion of the production
     * callback's held publication.  The callback itself returned immediately
     * after the bounded copy, without waiting, spinning, self-fencing, or
     * releasing its admission.  Terminal retirement can therefore close the
     * gate and wait here exactly as it does for a live callback. */
    const uint64_t write = atomic_load_explicit(
        &ring->heldFinalPublicationWriteIndexForTesting,
        memory_order_acquire);
    const uint64_t read = atomic_load_explicit(
        &ring->heldFinalPublicationReadIndexForTesting,
        memory_order_acquire);
    const uint64_t generation = atomic_load_explicit(
        &ring->heldFinalPublicationGenerationForTesting,
        memory_order_acquire);
    const uint32_t totalBytes = atomic_load_explicit(
        &ring->heldFinalPublicationByteCountForTesting,
        memory_order_acquire);
    const uint64_t publicationGeneration = atomic_load_explicit(
        &ring->activeGeneration,
        memory_order_seq_cst);
    const bool publish = publicationGeneration != 0u &&
        publicationGeneration == generation &&
        write != UINT64_MAX && write >= read &&
        write - read < (uint64_t)ring->slotCount;
    if (publish) {
        atomic_store_explicit(&ring->writeIndex, write + 1u, memory_order_release);
        TARS_REALTIME_SATURATING_INCREMENT(ring->enqueuedCount);
    } else {
        if (atomic_load_explicit(&ring->terminalRetirementActive, memory_order_acquire)) {
            bool expectedLoss = false;
            if (atomic_compare_exchange_strong_explicit(
                    &ring->terminalRetirementHadAdmittedLoss,
                    &expectedLoss,
                    true,
                    memory_order_acq_rel,
                    memory_order_acquire)) {
                atomic_store_explicit(
                    &ring->terminalRetirementLossReadIndex,
                    read,
                    memory_order_release);
                atomic_store_explicit(
                    &ring->terminalRetirementLossWriteIndex,
                    write,
                    memory_order_release);
            }
        }
        if (ring->slots != NULL && ring->slotCount != 0u) {
            TarsRealtimeAudioSlot *slot = &ring->slots[write % ring->slotCount];
            volatile uint8_t *zeroBytes = slot->bytes;
            const uint32_t boundedBytes = totalBytes > ring->slotCapacity
                ? ring->slotCapacity
                : totalBytes;
            for (uint32_t zeroIndex = 0u; zeroIndex < boundedBytes; ++zeroIndex) {
                zeroBytes[zeroIndex] = 0u;
            }
            slot->bufferCount = 0u;
            slot->totalBytes = 0u;
            slot->sampleTime = 0.0;
            slot->hostTime = 0u;
            slot->timestampFlags = 0u;
            slot->generation = 0u;
            for (uint32_t zeroIndex = 0u; zeroIndex < TARS_REALTIME_MAX_BUFFERS; ++zeroIndex) {
                slot->bufferByteSizes[zeroIndex] = 0u;
                slot->bufferChannels[zeroIndex] = 0u;
            }
            slot->asbd.sampleRate = 0.0;
            slot->asbd.formatID = 0u;
            slot->asbd.formatFlags = 0u;
            slot->asbd.bytesPerPacket = 0u;
            slot->asbd.framesPerPacket = 0u;
            slot->asbd.bytesPerFrame = 0u;
            slot->asbd.channelsPerFrame = 0u;
            slot->asbd.bitsPerChannel = 0u;
            slot->asbd.isInterleaved = 0u;
        }
        TARS_REALTIME_SATURATING_INCREMENT(ring->staleGenerationArrivals);
    }
    atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
}

TarsRealtimeRingRetirement TarsRealtimeAudioRingRetireForTerminalFailure(
    TarsRealtimeAudioRing *ring)
{
    /* This is deliberately the same close/wait/purge ownership edge as a
     * generation transition.  Its return value is captured before purge so a
     * callback admitted before the close cannot disappear without a terminal
     * raw-ring boundary. */
    if (ring == NULL) {
        return tars_set_generation_nonrealtime(NULL, 0u, true);
    }
    bool expectedClaimed = false;
    if (!atomic_compare_exchange_strong_explicit(
            &ring->terminalRetirementClaimed,
            &expectedClaimed,
            true,
            memory_order_acq_rel,
            memory_order_acquire)) {
        /* Terminal retirement is a one-shot ownership transition.  A
         * duplicate caller must not invoke the hook, close/fence a second
         * time, or manufacture another publication-boundary observation. */
        return (TarsRealtimeRingRetirement){
            .hadRetainedSlots = false,
            .firstReadIndex = 0u,
            .writeIndex = 0u,
            .retainedSlotCount = 0u,
            .hadAdmittedLoss = false,
            .admittedLossReadIndex = 0u,
            .admittedLossWriteIndex = 0u
        };
    }
    atomic_store_explicit(&ring->terminalRetirementLossReadIndex, 0u, memory_order_release);
    atomic_store_explicit(&ring->terminalRetirementLossWriteIndex, 0u, memory_order_release);
    atomic_store_explicit(&ring->terminalRetirementHadAdmittedLoss, false, memory_order_release);
    /* Publish the terminal-retirement epoch before closing admission.  A
     * callback already admitted in the old epoch can therefore account its
     * copied-but-discarded payload before it releases the gate count that the
     * retirement edge waits on. */
    atomic_store_explicit(&ring->terminalRetirementActive, true, memory_order_release);
    TarsRealtimeRingRetirement retirement = tars_set_generation_nonrealtime(ring, 0u, true);
    retirement.hadAdmittedLoss = atomic_load_explicit(
        &ring->terminalRetirementHadAdmittedLoss,
        memory_order_acquire);
    if (retirement.hadAdmittedLoss) {
        retirement.admittedLossReadIndex = atomic_load_explicit(
            &ring->terminalRetirementLossReadIndex,
            memory_order_acquire);
        retirement.admittedLossWriteIndex = atomic_load_explicit(
            &ring->terminalRetirementLossWriteIndex,
            memory_order_acquire);
    }
    atomic_store_explicit(&ring->terminalRetirementActive, false, memory_order_release);
    atomic_store_explicit(&ring->terminalRetirementCompleted, true, memory_order_release);
    return retirement;
}

uint64_t TarsRealtimeAudioRingGeneration(const TarsRealtimeAudioRing *ring)
{
    return ring == NULL ? 0u : atomic_load_explicit(&ring->activeGeneration, memory_order_seq_cst);
}

uint32_t TarsRealtimeAudioRingSlotCapacity(const TarsRealtimeAudioRing *ring)
{
    return ring == NULL ? 0u : ring->slotCapacity;
}

uint32_t TarsRealtimeAudioRingSlotCount(const TarsRealtimeAudioRing *ring)
{
    return ring == NULL ? 0u : ring->slotCount;
}

uint64_t TarsRealtimeAudioRingReadIndex(const TarsRealtimeAudioRing *ring)
{
    return ring == NULL ? 0u : atomic_load_explicit(&ring->readIndex, memory_order_acquire);
}

void TarsRealtimeAudioRingFenceBeforeNextPushPublicationForTesting(TarsRealtimeAudioRing *ring)
{
    if (ring != NULL) {
        atomic_store_explicit(&ring->fenceBeforeNextPushPublicationForTesting, true, memory_order_release);
    }
}

void TarsRealtimeAudioRingSetStaleCleanupHookForTesting(TarsRealtimeAudioRing *ring,
                                                        TarsRealtimeStaleCleanupHook hook,
                                                        void *context)
{
    if (ring != NULL) {
        ring->staleCleanupHookForTesting = hook;
        ring->staleCleanupContextForTesting = context;
    }
}

void TarsRealtimeAudioRingSetPublicationFenceHookForTesting(TarsRealtimeAudioRing *ring,
                                                             TarsRealtimePublicationFenceHook hook,
                                                             void *context)
{
    if (ring != NULL) {
        ring->publicationFenceHookForTesting = hook;
        ring->publicationFenceContextForTesting = context;
    }
}

bool TarsRealtimeAudioRingTryBeginPublicationForTesting(TarsRealtimeAudioRing *ring,
                                                         uint64_t generation)
{
    bool reserved = false;
    const bool admitted = tars_try_admit_publication(ring, generation, &reserved);
    if (!admitted && reserved) {
        tars_release_publication(ring);
    }
    return admitted;
}

void TarsRealtimeAudioRingEndPublicationForTesting(TarsRealtimeAudioRing *ring)
{
    tars_release_publication(ring);
}

bool TarsRealtimeAudioRingPublicationFenceStartedForTesting(const TarsRealtimeAudioRing *ring)
{
    return ring != NULL &&
        atomic_load_explicit(&ring->publicationFenceStartedForTesting, memory_order_acquire);
}

uint64_t TarsRealtimeAudioRingLoadPublicationAdmissionTokenForTesting(
    const TarsRealtimeAudioRing *ring)
{
    return ring == NULL
        ? 0u
        : atomic_load_explicit(&ring->publicationGate, memory_order_acquire);
}

bool TarsRealtimeAudioRingTryCommitPublicationAdmissionTokenForTesting(
    TarsRealtimeAudioRing *ring,
    uint64_t sampledToken,
    uint64_t generation)
{
    if (ring == NULL || sampledToken == 0u || generation == 0u ||
        (sampledToken & TARS_REALTIME_PUBLICATION_CLOSED) != 0u ||
        (sampledToken & TARS_REALTIME_PUBLICATION_COUNT_MASK) == TARS_REALTIME_PUBLICATION_COUNT_MASK) {
        return false;
    }
    uint64_t expected = sampledToken;
    const uint64_t admittedToken = sampledToken + 1u;
    if (!atomic_compare_exchange_strong_explicit(
            &ring->publicationGate,
            &expected,
            admittedToken,
            memory_order_acq_rel,
            memory_order_acquire)) {
        return false;
    }
    const uint64_t activeGeneration = atomic_load_explicit(
        &ring->activeGeneration,
        memory_order_seq_cst);
    const uint64_t currentToken = atomic_load_explicit(
        &ring->publicationGate,
        memory_order_acquire);
    if (TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(currentToken) !=
            TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(sampledToken) ||
        activeGeneration != generation || activeGeneration == 0u) {
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return false;
    }
    return true;
}

TarsRealtimeDescriptorClass TarsRealtimeAudioRingPush(TarsRealtimeAudioRing *ring,
                                                      const TarsRealtimeInputDescriptor *descriptor)
{
    return tars_classify_and_copy(ring, descriptor);
}

static int tars_pop(TarsRealtimeAudioRing *ring,
                    uint64_t producerWriteLimit,
                    bool bounded,
                    TarsRealtimeSlotOutput *output)
{
    if (ring == NULL || output == NULL) {
        return 0;
    }
    const uint64_t read = atomic_load_explicit(&ring->readIndex, memory_order_relaxed);
    const uint64_t write = atomic_load_explicit(&ring->writeIndex, memory_order_acquire);
    /* Recheck the producer-side FIFO after acquiring the write snapshot.  A
     * producer can publish an overflow marker and a post-boundary frame
     * between Swift's metadata poll and this pop.  Returning -2 at the exact
     * boundary makes the marker an uncrossable C-side edge; Swift must poll it
     * before any slot at or after the captured producer cursor. */
    if (tars_overflow_boundary_pending(ring, read)) {
        return -2;
    }
    if (read == UINT64_MAX) {
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        return -1;
    }
    if (read == write || (bounded && read >= producerWriteLimit)) {
        return 0;
    }
    TarsRealtimeAudioSlot *slot = &ring->slots[read % ring->slotCount];
    if (output->bytes == NULL || output->byteCapacity < slot->totalBytes) {
        return -1;
    }
    output->bufferCount = slot->bufferCount;
    output->totalBytes = slot->totalBytes;
    output->asbd = slot->asbd;
    output->sampleTime = slot->sampleTime;
    output->hostTime = slot->hostTime;
    output->timestampFlags = slot->timestampFlags;
    output->generation = slot->generation;
    memcpy(output->bufferByteSizes, slot->bufferByteSizes, sizeof(output->bufferByteSizes));
    memcpy(output->bufferChannels, slot->bufferChannels, sizeof(output->bufferChannels));
    if (slot->totalBytes != 0u) {
        memcpy(output->bytes, slot->bytes, (size_t)slot->totalBytes);
    }
    tars_zero_slot(slot, ring->slotCapacity);
    atomic_store_explicit(&ring->readIndex, read + 1u, memory_order_release);
    TARS_REALTIME_SATURATING_INCREMENT(ring->poppedCount);
    return 1;
}

int TarsRealtimeAudioRingPop(TarsRealtimeAudioRing *ring, TarsRealtimeSlotOutput *output)
{
    return tars_pop(ring, 0u, false, output);
}

int TarsRealtimeAudioRingPopThrough(TarsRealtimeAudioRing *ring,
                                    uint64_t producerWriteIndex,
                                    TarsRealtimeSlotOutput *output)
{
    return tars_pop(ring, producerWriteIndex, true, output);
}

bool TarsRealtimeAudioRingSlotIsZeroizedForTesting(const TarsRealtimeAudioRing *ring,
                                                   uint32_t slotIndex)
{
    if (ring == NULL || ring->slots == NULL || slotIndex >= ring->slotCount) {
        return false;
    }
    const TarsRealtimeAudioSlot *slot = &ring->slots[slotIndex];
    if (slot->bufferCount != 0u || slot->totalBytes != 0u || slot->sampleTime != 0.0 ||
        slot->hostTime != 0u || slot->timestampFlags != 0u || slot->generation != 0u) {
        return false;
    }
    for (uint32_t index = 0u; index < TARS_REALTIME_MAX_BUFFERS; ++index) {
        if (slot->bufferByteSizes[index] != 0u || slot->bufferChannels[index] != 0u) {
            return false;
        }
    }
    if (slot->asbd.sampleRate != 0.0 || slot->asbd.formatID != 0u ||
        slot->asbd.formatFlags != 0u || slot->asbd.bytesPerPacket != 0u ||
        slot->asbd.framesPerPacket != 0u || slot->asbd.bytesPerFrame != 0u ||
        slot->asbd.channelsPerFrame != 0u || slot->asbd.bitsPerChannel != 0u ||
        slot->asbd.isInterleaved != 0u) {
        return false;
    }
    if (slot->bytes == NULL) {
        return false;
    }
    for (uint32_t index = 0u; index < ring->slotCapacity; ++index) {
        if (slot->bytes[index] != 0u) {
            return false;
        }
    }
    return true;
}

bool TarsRealtimeAudioRingOverflowMetadataIsZeroizedForTesting(
    const TarsRealtimeAudioRing *ring)
{
    if (ring == NULL) {
        return false;
    }
    /* The metadata slots are producer-owned and deliberately non-atomic.  A
     * test inspector must therefore take the same close/admission ownership
     * edge as teardown before reading them; this is safe even if a fixture
     * calls the inspector while an IOProc is active. */
    TarsRealtimeAudioRing *quiescedRing = (TarsRealtimeAudioRing *)ring;
    tars_close_publication_gate(quiescedRing);
    tars_wait_for_publications(quiescedRing);
    const uint8_t *metadataBytes = (const uint8_t *)ring->overflowMetadata;
    for (size_t index = 0u; index < sizeof(ring->overflowMetadata); ++index) {
        if (metadataBytes[index] != 0u) {
            return false;
        }
    }
    return true;
}

TarsRealtimeCounters TarsRealtimeAudioRingSnapshot(const TarsRealtimeAudioRing *ring)
{
    TarsRealtimeCounters counters = {0};
    if (ring == NULL) {
        return counters;
    }
    counters.callbackArrivals = atomic_load_explicit(&ring->callbackArrivals, memory_order_acquire);
    counters.validNonemptyArrivals = atomic_load_explicit(&ring->validNonemptyArrivals, memory_order_acquire);
    counters.emptyArrivals = atomic_load_explicit(&ring->emptyArrivals, memory_order_acquire);
    counters.malformedArrivals = atomic_load_explicit(&ring->malformedArrivals, memory_order_acquire);
    counters.capacityRejectedArrivals = atomic_load_explicit(&ring->capacityRejectedArrivals, memory_order_acquire);
    counters.staleGenerationArrivals = atomic_load_explicit(&ring->staleGenerationArrivals, memory_order_acquire);
    counters.ringOverflowCount = atomic_load_explicit(&ring->ringOverflowCount, memory_order_acquire);
    counters.ringOverflowEpisodes = atomic_load_explicit(&ring->ringOverflowEpisodes, memory_order_acquire);
    counters.enqueuedCount = atomic_load_explicit(&ring->enqueuedCount, memory_order_acquire);
    counters.poppedCount = atomic_load_explicit(&ring->poppedCount, memory_order_acquire);
    counters.ringOverflowMetadataDrops = atomic_load_explicit(&ring->ringOverflowMetadataDrops, memory_order_acquire);
    counters.cursorOverflow = atomic_load_explicit(&ring->cursorOverflow, memory_order_acquire);
    return counters;
}

bool TarsRealtimeAudioRingIsEmpty(const TarsRealtimeAudioRing *ring)
{
    if (ring == NULL) {
        return true;
    }
    return atomic_load_explicit(&ring->readIndex, memory_order_acquire) ==
        atomic_load_explicit(&ring->writeIndex, memory_order_acquire);
}

uint32_t TarsRealtimeAudioRingRetainedSlots(const TarsRealtimeAudioRing *ring)
{
    if (ring == NULL) {
        return 0u;
    }
    const uint64_t read = atomic_load_explicit(&ring->readIndex, memory_order_acquire);
    const uint64_t write = atomic_load_explicit(&ring->writeIndex, memory_order_acquire);
    if (write < read) {
        return UINT32_MAX;
    }
    const uint64_t count = write - read;
    return count > UINT32_MAX ? UINT32_MAX : (uint32_t)count;
}

bool TarsRealtimeAudioRingPopOverflowBoundary(TarsRealtimeAudioRing *ring,
                                              TarsRealtimeOverflowBoundary *boundary)
{
    if (ring == NULL || boundary == NULL) {
        return false;
    }
    const uint64_t read = atomic_load_explicit(&ring->overflowMetadataReadIndex, memory_order_relaxed);
    const uint64_t write = atomic_load_explicit(&ring->overflowMetadataWriteIndex, memory_order_acquire);
    if (read == write) {
        return false;
    }
    if (read == UINT64_MAX) {
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        return false;
    }
    *boundary = ring->overflowMetadata[read % TARS_REALTIME_MAX_OVERFLOW_EPISODES].boundary;
    atomic_store_explicit(&ring->overflowMetadataReadIndex, read + 1u, memory_order_release);
    return true;
}

void TarsRealtimeAudioRingSetCursorForTesting(TarsRealtimeAudioRing *ring,
                                              uint64_t writeIndex,
                                              uint64_t readIndex)
{
    if (ring == NULL) {
        return;
    }
    atomic_store_explicit(&ring->writeIndex, writeIndex, memory_order_release);
    atomic_store_explicit(&ring->readIndex, readIndex, memory_order_release);
    atomic_store_explicit(&ring->cursorOverflow, 0u, memory_order_release);
}

void TarsRealtimeAudioRingSetCallbackArrivalsForTesting(TarsRealtimeAudioRing *ring,
                                                        uint64_t value)
{
    if (ring != NULL) {
        atomic_store_explicit(&ring->callbackArrivals, value, memory_order_release);
    }
}

void TarsRealtimeAudioRingSetZeroizationHook(TarsRealtimeAudioRing *ring,
                                             TarsRealtimeZeroizationHook hook,
                                             void *context)
{
    if (ring != NULL) {
        ring->zeroizationHook = hook;
        ring->zeroizationContext = context;
    }
}

OSStatus TarsRealtimeAudioIOProc(AudioObjectID inDevice,
                                 const AudioTimeStamp *inNow,
                                 const AudioBufferList *inInputData,
                                 const AudioTimeStamp *inInputTime,
                                 AudioBufferList *outOutputData,
                                 const AudioTimeStamp *inOutputTime,
                                 void *inClientData) CA_REALTIME_API
{
    (void)inDevice;
    (void)inNow;
    (void)outOutputData;
    (void)inOutputTime;
    TarsRealtimeAudioRing *ring = (TarsRealtimeAudioRing *)inClientData;
    if (ring == NULL) {
        return noErr;
    }

    /* Enter the bounded publication lifetime before reading any ring metadata
     * or slot state.  The callback never waits: one CAS admits it, and the
     * non-realtime fence waits only after closing admission. */
    uint64_t publicationGate = atomic_load_explicit(&ring->publicationGate, memory_order_acquire);
    bool publicationAdmitted = false;
    bool publicationReserved = false;
    if ((publicationGate & TARS_REALTIME_PUBLICATION_CLOSED) == 0u &&
        (publicationGate & TARS_REALTIME_PUBLICATION_COUNT_MASK) != TARS_REALTIME_PUBLICATION_COUNT_MASK) {
        const uint64_t sampledPublicationEpoch =
            TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(publicationGate);
        const uint64_t admittedGate = publicationGate + 1u;
        if (atomic_compare_exchange_strong_explicit(&ring->publicationGate,
                                                    &publicationGate,
                                                    admittedGate,
                                                    memory_order_acq_rel,
                                                    memory_order_acquire)) {
            publicationReserved = true;
            const uint64_t admittedGeneration = atomic_load_explicit(
                &ring->activeGeneration,
                memory_order_seq_cst);
            const uint64_t admittedGateAfterCAS = atomic_load_explicit(
                &ring->publicationGate,
                memory_order_acquire);
            if (TARS_REALTIME_PUBLICATION_EPOCH_FROM_GATE(admittedGateAfterCAS) == sampledPublicationEpoch &&
                admittedGeneration != 0u) {
                publicationAdmitted = true;
            }
        }
    }
    if (!publicationAdmitted) {
        if (publicationReserved) {
            TARS_REALTIME_SATURATING_INCREMENT(ring->callbackArrivals);
            TARS_REALTIME_SATURATING_INCREMENT(ring->staleGenerationArrivals);
            atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        }
        return noErr;
    }
    /* Admission is the lifetime proof for every remaining callback access,
     * including the arrival diagnostic and the active generation value used
     * to classify this invocation. */
    TARS_REALTIME_SATURATING_INCREMENT(ring->callbackArrivals);
    const uint64_t generation = atomic_load_explicit(&ring->activeGeneration, memory_order_seq_cst);
    if (inInputData == NULL || inInputData->mNumberBuffers == 0u ||
        inInputData->mNumberBuffers > TARS_REALTIME_MAX_BUFFERS) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return noErr;
    }

    bool shapeValid = true;
    if (ring->expectedInterleaved) {
        shapeValid = inInputData->mNumberBuffers == 1u &&
            inInputData->mBuffers[0].mNumberChannels == ring->expectedChannels;
    } else {
        shapeValid = inInputData->mNumberBuffers == ring->expectedChannels;
        for (UInt32 index = 0u; shapeValid && index < inInputData->mNumberBuffers; ++index) {
            shapeValid = inInputData->mBuffers[index].mNumberChannels == 1u;
        }
    }
    if (!shapeValid) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return noErr;
    }

    size_t totalBytes = 0u;
    bool allEmpty = true;
    bool malformed = false;
    bool layoutValid = ring->expectedASBD.bytesPerFrame != 0u;
    UInt32 firstPlaneBytes = 0u;
    for (UInt32 index = 0u; index < inInputData->mNumberBuffers; ++index) {
        const AudioBuffer input = inInputData->mBuffers[index];
        if (ring->expectedASBD.bytesPerFrame == 0u ||
            input.mDataByteSize % ring->expectedASBD.bytesPerFrame != 0u) {
            layoutValid = false;
        }
        if (!ring->expectedInterleaved) {
            if (index == 0u) {
                firstPlaneBytes = input.mDataByteSize;
            } else if (input.mDataByteSize != firstPlaneBytes) {
                layoutValid = false;
            }
        }
        if (input.mDataByteSize != 0u) {
            allEmpty = false;
            if (input.mData == NULL) {
                malformed = true;
            }
        }
        if ((size_t)input.mDataByteSize > SIZE_MAX - totalBytes) {
            malformed = true;
        } else {
            totalBytes += (size_t)input.mDataByteSize;
        }
    }
    if (!ring->expectedInterleaved) {
        for (UInt32 index = 0u; index < inInputData->mNumberBuffers; ++index) {
            if ((inInputData->mBuffers[index].mDataByteSize == 0u) != (firstPlaneBytes == 0u)) {
                layoutValid = false;
            }
        }
    }
    if (!layoutValid) {
        malformed = true;
    }
    if (malformed) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->malformedArrivals);
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return noErr;
    }
    if (allEmpty) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->emptyArrivals);
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return noErr;
    }
    if (totalBytes > (size_t)ring->slotCapacity || totalBytes > UINT32_MAX) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->capacityRejectedArrivals);
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return noErr;
    }

    /* This is the last descriptor decision.  Once this counter advances, the
     * callback is structurally valid even when publication finds a full ring. */
    TARS_REALTIME_SATURATING_INCREMENT(ring->validNonemptyArrivals);
    const uint64_t write = atomic_load_explicit(&ring->writeIndex, memory_order_relaxed);
    const uint64_t read = atomic_load_explicit(&ring->readIndex, memory_order_acquire);
    if (write == UINT64_MAX || write < read) {
        TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return noErr;
    }
    if (write - read >= (uint64_t)ring->slotCount) {
        TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowCount);
        if (!atomic_exchange_explicit(&ring->overflowEpisodeActive, true, memory_order_acq_rel)) {
            /* Producer-side FIFO metadata is fixed-size and preallocated at
             * ring creation.  It is written before its release publication;
             * the drain consumes the exact boundary instead of sampling a
             * later retained-slot count.  If this bounded evidence queue is
             * itself full, retain the diagnostic counter so Swift can fail
             * loudly rather than silently inventing a cursor. */
            const uint64_t metadataWrite = atomic_load_explicit(
                &ring->overflowMetadataWriteIndex,
                memory_order_relaxed);
            const uint64_t metadataRead = atomic_load_explicit(
                &ring->overflowMetadataReadIndex,
                memory_order_acquire);
            if (metadataWrite == UINT64_MAX || metadataWrite < metadataRead) {
                TARS_REALTIME_MARK_CURSOR_OVERFLOW(ring);
                TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowMetadataDrops);
            } else if (metadataWrite - metadataRead >= (uint64_t)TARS_REALTIME_MAX_OVERFLOW_EPISODES) {
                TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowMetadataDrops);
            } else {
                TarsRealtimeOverflowMetadataSlot *metadata = &ring->overflowMetadata[
                    metadataWrite % TARS_REALTIME_MAX_OVERFLOW_EPISODES];
                metadata->boundary.producerWriteIndex = write;
                metadata->boundary.producerReadIndex = read;
                metadata->boundary.episodeNumber = metadataWrite + 1u;
                const uint64_t retained = write - read;
                metadata->boundary.retainedSlotCount = retained > UINT32_MAX ? UINT32_MAX : (uint32_t)retained;
                atomic_store_explicit(
                    &ring->overflowMetadataWriteIndex,
                    metadataWrite + 1u,
                    memory_order_release);
            }
            TARS_REALTIME_SATURATING_INCREMENT(ring->ringOverflowEpisodes);
        }
        atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
        return noErr;
    }

    TarsRealtimeAudioSlot *slot = &ring->slots[write % ring->slotCount];
    slot->bufferCount = inInputData->mNumberBuffers;
    slot->totalBytes = (uint32_t)totalBytes;
    slot->asbd = ring->expectedASBD;
    slot->sampleTime = 0.0;
    slot->hostTime = 0u;
    slot->timestampFlags = 0u;
    if (inInputTime != NULL) {
        slot->timestampFlags = inInputTime->mFlags;
        if ((inInputTime->mFlags & kAudioTimeStampSampleTimeValid) != 0u) {
            slot->sampleTime = inInputTime->mSampleTime;
        }
        if ((inInputTime->mFlags & kAudioTimeStampHostTimeValid) != 0u) {
            slot->hostTime = inInputTime->mHostTime;
        }
    }
    slot->generation = generation;
    size_t offset = 0u;
    for (UInt32 index = 0u; index < inInputData->mNumberBuffers; ++index) {
        const AudioBuffer input = inInputData->mBuffers[index];
        slot->bufferByteSizes[index] = input.mDataByteSize;
        slot->bufferChannels[index] = input.mNumberChannels;
        if (input.mDataByteSize != 0u) {
            memcpy(slot->bytes + offset, input.mData, (size_t)input.mDataByteSize);
        }
        offset += (size_t)input.mDataByteSize;
    }

    /* Revalidate at the publication boundary.  A listener/teardown fence can
     * arrive after descriptor validation and while the bounded copy is in
     * progress.  The admitted-publication counter makes the final generation
     * check and write-index store linearizable with the non-realtime fence. */
    atomic_store_explicit(&ring->overflowEpisodeActive, false, memory_order_release);
    if (atomic_exchange_explicit(
            &ring->holdNextFinalPublicationForTesting,
            false,
            memory_order_acq_rel)) {
        /* The test-only control edge keeps this admitted publication alive
         * after the callback returns, immediately before its final
         * generation check.  It does not wait, spin, block, self-fence, or
         * release the admission; a separate non-realtime control actor calls
         * ResumeHeldPublicationForTesting after terminal close is observed. */
        atomic_store_explicit(
            &ring->heldFinalPublicationWriteIndexForTesting,
            write,
            memory_order_release);
        atomic_store_explicit(
            &ring->heldFinalPublicationReadIndexForTesting,
            read,
            memory_order_release);
        atomic_store_explicit(
            &ring->heldFinalPublicationByteCountForTesting,
            (uint32_t)totalBytes,
            memory_order_release);
        atomic_store_explicit(
            &ring->heldFinalPublicationGenerationForTesting,
            generation,
            memory_order_release);
        bool expectedHeld = false;
        if (atomic_compare_exchange_strong_explicit(
                &ring->heldFinalPublicationReadyForTesting,
                &expectedHeld,
                true,
                memory_order_release,
                memory_order_acquire)) {
            return noErr;
        }
    }
    const uint64_t publicationGeneration = atomic_load_explicit(&ring->activeGeneration, memory_order_seq_cst);
    const bool publish = publicationGeneration != 0u && publicationGeneration == generation;
    if (publish) {
        atomic_store_explicit(&ring->writeIndex, write + 1u, memory_order_release);
        TARS_REALTIME_SATURATING_INCREMENT(ring->enqueuedCount);
    }
    if (!publish) {
        /* A terminal close can invalidate an admitted callback after its
         * bounded copy but before writeIndex publication.  Record that
         * absolute raw boundary before any cleanup and before releasing the
         * publication admission; the non-realtime retirement edge will
         * consume this one-shot outcome after it observes the gate quiesce. */
        if (atomic_load_explicit(&ring->terminalRetirementActive, memory_order_acquire)) {
            bool expectedLoss = false;
            if (atomic_compare_exchange_strong_explicit(
                    &ring->terminalRetirementHadAdmittedLoss,
                    &expectedLoss,
                    true,
                    memory_order_acq_rel,
                    memory_order_acquire)) {
                atomic_store_explicit(
                    &ring->terminalRetirementLossReadIndex,
                    read,
                    memory_order_release);
                atomic_store_explicit(
                    &ring->terminalRetirementLossWriteIndex,
                    write,
                    memory_order_release);
            }
        }
        /* The SDK's memset_s declaration is not inferred nonblocking by
         * Clang's function-effects analysis.  A volatile byte loop keeps the
         * same optimization-resistant guarantee without leaving the
         * realtime allowlist. */
        volatile uint8_t *zeroBytes = slot->bytes;
        const uint32_t staleByteCount = (uint32_t)totalBytes;
        for (uint32_t zeroIndex = 0u; zeroIndex < staleByteCount; ++zeroIndex) {
            zeroBytes[zeroIndex] = 0u;
        }
        slot->bufferCount = 0u;
        slot->totalBytes = 0u;
        slot->sampleTime = 0.0;
        slot->hostTime = 0u;
        slot->timestampFlags = 0u;
        slot->generation = 0u;
        for (uint32_t zeroIndex = 0u; zeroIndex < TARS_REALTIME_MAX_BUFFERS; ++zeroIndex) {
            slot->bufferByteSizes[zeroIndex] = 0u;
            slot->bufferChannels[zeroIndex] = 0u;
        }
        slot->asbd.sampleRate = 0.0;
        slot->asbd.formatID = 0u;
        slot->asbd.formatFlags = 0u;
        slot->asbd.bytesPerPacket = 0u;
        slot->asbd.framesPerPacket = 0u;
        slot->asbd.bytesPerFrame = 0u;
        slot->asbd.channelsPerFrame = 0u;
        slot->asbd.bitsPerChannel = 0u;
        slot->asbd.isInterleaved = 0u;
        TARS_REALTIME_SATURATING_INCREMENT(ring->staleGenerationArrivals);
    }
    /* Admission remains held through the complete stale cleanup above.  This
     * prevents a reopened generation from reusing the slot while the old
     * IOProc is still clearing it. */
    atomic_fetch_sub_explicit(&ring->publicationGate, 1u, memory_order_release);
    return noErr;
}

OSStatus TarsRealtimeAudioCreateIOProc(AudioObjectID device,
                                       void *clientData,
                                       uint64_t *outToken)
{
    if (outToken == NULL) {
        return -50;
    }
    AudioDeviceIOProcID ioProc = NULL;
    const OSStatus status = AudioDeviceCreateIOProcID(device, TarsRealtimeAudioIOProc, clientData, &ioProc);
    if (status != noErr) {
        return status;
    }
    *outToken = (uint64_t)(uintptr_t)ioProc;
    return noErr;
}

static AudioDeviceIOProcID tars_io_proc_from_token(uint64_t token)
{
    return (AudioDeviceIOProcID)(uintptr_t)token;
}

OSStatus TarsRealtimeAudioStartIOProc(AudioObjectID device, uint64_t token)
{
    return AudioDeviceStart(device, tars_io_proc_from_token(token));
}

OSStatus TarsRealtimeAudioStopIOProc(AudioObjectID device, uint64_t token)
{
    return AudioDeviceStop(device, tars_io_proc_from_token(token));
}

OSStatus TarsRealtimeAudioDestroyIOProc(AudioObjectID device, uint64_t token)
{
    return AudioDeviceDestroyIOProcID(device, tars_io_proc_from_token(token));
}
