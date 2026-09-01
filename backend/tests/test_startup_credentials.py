"""The backend must fail loudly instead of hanging on expired ADC."""

import asyncio
import re
import threading
import time
import traceback
from unittest.mock import Mock, patch

import pytest
from google.auth.exceptions import RefreshError

from backend import startup_credentials
from backend.auth import AuthConfigurationError
from backend.config import Settings


def test_probe_refreshes_default_credentials(monkeypatch, capsys):
    credentials = Mock()
    request = object()
    default = Mock(return_value=(credentials, "test-project"))

    monkeypatch.setattr(startup_credentials.google.auth, "default", default)
    monkeypatch.setattr(startup_credentials, "Request", lambda: request)

    asyncio.run(startup_credentials.probe_application_default_credentials())

    default.assert_called_once_with()
    credentials.refresh.assert_called_once_with(request)
    assert capsys.readouterr().err == ""


def test_probe_exits_loudly_when_credential_refresh_fails(monkeypatch, capsys):
    credentials = Mock()
    credentials.refresh.side_effect = RefreshError("reauthentication required")
    request = object()

    monkeypatch.setattr(
        startup_credentials.google.auth,
        "default",
        lambda: (credentials, "test-project"),
    )
    monkeypatch.setattr(startup_credentials, "Request", lambda: request)

    with pytest.raises(
        startup_credentials.ADCStartupError,
        match=f"^{re.escape(startup_credentials.ADC_ERROR_MESSAGE)}$",
    ):
        asyncio.run(
            startup_credentials.probe_application_default_credentials(
                timeout_seconds=0.5
            )
        )

    credentials.refresh.assert_called_once_with(request)
    assert capsys.readouterr().err == f"{startup_credentials.ADC_ERROR_MESSAGE}\n"


def test_probe_times_out_without_waiting_for_stuck_refresh(monkeypatch, capsys):
    release_refresh = threading.Event()
    refresh_finished = threading.Event()
    credentials = Mock()

    def blocking_refresh(_request):
        release_refresh.wait(timeout=1.0)
        refresh_finished.set()

    credentials.refresh.side_effect = blocking_refresh
    monkeypatch.setattr(
        startup_credentials.google.auth,
        "default",
        lambda: (credentials, "test-project"),
    )

    async def run_probe_and_release_worker():
        started_at = time.monotonic()
        with pytest.raises(
            startup_credentials.ADCStartupError,
            match=f"^{re.escape(startup_credentials.ADC_ERROR_MESSAGE)}$",
        ):
            await startup_credentials.probe_application_default_credentials(
                timeout_seconds=0.02
            )
        elapsed = time.monotonic() - started_at

        release_refresh.set()
        for _ in range(100):
            if refresh_finished.is_set():
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0)
        return elapsed

    elapsed = asyncio.run(run_probe_and_release_worker())

    assert elapsed < 0.5
    assert refresh_finished.is_set()
    assert capsys.readouterr().err == f"{startup_credentials.ADC_ERROR_MESSAGE}\n"


