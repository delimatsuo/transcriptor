"""Single deletion path for session data (LGPD Art. 18 elimination)."""

from __future__ import annotations

from datetime import datetime

import structlog

logger = structlog.get_logger()


async def delete_session_everywhere(
    session_id: str,
    db,
    gcs,
    *,
    reason: str = "owner_request",
    owner_id: str | None = None,
    org_id: str | None = None,
) -> dict:
    """Delete a session's Firestore data, linked GCS blobs, and write a tombstone.

    Subcollections are discovered at deletion time, including nested
    subcollections, so new persisted session data cannot bypass this path.
    """
    session_ref = db.collection("sessions").document(session_id)
    subs_deleted, docs_deleted, blobs_deleted = 0, 0, 0

    async def delete_subcollections(document_ref) -> None:
        nonlocal subs_deleted, docs_deleted, blobs_deleted

        async for subcollection in document_ref.collections():
            subs_deleted += 1
            async for snapshot in subcollection.stream():
                data = snapshot.to_dict() or {}
                if owner_id is not None and org_id is not None and (
                    data.get("ownerId") != owner_id or data.get("orgId") != org_id
                ):
                    raise PermissionError("child record ownership is not authorized")
                gcs_path = data.get("gcsPath")
                if gcs_path and gcs is not None and gcs.delete_blob(gcs_path):
                    blobs_deleted += 1
                await delete_subcollections(snapshot.reference)
                await snapshot.reference.delete()
                docs_deleted += 1

    await delete_subcollections(session_ref)
    await session_ref.delete()

    await db.collection("deletions").document().set({
        "sessionId": session_id,
        "deletedAt": datetime.utcnow(),
        "reason": reason,
        "subcollectionsDeleted": subs_deleted,
        "docsDeleted": docs_deleted,
        "gcsBlobsDeleted": blobs_deleted,
        "ownerId": owner_id,
        "orgId": org_id,
    })
    logger.info(
        "session_deleted_everywhere",
        session_id=session_id,
        docs=docs_deleted,
        blobs=blobs_deleted,
    )
    return {
        "session_id": session_id,
        "subcollections_deleted": subs_deleted,
        "docs_deleted": docs_deleted,
        "gcs_blobs_deleted": blobs_deleted,
    }
