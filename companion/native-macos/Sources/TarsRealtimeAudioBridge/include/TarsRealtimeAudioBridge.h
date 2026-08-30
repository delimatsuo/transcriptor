#ifndef TARS_REALTIME_AUDIO_BRIDGE_H
#define TARS_REALTIME_AUDIO_BRIDGE_H

#include <CoreAudio/AudioHardware.h>
#include <CoreAudioTypes/CoreAudioTypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * The bridge deliberately has no variable-sized public object.  The ring and
 * its slots are allocated by the implementation before AudioDeviceStart and
 * are addressed by the IOProc only through this opaque handle.
 */
#define TARS_REALTIME_MAX_BUFFERS 8u
#define TARS_REALTIME_MAX_OVERFLOW_EPISODES 64u

typedef struct TarsRealtimeAudioRing TarsRealtimeAudioRing;

typedef enum TarsRealtimeDescriptorClass {
    TARS_REALTIME_DESCRIPTOR_EMPTY = 0,
    TARS_REALTIME_DESCRIPTOR_VALID_NONEMPTY = 1,
    TARS_REALTIME_DESCRIPTOR_MALFORMED = 2,
    TARS_REALTIME_DESCRIPTOR_CAPACITY_REJECTED = 3,
    TARS_REALTIME_DESCRIPTOR_STALE_GENERATION = 4,
    TARS_REALTIME_DESCRIPTOR_CURSOR_OVERFLOW = 5
} TarsRealtimeDescriptorClass;

typedef struct TarsRealtimeASBDSnapshot {
    double sampleRate;
    uint32_t formatID;
    uint32_t formatFlags;
    uint32_t bytesPerPacket;
    uint32_t framesPerPacket;
    uint32_t bytesPerFrame;
    uint32_t channelsPerFrame;
    uint32_t bitsPerChannel;
    uint32_t isInterleaved;
} TarsRealtimeASBDSnapshot;

typedef struct TarsRealtimeInputBuffer {
    const uint8_t *data;
    uint32_t byteSize;
    uint32_t channels;
} TarsRealtimeInputBuffer;

typedef struct TarsRealtimeInputDescriptor {
    uint32_t bufferCount;
    const TarsRealtimeInputBuffer *buffers;
    TarsRealtimeASBDSnapshot asbd;
    double sampleTime;
    uint64_t hostTime;
    uint32_t timestampFlags;
    uint64_t generation;
} TarsRealtimeInputDescriptor;

typedef struct TarsRealtimeSlotOutput {
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
    uint32_t byteCapacity;
} TarsRealtimeSlotOutput;

typedef struct TarsRealtimeCounters {
    uint64_t callbackArrivals;
    uint64_t validNonemptyArrivals;
    uint64_t emptyArrivals;
    uint64_t malformedArrivals;
    uint64_t capacityRejectedArrivals;
    uint64_t staleGenerationArrivals;
    uint64_t ringOverflowCount;
    uint64_t ringOverflowEpisodes;
    uint64_t enqueuedCount;
    uint64_t poppedCount;
    uint64_t ringOverflowMetadataDrops;
    uint64_t cursorOverflow;
} TarsRealtimeCounters;

/* One bounded producer-side record for an overflow episode.  The write/read
 * cursors are captured at the callback that first observes a full ring; Swift
 * must not reconstruct this boundary later from a moving retained-slot count. */
typedef struct TarsRealtimeOverflowBoundary {
    uint64_t producerWriteIndex;
    uint64_t producerReadIndex;
    uint64_t episodeNumber;
    uint32_t retainedSlotCount;
} TarsRealtimeOverflowBoundary;

/* Snapshot returned by the non-realtime terminal-retirement ownership edge.
 * The snapshot is taken only after callback publication admission is closed
 * and all already-admitted callbacks have quiesced. */
typedef struct TarsRealtimeRingRetirement {
    bool hadRetainedSlots;
    uint64_t firstReadIndex;
    uint64_t writeIndex;
    uint32_t retainedSlotCount;
    /* A callback can be admitted, copy a valid nonempty payload, and then
     * lose the final generation check after terminal close.  This outcome is
     * distinct from a retained ring slot because the callback never advances
     * writeIndex; terminal ownership must still emit one conservative raw
     * boundary for it. */
    bool hadAdmittedLoss;
    uint64_t admittedLossReadIndex;
    uint64_t admittedLossWriteIndex;
} TarsRealtimeRingRetirement;

typedef void (*TarsRealtimeZeroizationHook)(const uint8_t *slotBytes,
                                             size_t slotByteCount,
                                             void *context);
typedef void (*TarsRealtimeStaleCleanupHook)(void *context);
typedef void (*TarsRealtimePublicationFenceHook)(void *context);