def test_lifespan_probes_adc_before_readiness(monkeypatch):
    """The app must run pure validators, inspect existing app binding, and probe ADC before readiness."""
    from backend import main

    events: list[str] = []
    settings = Settings(
        google_cloud_project="tars-test-project",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
        auth_bypass=False,
    )

    def fake_validate_raw(raw_env, resolved=None):
        if resolved is None:
            events.append("raw_env_gate")
        else:
            events.append("raw_vs_resolved_gate")
        return "local"

    def fake_resolve_settings():
        events.append("resolve_settings")
        return settings

    def fake_validate_auth(s):
        events.append("validate_auth_config")

    def fake_validate_existing_app(s):
        events.append("validate_existing_app")

    async def fake_probe():
        events.append("adc_probe")

    def fake_initialize(google_project, firebase_project=None):
        events.append("firebase_init")

    class FakeSessionManager:
        def __init__(self, _settings):
            events.append("session_manager")

        def detect_orphaned_sessions(self):
            return []

    class FakeStorage:
        def __init__(self, _settings):
            events.append("storage")

    class FakeGemini:
        def __init__(self, _settings):
            events.append("gemini")

    monkeypatch.setattr(main, "validate_raw_process_env", fake_validate_raw)
    monkeypatch.setattr(main, "resolve_settings_safely", fake_resolve_settings)
    monkeypatch.setattr(main, "validate_auth_configuration", fake_validate_auth)
    monkeypatch.setattr(main, "validate_existing_firebase_app", fake_validate_existing_app)
    monkeypatch.setattr(main, "probe_application_default_credentials", fake_probe)
    monkeypatch.setattr(main, "initialize_firebase_admin", fake_initialize)
    monkeypatch.setattr(main, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(main, "FirestoreStorage", FakeStorage)
    monkeypatch.setattr(main, "GCSStorage", FakeStorage)
    monkeypatch.setattr(main, "GeminiClient", FakeGemini)

    main.app.state.ready = False
    async def run_lifespan():
        assert main.app.state.ready is False
        async with main.lifespan(main.app):
            events.append("ready_true")
            assert main.app.state.ready is True

    asyncio.run(run_lifespan())

    expected_sequence = [
        "raw_env_gate",
        "resolve_settings",
        "raw_vs_resolved_gate",
        "validate_auth_config",
        "validate_existing_app",
        "adc_probe",
        "firebase_init",
        "session_manager",
        "storage",
        "storage",
        "gemini",
        "ready_true",
    ]
    assert events == expected_sequence
    assert main.app.state.ready is False


def test_lifespan_post_adc_firebase_fail_closed_matrix(monkeypatch, caplog):
    """Post-ADC Firebase lookup, option, and initialization failures must fail closed with zero sentinels."""
    import firebase_admin
    from backend import main

    settings = Settings(
        google_cloud_project="tars-test-project",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
        auth_bypass=False,
    )

    class ThrowingStrValueError(ValueError):
        def __str__(self):
            raise RuntimeError("THROWING_STR_SENTINEL_123")

        def __repr__(self):
            raise RuntimeError("THROWING_REPR_SENTINEL_123")

    class ThrowingOptionsApp:
        class ThrowingOptions:
            def get(self, key, default=None):
                raise RuntimeError("THROWING_OPTIONS_GET_SENTINEL_456")

        def __init__(self):
            self._options = self.ThrowingOptions()

        @property
        def project_id(self):
            raise AssertionError("LAZY_PROJECT_ID_SENTINEL_MUST_NOT_BE_TOUCHED")

    failure_cases = [
        # 1. Unrelated ValueError on second get_app
        ("unrelated_value_error", lambda: (_ for _ in ()).throw(ValueError("UNRELATED_VALUE_ERROR_SENTINEL")), None),
        # 2. Subclass with throwing __str__ / repr
        ("throwing_str_subclass", lambda: (_ for _ in ()).throw(ThrowingStrValueError("SUBCLASS_VALUE_ERROR_SENTINEL")), None),
        # 3. Throwing _options.get
        ("throwing_options", lambda: ThrowingOptionsApp(), None),
        # 4. initialize_app throws sentinel
        ("throwing_init_app", None, lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("INIT_APP_FAIL_SENTINEL_789"))),
    ]

    for name, get_app_factory, init_app_side_effect in failure_cases:
        caplog.clear()
        main.app.state.ready = False
        downstream_events = []
        counts = {"get_app": 0, "probe_adc": 0, "init_app": 0}

        monkeypatch.setattr(main, "validate_raw_process_env", lambda *a, **k: "local")
        monkeypatch.setattr(main, "resolve_settings_safely", lambda: settings)
        monkeypatch.setattr(main, "validate_auth_configuration", lambda s: None)
        monkeypatch.setattr(main, "validate_existing_firebase_app", lambda s: None)

        async def fake_probe(proj=None):
            counts["probe_adc"] += 1
        monkeypatch.setattr(main, "probe_application_default_credentials", fake_probe)

        if get_app_factory is not None:
            def counting_get_app():
                counts["get_app"] += 1
                return get_app_factory()
            monkeypatch.setattr(firebase_admin, "get_app", counting_get_app)
        else:
            # Canonical no default app on get_app
            def canonical_get_app():
                counts["get_app"] += 1
                raise ValueError(
                    "The default Firebase app does not exist. Make sure to initialize the SDK by calling initialize_app()."
                )
            monkeypatch.setattr(firebase_admin, "get_app", canonical_get_app)

        if init_app_side_effect is not None:
            def counting_init_app(*a, **kw):
                counts["init_app"] += 1
                return init_app_side_effect(*a, **kw)
            monkeypatch.setattr(firebase_admin, "initialize_app", counting_init_app)
        else:
            def counting_init_app(*a, **kw):
                counts["init_app"] += 1
                return None
            monkeypatch.setattr(firebase_admin, "initialize_app", counting_init_app)

        monkeypatch.setattr(main, "SessionManager", lambda s: downstream_events.append("session_mgr"))
        monkeypatch.setattr(main, "FirestoreStorage", lambda s: downstream_events.append("firestore"))
        monkeypatch.setattr(main, "GCSStorage", lambda s: downstream_events.append("gcs"))
        monkeypatch.setattr(main, "GeminiClient", lambda s: downstream_events.append("gemini"))

        async def run():
            async with main.lifespan(main.app):
                pass

        with pytest.raises(AuthConfigurationError) as exc_info:
            asyncio.run(run())

        # Zero downstream constructors executed
        assert downstream_events == [], f"Failure case {name} executed downstream constructors: {downstream_events}"
        # Ready remains exactly False
        assert main.app.state.ready is False
        assert main.settings is None

        # Content-free exception hygiene
        err_msg = str(exc_info.value)
        assert "SENTINEL" not in err_msg
        assert exc_info.value.__cause__ is None
        assert "SENTINEL" not in caplog.text


