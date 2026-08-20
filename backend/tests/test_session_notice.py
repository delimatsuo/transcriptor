from backend.schemas.models import Session


def test_session_has_notice_given_defaulting_false():
    s = Session()
    assert s.notice_given is False
    s2 = Session(notice_given=True)
    assert s2.notice_given is True