/* Creation/destruction are non-realtime operations. */
TarsRealtimeAudioRing *TarsRealtimeAudioRingCreate(uint32_t slotCount,
                                                    uint32_t slotCapacity,
                                                    const TarsRealtimeASBDSnapshot *expectedASBD,
                                                    uint32_t expectedChannels,
                                                    bool expectedInterleaved,
                                                    uint64_t generation);
void TarsRealtimeAudioRingDestroy(TarsRealtimeAudioRing *ring);
void TarsRealtimeAudioRingSetGeneration(TarsRealtimeAudioRing *ring, uint64_t generation);
/* Non-realtime terminal-failure ownership edge.  Closes callback admission,
 * waits for admitted callbacks, snapshots the raw backlog, then retires the
 * ring and leaves it fenced at generation zero. */
TarsRealtimeRingRetirement TarsRealtimeAudioRingRetireForTerminalFailure(
    TarsRealtimeAudioRing *ring);
uint64_t TarsRealtimeAudioRingGeneration(const TarsRealtimeAudioRing *ring);
uint32_t TarsRealtimeAudioRingSlotCapacity(const TarsRealtimeAudioRing *ring);
uint32_t TarsRealtimeAudioRingSlotCount(const TarsRealtimeAudioRing *ring);
uint64_t TarsRealtimeAudioRingReadIndex(const TarsRealtimeAudioRing *ring);

/* Non-realtime fixture-only boundary: force the next Push publication to
 * become stale after its bounded copy, before stale-slot cleanup begins. */
void TarsRealtimeAudioRingFenceBeforeNextPushPublicationForTesting(TarsRealtimeAudioRing *ring);
/* Non-realtime fixture-only hook invoked before stale Push cleanup.  It is
 * intentionally not reachable from the Core Audio IOProc. */
void TarsRealtimeAudioRingSetStaleCleanupHookForTesting(TarsRealtimeAudioRing *ring,
                                                        TarsRealtimeStaleCleanupHook hook,
                                                        void *context);
void TarsRealtimeAudioRingSetPublicationFenceHookForTesting(TarsRealtimeAudioRing *ring,
                                                             TarsRealtimePublicationFenceHook hook,
                                                             void *context);
/* Test-only terminal-retirement race hook.  It runs immediately before the
 * non-realtime close-admission edge, so a fixture can admit one final
 * production IOProc publication and prove the subsequent retirement snapshot
 * includes it. */
void TarsRealtimeAudioRingSetTerminalRetirementHookForTesting(
    TarsRealtimeAudioRing *ring,
    TarsRealtimePublicationFenceHook hook,
    void *context);
/* Test-only external control edge for the production IOProc.  The next valid
 * callback copies its payload, records its publication state, and returns
 * without releasing admission or performing its final generation check.  It
 * never waits, spins, blocks, or self-fences.  A non-realtime control actor
 * must observe PublicationPauseReady and call ResumeHeldPublication before
 * any fence/destroy operation can complete. */
void TarsRealtimeAudioRingHoldNextFinalPublicationForTesting(
    TarsRealtimeAudioRing *ring);
bool TarsRealtimeAudioRingPublicationPauseReadyForTesting(
    const TarsRealtimeAudioRing *ring);
void TarsRealtimeAudioRingResumeHeldPublicationForTesting(
    TarsRealtimeAudioRing *ring);

/* Deterministic publication-admission fixture.  Begin reserves one admitted
 * publication and End releases it.  A concurrent non-realtime generation
 * fence must close admission, store generation zero, and wait for End; a
 * later Begin must fail without a callback wait or spin. */
bool TarsRealtimeAudioRingTryBeginPublicationForTesting(TarsRealtimeAudioRing *ring,
                                                         uint64_t generation);
void TarsRealtimeAudioRingEndPublicationForTesting(TarsRealtimeAudioRing *ring);
bool TarsRealtimeAudioRingPublicationFenceStartedForTesting(const TarsRealtimeAudioRing *ring);
/* A deterministic pre-admission ABA fixture.  Load captures the exact
 * publication token observed by a would-be IOProc before its CAS; Commit
 * performs that one CAS later and never retries.  A close/reopen transition
 * must invalidate the token, even when the gate count returns to zero. */
uint64_t TarsRealtimeAudioRingLoadPublicationAdmissionTokenForTesting(
    const TarsRealtimeAudioRing *ring);
bool TarsRealtimeAudioRingTryCommitPublicationAdmissionTokenForTesting(
    TarsRealtimeAudioRing *ring,
    uint64_t sampledToken,
    uint64_t generation);

/* Deterministic non-realtime fixture input.  The production IOProc does not
 * call this function; it performs the same classification inline. */
TarsRealtimeDescriptorClass TarsRealtimeAudioRingPush(TarsRealtimeAudioRing *ring,
                                                      const TarsRealtimeInputDescriptor *descriptor);