def test_lifespan_isolated_failure_gates(monkeypatch):
    """Failure at each pre-provider gate leaves readiness false and stops subsequent steps."""
    from backend import main

    settings = Settings(
        google_cloud_project="tars-test-project",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
        auth_bypass=False,
    )

    gates = [
        "raw_env_gate",
        "resolve_settings",
        "raw_vs_resolved_gate",
        "validate_auth_config",
        "validate_existing_app",
    ]

    for failing_gate in gates:
        events: list[str] = []
        main.app.state.ready = False

        def fake_validate_raw(raw_env, resolved=None):
            gate = "raw_env_gate" if resolved is None else "raw_vs_resolved_gate"
            events.append(gate)
            if gate == failing_gate:
                raise AuthConfigurationError(f"Gate {gate} failed")
            return "local"

        def make_validator(name):
            def fn(*args, **kwargs):
                events.append(name)
                if name == failing_gate:
                    raise AuthConfigurationError(f"Gate {name} failed")
                if name == "resolve_settings":
                    return settings
                return "local"
            return fn

        monkeypatch.setattr(main, "validate_raw_process_env", fake_validate_raw)
        monkeypatch.setattr(main, "resolve_settings_safely", make_validator("resolve_settings"))
        monkeypatch.setattr(main, "validate_auth_configuration", make_validator("validate_auth_config"))
        monkeypatch.setattr(main, "validate_existing_firebase_app", make_validator("validate_existing_app"))

        async def fake_probe(project=None):
            events.append("adc_probe")

        monkeypatch.setattr(main, "probe_application_default_credentials", fake_probe)
        monkeypatch.setattr(main, "initialize_firebase_admin", lambda *a, **kw: events.append("firebase_init"))

        async def run():
            async with main.lifespan(main.app):
                pass

        with pytest.raises(AuthConfigurationError):
            asyncio.run(run())

        assert failing_gate in events
        assert "adc_probe" not in events
        assert "firebase_init" not in events
        assert main.app.state.ready is False


def test_is_canonical_no_default_app_error_adversarial_matrix():
    """Classifier must accept ONLY the exact installed SDK string and reject variants, spoofs, and subclasses."""
    from backend.auth import _is_canonical_no_default_app_error

    class EqualitySpoof:
        def __eq__(self, other):
            return True

    class ThrowingEqualitySpoof:
        def __eq__(self, other):
            raise AssertionError("THROWING_EQUALITY_SENTINEL")

    class ThrowingStrSpoof:
        def __str__(self):
            raise AssertionError("THROWING_STR_SENTINEL")

        def __repr__(self):
            raise AssertionError("THROWING_REPR_SENTINEL")

    class ValueErrorSubclass(ValueError):
        pass

    canonical_msg = "The default Firebase app does not exist. Make sure to initialize the SDK by calling initialize_app()."

    # 1. Equality spoof where args[0] is not str -> False
    assert _is_canonical_no_default_app_error(ValueError(EqualitySpoof())) is False

    # 2. Throwing equality spoof -> False without throwing
    assert _is_canonical_no_default_app_error(ValueError(ThrowingEqualitySpoof())) is False

    # 3. Throwing string spoof -> False without throwing
    assert _is_canonical_no_default_app_error(ValueError(ThrowingStrSpoof())) is False

    # 4. ValueError subclass with exact message -> False (kills type is vs isinstance mutation)
    assert _is_canonical_no_default_app_error(ValueErrorSubclass(canonical_msg)) is False

    # 5. Multiple args -> False
    assert _is_canonical_no_default_app_error(ValueError(canonical_msg, "extra")) is False

    # 6. Unrelated string -> False
    assert _is_canonical_no_default_app_error(ValueError("Some other ValueError")) is False

    # 7. Bare, period-only, or wording variants -> False
    assert _is_canonical_no_default_app_error(ValueError("The default Firebase app does not exist")) is False
    assert _is_canonical_no_default_app_error(ValueError("The default Firebase app does not exist.")) is False
    assert _is_canonical_no_default_app_error(ValueError("The default Firebase app does not exist. Make sure you initialize the SDK by calling initialize_app().")) is False

    # 8. Single exact installed SDK string on built-in ValueError -> True
    assert _is_canonical_no_default_app_error(ValueError(canonical_msg)) is True


