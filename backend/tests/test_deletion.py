"""delete_session_everywhere must cascade: subcollections, GCS blobs, doc, tombstone."""
import asyncio

import pytest

from backend.storage.deletion import delete_session_everywhere


class FakeDoc:
    def __init__(self, doc_id, data, subcollections=None):
        self.id, self._data = doc_id, data
        self._subs = subcollections or {}
        self.deleted = False

    async def delete(self):
        self.deleted = True

    def collections(self):
        async def gen():
            for name, docs in self._subs.items():
                yield FakeCollection(name, docs)

        return gen()


class FakeSnapshot:
    """Mirror a Firestore document snapshot yielded from collection.stream()."""

    def __init__(self, reference):
        self.reference = reference

    def to_dict(self):
        return self.reference._data


class FakeCollection:
    def __init__(self, name, docs):
        self.id, self._docs = name, docs

    def stream(self):
        async def gen():
            for d in self._docs:
                yield FakeSnapshot(d)

        return gen()


class FakeDB:
    def __init__(self, session_doc):
        self._session_doc = session_doc
        self.tombstones = []
        self.session_tombstone = None

    def collection(self, name):
        db = self

        class Col:
            def document(self, doc_id=None):
                if name == "sessions":
                    return db._session_doc

                if name == "session_tombstones":
                    class Snapshot:
                        @property
                        def exists(self):
                            return db.session_tombstone is not None

                        def to_dict(self):
                            return db.session_tombstone or {}

                    class Fence:
                        async def get(self):
                            return Snapshot()

                        async def set(self, data, merge=False):
                            base = (db.session_tombstone or {}) if merge else {}
                            db.session_tombstone = {
                                **base,
                                **data,
                            }

                    return Fence()

                class T:
                    async def set(self, data, merge=False):
                        db.tombstones.append(data)

                return T()

        return Col()


class FakeGCS:
    def __init__(self):
        self.deleted = []

    def delete_blob(self, path):
        self.deleted.append(path)
        return True


def test_cascade_deletes_everything_and_tombstones():
    seg = FakeDoc("seg1", {"text": "hi"})
    note = FakeDoc("note1", {"kind": "strength", "source": "recruiter"})
    report = FakeDoc("current", {"status": "approved", "version": 2})
    report_source = FakeDoc("resume", {"type": "resume", "text": "CV"})
    cv = FakeDoc("d1", {"gcsPath": "sessions/s1/cv.pdf", "type": "resume"})
    session = FakeDoc(
        "s1",
        {"title": "t"},
        {
            "transcript": [seg],
            "notes": [note],
            "reports": [report],
            "report_sources": [report_source],
            "documents": [cv],
        },
    )
    db, gcs = FakeDB(session), FakeGCS()

    result = asyncio.run(delete_session_everywhere("s1", db, gcs))

    assert all(
        document.deleted
        for document in (seg, note, report, report_source, cv, session)
    )
    assert gcs.deleted == ["sessions/s1/cv.pdf"]
    assert db.tombstones and db.tombstones[0]["sessionId"] == "s1"
    assert db.session_tombstone["sessionId"] == "s1"
    assert result["gcs_blobs_deleted"] == 1


def test_failure_after_fencing_stays_fenced_and_retryable():
    class FailingDoc(FakeDoc):
        async def delete(self):
            raise RuntimeError("synthetic child delete failure")

    child = FailingDoc("seg1", {"text": "synthetic"})
    session = FakeDoc("s1", {"title": "t"}, {"transcript": [child]})
    db = FakeDB(session)

    with pytest.raises(RuntimeError, match="synthetic"):
        asyncio.run(delete_session_everywhere("s1", db, FakeGCS()))
    assert db.session_tombstone["sessionId"] == "s1"
    assert session.deleted is False