/* Pop copies into caller-owned preallocated storage and zeroizes the consumed
 * slot before making it reusable.  Returns 1 for success, 0 for empty, -1
 * when the caller supplied insufficient storage (the slot remains queued), or
 * -2 when an unconsumed overflow boundary has reached the consumer cursor.
 * Swift must consume that FIFO boundary before attempting another pop. */
int TarsRealtimeAudioRingPop(TarsRealtimeAudioRing *ring, TarsRealtimeSlotOutput *output);

/* Pop only an item whose absolute consumer cursor is below the captured
 * producer write cursor from an overflow boundary.  This keeps a producer
 * enqueue that races metadata polling out of the older episode's retained
 * audio sequence.  Returns the same 1/0/-1/-2 result contract as Pop. */
int TarsRealtimeAudioRingPopThrough(TarsRealtimeAudioRing *ring,
                                    uint64_t producerWriteIndex,
                                    TarsRealtimeSlotOutput *output);

/* Test-only non-realtime cursor placement used to prove restart-before-wrap
 * behavior.  The production ring always begins at zero and stops loudly
 * before either absolute cursor can wrap. */
void TarsRealtimeAudioRingSetCursorForTesting(TarsRealtimeAudioRing *ring,
                                              uint64_t writeIndex,
                                              uint64_t readIndex);

/* Test-only diagnostic seed used to prove that the realtime counter update
 * saturates at UINT64_MAX instead of wrapping back to an apparently healthy
 * zero.  Production callers never set diagnostic state directly. */
void TarsRealtimeAudioRingSetCallbackArrivalsForTesting(TarsRealtimeAudioRing *ring,
                                                        uint64_t value);

/* Test-only inspection proving Pop zeroized a reusable slot before Destroy;
 * this is independent of the destroy-time zeroization hook. */
bool TarsRealtimeAudioRingSlotIsZeroizedForTesting(const TarsRealtimeAudioRing *ring,
                                                   uint32_t slotIndex);
/* Test-only inspection proving a generation transition zeroized the complete
 * producer-side overflow evidence array, not merely its FIFO cursors.  The
 * inspector closes callback admission and waits for admitted callbacks before
 * reading the bytes; callers must reopen the test ring explicitly if they
 * want to publish more fixture input.  Metadata bytes never leave the bridge
 * or become production API data. */
bool TarsRealtimeAudioRingOverflowMetadataIsZeroizedForTesting(
    const TarsRealtimeAudioRing *ring);

TarsRealtimeCounters TarsRealtimeAudioRingSnapshot(const TarsRealtimeAudioRing *ring);
bool TarsRealtimeAudioRingIsEmpty(const TarsRealtimeAudioRing *ring);
uint32_t TarsRealtimeAudioRingRetainedSlots(const TarsRealtimeAudioRing *ring);
/* FIFO consumer of the preallocated producer-side overflow metadata. */
bool TarsRealtimeAudioRingPopOverflowBoundary(TarsRealtimeAudioRing *ring,
                                              TarsRealtimeOverflowBoundary *boundary);
void TarsRealtimeAudioRingSetZeroizationHook(TarsRealtimeAudioRing *ring,
                                             TarsRealtimeZeroizationHook hook,
                                             void *context);

/*
 * The literal Core Audio callback symbol.  Its body intentionally contains
 * the complete descriptor classification and publication path: the compiler
 * effects gate and the AST reachability gate can therefore inspect the exact
 * realtime boundary without trusting a Swift closure or a helper.
 */
OSStatus TarsRealtimeAudioIOProc(AudioObjectID inDevice,
                                 const AudioTimeStamp *inNow,
                                 const AudioBufferList *inInputData,
                                 const AudioTimeStamp *inInputTime,
                                 AudioBufferList *outOutputData,
                                 const AudioTimeStamp *inOutputTime,
                                 void *inClientData) CA_REALTIME_API;

/* Non-realtime Core Audio IOProc handle adapters.  Swift keeps only the
 * opaque integer token; the function-pointer representation never crosses
 * the Swift concurrency boundary. */
OSStatus TarsRealtimeAudioCreateIOProc(AudioObjectID device,
                                       void *clientData,
                                       uint64_t *outToken);
OSStatus TarsRealtimeAudioStartIOProc(AudioObjectID device, uint64_t token);
OSStatus TarsRealtimeAudioStopIOProc(AudioObjectID device, uint64_t token);
OSStatus TarsRealtimeAudioDestroyIOProc(AudioObjectID device, uint64_t token);

/* Descriptive compatibility spelling used by source-contract tooling. */
#define TarsAudioDeviceIOProc TarsRealtimeAudioIOProc

#ifdef __cplusplus
}
#endif

#endif /* TARS_REALTIME_AUDIO_BRIDGE_H */