def test_lifespan_bare_vs_exact_firebase_error(monkeypatch):
    """Bare ValueError stops before ADC probe, constructors, or ready; only exact SDK message authorizes init."""
    import firebase_admin
    from backend import main

    settings = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test",
        auth_allowed_emails="a@b.com",
        auth_org_id="ella-internal",
    )

    events: list[str] = []
    main.app.state.ready = False

    monkeypatch.setattr(main, "validate_raw_process_env", lambda *a, **kw: "local")
    monkeypatch.setattr(main, "resolve_settings_safely", lambda: settings)
    monkeypatch.setattr(main, "validate_auth_configuration", lambda *a, **kw: None)

    async def fake_probe(project=None):
        events.append("adc_probe")

    monkeypatch.setattr(main, "probe_application_default_credentials", fake_probe)
    monkeypatch.setattr(main, "initialize_firebase_admin", lambda *a, **kw: events.append("firebase_init"))

    # 1. Bare short ValueError stops before ADC and init
    with patch("backend.auth.firebase_admin.get_app", side_effect=ValueError("The default Firebase app does not exist")):
        async def run_bare():
            async with main.lifespan(main.app):
                pass

        with pytest.raises(AuthConfigurationError):
            asyncio.run(run_bare())

    assert "adc_probe" not in events
    assert "firebase_init" not in events
    assert main.app.state.ready is False

    # 2. Exact SDK string authorizes ADC and init
    with patch("backend.auth.firebase_admin.get_app", side_effect=ValueError("The default Firebase app does not exist. Make sure to initialize the SDK by calling initialize_app().")):
        async def run_exact():
            async with main.lifespan(main.app):
                assert main.app.state.ready is True

        asyncio.run(run_exact())

    assert "adc_probe" in events
    assert "firebase_init" in events
    assert main.app.state.ready is False


class SequencedGetApp:
    def __init__(self, sequence, tracker=None, assert_fn=None):
        self.sequence = list(sequence)
        self.call_count = 0
        self.tracker = tracker
        self.assert_fn = assert_fn

    def __call__(self, *args, **kwargs):
        self.call_count += 1
        if self.assert_fn is not None:
            self.assert_fn()
        if self.tracker is not None:
            self.tracker.events.append(f"get_app[{self.call_count}]")
        if self.sequence:
            item = self.sequence.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise ValueError(
            "The default Firebase app does not exist. Make sure to initialize the SDK by calling initialize_app()."
        )


class ThrowingStrError(Exception):
    def __str__(self):
        raise RuntimeError("THROWING_STR_SENTINEL_LEAK")

    def __repr__(self):
        return "ThrowingStrError()"


class ThrowingReprError(Exception):
    def __str__(self):
        return "ThrowingReprError()"

    def __repr__(self):
        raise RuntimeError("THROWING_REPR_SENTINEL_LEAK")


class ThrowingStrAndReprError(Exception):
    def __str__(self):
        raise RuntimeError("THROWING_STR_SENTINEL_LEAK_BOTH")

    def __repr__(self):
        raise RuntimeError("THROWING_REPR_SENTINEL_LEAK_BOTH")


class FakeValidApp:
    def __init__(self, project_id="tars-test-project"):
        self._options = {"projectId": project_id}


class FakeAppMissingOptions:
    pass


class FakeAppThrowingOptionsProp:
    @property
    def _options(self):
        raise RuntimeError("THROWING_OPTIONS_PROP_SENTINEL")


class FakeAppThrowingOptionsGet:
    class ThrowingGetDict:
        def get(self, key, default=None):
            raise RuntimeError("THROWING_OPTIONS_GET_SENTINEL")

    def __init__(self):
        self._options = self.ThrowingGetDict()


class NonDictOptions:
    def __init__(self, proj="tars-test-project"):
        self._proj = proj
        self.accessed_keys = []

    def get(self, key, default=None):
        self.accessed_keys.append(key)
        if key == "projectId":
            return self._proj
        return default


class FakeExistingAppWithLazyProperties:
    def __init__(self, proj="tars-test-project"):
        self._options = NonDictOptions(proj)
        self.project_id_accesses = 0
        self.credential_accesses = 0

    @property
    def project_id(self):
        self.project_id_accesses += 1
        raise AssertionError("LAZY_PROJECT_ID_PROPERTY_MUST_NOT_BE_ACCESSED")

    @property
    def credential(self):
        self.credential_accesses += 1
        raise AssertionError("LAZY_CREDENTIAL_PROPERTY_MUST_NOT_BE_ACCESSED")


def test_lifespan_firebase_lifecycle_causal_matrix(monkeypatch, caplog):
    """Full causal matrix for main.lifespan Firebase initialization, ordered stages, and failure containment."""
    import firebase_admin
    from backend import main

    settings = Settings(
        tars_runtime_mode="local",
        auth_bypass=False,
        google_cloud_project="tars-test-project",
        firebase_project_id="tars-test-project",
        auth_allowed_emails="recruiter@ellaexecutivesearch.com",
        auth_org_id="ella-internal",
    )

    def fresh_no_app():
        return ValueError(
            "The default Firebase app does not exist. Make sure to initialize the SDK by calling initialize_app()."
        )

    class StageTracker:
        def __init__(self):
            self.events: list[str] = []
            self.adc_probe = 0
            self.init_app = 0
            self.session_mgr_inst = None
            self.firestore_inst = None
            self.gcs_inst = None
            self.gemini_inst = None
            self.orphan_detected = False

    tracker = StageTracker()

    def assert_no_early_publication():
        assert main.app.state.ready is False
        assert main.settings is None
        assert main.session_mgr is None
        assert main.firestore_storage is None
        assert main.gcs_storage is None
        assert main.gemini_client is None
        assert main.context_window is None
        assert len(main.context_windows) == 0
        assert len(main.pipeline_tasks) == 0

    def wrapped_raw_gate(raw_env, resolved=None):
        assert_no_early_publication()
        if resolved is None:
            tracker.events.append("raw_env_gate")
        else:
            tracker.events.append("raw_vs_resolved_gate")
        return "local"

    def wrapped_resolve():
        assert_no_early_publication()
        tracker.events.append("resolve_settings")
        return settings

    def wrapped_auth_validate(s):
        assert_no_early_publication()
        tracker.events.append("validate_auth_config")

    async def counting_adc(proj=None):
        assert_no_early_publication()
        tracker.adc_probe += 1
        tracker.events.append("adc_probe")

    class TrackingSessionManager:
        def __init__(self, s):
            assert_no_early_publication()
            tracker.session_mgr_inst = self
            tracker.events.append("session_manager")
        def detect_orphaned_sessions(self):
            assert_no_early_publication()
            tracker.orphan_detected = True
            tracker.events.append("orphan_detect")
            return []

    class TrackingFirestoreStorage:
        def __init__(self, s):
            assert_no_early_publication()
            tracker.firestore_inst = self
            tracker.events.append("firestore")

    class TrackingGCSStorage:
        def __init__(self, s):
            assert_no_early_publication()
            tracker.gcs_inst = self
            tracker.events.append("gcs")

    class TrackingGeminiClient:
        def __init__(self, s):
            assert_no_early_publication()
            tracker.gemini_inst = self
            tracker.events.append("gemini")

    monkeypatch.setattr(main, "validate_raw_process_env", wrapped_raw_gate)
    monkeypatch.setattr(main, "resolve_settings_safely", wrapped_resolve)
    monkeypatch.setattr(main, "validate_auth_configuration", wrapped_auth_validate)
    monkeypatch.setattr(main, "probe_application_default_credentials", counting_adc)
    monkeypatch.setattr(main, "SessionManager", TrackingSessionManager)
    monkeypatch.setattr(main, "FirestoreStorage", TrackingFirestoreStorage)
    monkeypatch.setattr(main, "GCSStorage", TrackingGCSStorage)
    monkeypatch.setattr(main, "GeminiClient", TrackingGeminiClient)

    injected_same_class_exc = AuthConfigurationError("INIT_SAME_CLASS_SENTINEL")

    failure_cases = [
        # (name, get_app_seq_factory, init_app_side_effect / return_value, expected_exact_msg, sentinels)
        ("ordinary_init_error", lambda: [fresh_no_app(), fresh_no_app()], {"side_effect": RuntimeError("INIT_ORDINARY_SENTINEL")}, "Firebase Admin SDK initialization failed", ["INIT_ORDINARY_SENTINEL"]),
        ("same_class_init_error", lambda: [fresh_no_app(), fresh_no_app()], {"side_effect": injected_same_class_exc}, "Firebase Admin SDK initialization failed", ["INIT_SAME_CLASS_SENTINEL"]),
        ("throwing_str_init_error", lambda: [fresh_no_app(), fresh_no_app()], {"side_effect": ThrowingStrError()}, "Firebase Admin SDK initialization failed", ["THROWING_STR_SENTINEL_LEAK"]),
        ("throwing_repr_init_error", lambda: [fresh_no_app(), fresh_no_app()], {"side_effect": ThrowingReprError()}, "Firebase Admin SDK initialization failed", ["THROWING_REPR_SENTINEL_LEAK"]),
        ("throwing_both_init_error", lambda: [fresh_no_app(), fresh_no_app()], {"side_effect": ThrowingStrAndReprError()}, "Firebase Admin SDK initialization failed", ["THROWING_STR_SENTINEL_LEAK_BOTH", "THROWING_REPR_SENTINEL_LEAK_BOTH"]),
        ("init_returns_none", lambda: [fresh_no_app(), fresh_no_app()], {"return_value": None}, "Firebase Admin SDK initialization failed", []),
        ("init_missing_options", lambda: [fresh_no_app(), fresh_no_app()], {"return_value": FakeAppMissingOptions()}, "Firebase Admin SDK initialization failed", []),
        ("init_wrong_project_id", lambda: [fresh_no_app(), fresh_no_app()], {"return_value": FakeValidApp("wrong-proj")}, "Firebase Admin SDK initialization failed", []),
        ("init_missing_project_id", lambda: [fresh_no_app(), fresh_no_app()], {"return_value": FakeValidApp(None)}, "Firebase Admin SDK initialization failed", []),
        ("init_throwing_options_prop", lambda: [fresh_no_app(), fresh_no_app()], {"return_value": FakeAppThrowingOptionsProp()}, "Firebase Admin SDK initialization failed", ["THROWING_OPTIONS_PROP_SENTINEL"]),
        ("init_throwing_options_get", lambda: [fresh_no_app(), fresh_no_app()], {"return_value": FakeAppThrowingOptionsGet()}, "Firebase Admin SDK initialization failed", ["THROWING_OPTIONS_GET_SENTINEL"]),
        ("unrelated_second_get_app", lambda: [fresh_no_app(), ValueError("UNRELATED_GET_APP_SENTINEL")], {"return_value": FakeValidApp()}, "Existing Firebase app lookup failed", ["UNRELATED_GET_APP_SENTINEL"]),
    ]

    for name, get_app_seq_factory, init_behavior, exp_msg, sentinels in failure_cases:
        caplog.clear()
        tracker.events.clear()
        tracker.adc_probe = 0
        tracker.init_app = 0
        tracker.session_mgr_inst = None
        tracker.firestore_inst = None
        tracker.gcs_inst = None
        tracker.gemini_inst = None
        tracker.orphan_detected = False

        get_app_mock = SequencedGetApp(get_app_seq_factory(), tracker=tracker, assert_fn=assert_no_early_publication)
        monkeypatch.setattr(firebase_admin, "get_app", get_app_mock)

        def counting_init_app(*args, **kwargs):
            assert_no_early_publication()
            tracker.init_app += 1
            tracker.events.append("firebase_init")
            if "side_effect" in init_behavior:
                raise init_behavior["side_effect"]
            return init_behavior.get("return_value")

        monkeypatch.setattr(firebase_admin, "initialize_app", counting_init_app)

        main.app.state.ready = False

        async def run_failing_lifespan():
            async with main.lifespan(main.app):
                pass

        with pytest.raises(AuthConfigurationError) as exc_info:
            asyncio.run(run_failing_lifespan())

        err = exc_info.value
        assert str(err) == exp_msg
        assert err.__cause__ is None
        assert err.__context__ is None

        if name == "same_class_init_error":
            assert err is not injected_same_class_exc

        # Content-free exception hygiene
        for sentinel in sentinels:
            assert sentinel not in str(err)
            assert sentinel not in repr(err)
            formatted_tb = "".join(traceback.format_exception(type(err), err, err.__traceback__))
            assert sentinel not in formatted_tb
            assert sentinel not in caplog.text

        # Counts
        assert get_app_mock.call_count == 2
        assert tracker.adc_probe == 1
        if name == "unrelated_second_get_app":
            assert tracker.init_app == 0
            assert tracker.events == [
                "raw_env_gate",
                "resolve_settings",
                "raw_vs_resolved_gate",
                "validate_auth_config",
                "get_app[1]",
                "adc_probe",
                "get_app[2]",
            ]
        else:
            assert tracker.init_app == 1
            assert tracker.events == [
                "raw_env_gate",
                "resolve_settings",
                "raw_vs_resolved_gate",
                "validate_auth_config",
                "get_app[1]",
                "adc_probe",
                "get_app[2]",
                "firebase_init",
            ]

        # Zero downstream effects
        assert tracker.session_mgr_inst is None
        assert tracker.firestore_inst is None
        assert tracker.gcs_inst is None
        assert tracker.gemini_inst is None
        assert tracker.orphan_detected is False

        assert main.app.state.ready is False
        assert main.settings is None
        assert main.session_mgr is None
        assert main.firestore_storage is None
        assert main.gcs_storage is None
        assert main.gemini_client is None
        assert main.context_window is None

    # Positive Acceptance Row: canonical new initialize_app success
    caplog.clear()
    tracker.events.clear()
    tracker.adc_probe = 0
    tracker.init_app = 0
    tracker.session_mgr_inst = None
    tracker.firestore_inst = None
    tracker.gcs_inst = None
    tracker.gemini_inst = None
    tracker.orphan_detected = False

    pos_get_app = SequencedGetApp([fresh_no_app(), fresh_no_app()], tracker=tracker, assert_fn=assert_no_early_publication)
    monkeypatch.setattr(firebase_admin, "get_app", pos_get_app)

    def pos_init_app(*args, **kwargs):
        assert_no_early_publication()
        tracker.init_app += 1
        tracker.events.append("firebase_init")
        return FakeValidApp("tars-test-project")

    monkeypatch.setattr(firebase_admin, "initialize_app", pos_init_app)

    async def run_pos_lifespan():
        async with main.lifespan(main.app):
            tracker.events.append("ready_true")
            assert main.app.state.ready is True
            assert main.settings is settings
            assert main.session_mgr is tracker.session_mgr_inst
            assert main.firestore_storage is tracker.firestore_inst
            assert main.gcs_storage is tracker.gcs_inst
            assert main.gemini_client is tracker.gemini_inst
            assert main.context_window is None
            assert len(main.context_windows) == 0
            assert tracker.orphan_detected is True
            main.context_window = object()
            main.context_windows["s1"] = object()

    asyncio.run(run_pos_lifespan())

    assert pos_get_app.call_count == 2
    assert tracker.adc_probe == 1
    assert tracker.init_app == 1
    assert tracker.events == [
        "raw_env_gate",
        "resolve_settings",
        "raw_vs_resolved_gate",
        "validate_auth_config",
        "get_app[1]",
        "adc_probe",
        "get_app[2]",
        "firebase_init",
        "session_manager",
        "firestore",
        "gcs",
        "gemini",
        "orphan_detect",
        "ready_true",
    ]
    assert main.app.state.ready is False
    assert main.settings is None
    assert main.session_mgr is None
    assert main.firestore_storage is None
    assert main.gcs_storage is None
    assert main.gemini_client is None
    assert main.context_window is None
    assert len(main.context_windows) == 0

    # Existing Matching App Acceptance Row (init_app is 0, non-dict options, lazy accessors untouched)
    caplog.clear()
    tracker.events.clear()
    tracker.adc_probe = 0
    tracker.init_app = 0
    tracker.session_mgr_inst = None
    tracker.firestore_inst = None
    tracker.gcs_inst = None
    tracker.gemini_inst = None
    tracker.orphan_detected = False

    existing_app = FakeExistingAppWithLazyProperties("tars-test-project")
    existing_get_app = SequencedGetApp([existing_app, existing_app], tracker=tracker, assert_fn=assert_no_early_publication)
    monkeypatch.setattr(firebase_admin, "get_app", existing_get_app)

    async def run_existing_lifespan():
        async with main.lifespan(main.app):
            tracker.events.append("ready_true")
            assert main.app.state.ready is True
            assert main.settings is settings
            assert main.session_mgr is tracker.session_mgr_inst
            assert main.firestore_storage is tracker.firestore_inst
            assert main.gcs_storage is tracker.gcs_inst
            assert main.gemini_client is tracker.gemini_inst
            assert main.context_window is None
            assert len(main.context_windows) == 0
            assert tracker.orphan_detected is True
            main.context_window = object()
            main.context_windows["s1"] = object()

    asyncio.run(run_existing_lifespan())

    assert existing_get_app.call_count == 2
    assert tracker.adc_probe == 1
    assert tracker.init_app == 0
    assert existing_app.project_id_accesses == 0
    assert existing_app.credential_accesses == 0
    assert tracker.events == [
        "raw_env_gate",
        "resolve_settings",
        "raw_vs_resolved_gate",
        "validate_auth_config",
        "get_app[1]",
        "adc_probe",
        "get_app[2]",
        "session_manager",
        "firestore",
        "gcs",
        "gemini",
        "orphan_detect",
        "ready_true",
    ]
    assert main.app.state.ready is False
    assert main.settings is None
    assert main.session_mgr is None
    assert main.firestore_storage is None
    assert main.gcs_storage is None
    assert main.gemini_client is None
    assert main.context_window is None
    assert len(main.context_windows) == 0
    assert existing_app.credential_accesses == 0
    assert existing_app._options.accessed_keys == ["projectId", "projectId"]


def test_firebase_post_adc_initialize_app_canonical_matrix(monkeypatch):
    """Canonical post-ADC baseline initializes Firebase, and independent rows fail closed without leakage."""
    from backend import auth
    from backend.auth import initialize_firebase_admin, AuthConfigurationError

    class ThrowingStrError(Exception):
        def __str__(self):
            raise RuntimeError("SENTINEL_THROWING_STR")

    class FakeValidApp:
        def __init__(self, project_id="tars-test-project"):
            self._options = {"projectId": project_id}

    class FakeThrowingOptionsApp:
        @property
        def _options(self):
            raise RuntimeError("SENTINEL_THROWING_OPTIONS")

    canonical_err = ValueError("The default Firebase app does not exist. Make sure to initialize the SDK by calling initialize_app().")

    # 1. Passing baseline: get_app raises canonical error -> initialize_app succeeds and returns valid app
    with patch("backend.auth.firebase_admin.get_app", side_effect=canonical_err) as mock_get_app, \
         patch("backend.auth.firebase_admin.initialize_app", return_value=FakeValidApp("tars-test-project")) as mock_init:
        initialize_firebase_admin("tars-test-project")
        assert mock_get_app.call_count == 1
        assert mock_init.call_count == 1

    # 2. Independent failure rows
    failure_cases = [
        # (initialize_app side_effect / return_value, expected_leak_sentinel)
        ({"side_effect": RuntimeError("SENTINEL_INIT_RUNTIME_ERROR")}, "SENTINEL_INIT_RUNTIME_ERROR"),
        ({"side_effect": AuthConfigurationError("SENTINEL_INIT_AUTH_ERROR")}, "SENTINEL_INIT_AUTH_ERROR"),
        ({"side_effect": ThrowingStrError()}, "SENTINEL_THROWING_STR"),
        ({"return_value": None}, None),
        ({"return_value": FakeValidApp("wrong-project-id")}, None),
        ({"return_value": FakeValidApp(None)}, None),
        ({"return_value": FakeThrowingOptionsApp()}, "SENTINEL_THROWING_OPTIONS"),
    ]

    for patch_kwargs, sentinel in failure_cases:
        with patch("backend.auth.firebase_admin.get_app", side_effect=canonical_err) as mock_get_app, \
             patch("backend.auth.firebase_admin.initialize_app", **patch_kwargs) as mock_init:
            with pytest.raises(AuthConfigurationError) as exc_info:
                initialize_firebase_admin("tars-test-project")
            assert exc_info.value.__cause__ is None
            assert exc_info.value.__context__ is None
            if sentinel:
                assert sentinel not in str(exc_info.value)
            assert mock_get_app.call_count == 1
            assert mock_init.call_count == 1


def test_cors_production_resolution_and_sabotage_boundary(monkeypatch):
    """Production CORS construction fails closed on sabotage and binds parsed origins safely."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend import main
    from backend.auth import AuthConfigurationError

    # 1. Sabotage raw env gate -> AuthConfigurationError with clean cause/context
    sabotaged_raw_env = {"TARS_RUNTIME_MODE": "invalid-mode"}
    test_app_1 = FastAPI()
    with pytest.raises(AuthConfigurationError) as exc_1:
        main.configure_cors(test_app_1, raw_env=sabotaged_raw_env)
    assert exc_1.value.__cause__ is None
    assert exc_1.value.__context__ is None

    # 2. Sabotage resolve_cors_settings_safely -> AuthConfigurationError
    test_app_2 = FastAPI()
    with monkeypatch.context() as m:
        m.setattr(main, "resolve_cors_settings_safely", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SENTINEL_RESOLVE_SABOTAGE")))
        with pytest.raises(AuthConfigurationError) as exc_2:
            main.configure_cors(test_app_2)
        assert exc_2.value.__cause__ is None
        assert exc_2.value.__context__ is None
        assert "SENTINEL_RESOLVE_SABOTAGE" not in str(exc_2.value)

    # 3. Sabotage parse_cors_allowed_origins -> AuthConfigurationError
    test_app_3 = FastAPI()
    with monkeypatch.context() as m:
        m.setattr(main, "parse_cors_allowed_origins", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SENTINEL_PARSER_SABOTAGE")))
        with pytest.raises(AuthConfigurationError) as exc_3:
            main.configure_cors(test_app_3)
        assert exc_3.value.__cause__ is None
        assert exc_3.value.__context__ is None
        assert "SENTINEL_PARSER_SABOTAGE" not in str(exc_3.value)

    # 4. Sabotage add_middleware -> AuthConfigurationError
    test_app_4 = FastAPI()
    with monkeypatch.context() as m:
        m.setattr(test_app_4, "add_middleware", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SENTINEL_MIDDLEWARE_SABOTAGE")))
        with pytest.raises(AuthConfigurationError) as exc_4:
            main.configure_cors(test_app_4)
        assert exc_4.value.__cause__ is None
        assert exc_4.value.__context__ is None
        assert "SENTINEL_MIDDLEWARE_SABOTAGE" not in str(exc_4.value)

    # 5. Production safe resolution with secondary source honors allowlist and binds CORSMiddleware
    test_app_5 = FastAPI()
    valid_raw_env = {
        "GOOGLE_CLOUD_PROJECT": "tars-test-project",
        "AUTH_ALLOWED_EMAILS": "recruiter@ellaexecutivesearch.com",
        "AUTH_ORG_ID": "ella-internal",
    }
    secondary_source = {"cors_allowed_origins": "http://localhost:3000,http://127.0.0.1:3000"}
    allowed = main.configure_cors(test_app_5, raw_env=valid_raw_env, secondary_source=secondary_source)
    assert allowed == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert "https://attacker.invalid" not in allowed

    # Full preflight checks: allowed vs disallowed
    client = TestClient(test_app_5)
    try:
        res_allowed = client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert res_allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"

        res_disallowed = client.options(
            "/healthz",
            headers={
                "Origin": "https://attacker.invalid",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert "access-control-allow-origin" not in res_disallowed.headers
    finally:
        client.close()
