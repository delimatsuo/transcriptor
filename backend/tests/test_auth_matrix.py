"""Focused HTTP and WebSocket authorization matrix for the internal tenancy gate.

All provider and persistence calls in this module are mocked.  These tests are
deliberately narrower than an end-to-end Firebase/Firestore run: they prove
that the ASGI boundary, one-time capabilities, and owner/org checks fail closed
before a route or socket can touch interview data.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
import concurrent.futures
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse
from starlette.websockets import WebSocketDisconnect

from backend import main
from backend.auth import (
    AuthContext,
    AuthenticationError,
    auth_is_enforced,
    current_auth,
    verify_bearer_token,
)
from backend.config import Settings
from backend.schemas.models import SessionStatus


def auth_settings(**overrides) -> Settings:
    values = {
        "google_cloud_project": "tars-test-project",
        "auth_allowed_emails": "recruiter@example.com",
        "auth_org_id": "ella-internal",
        "auth_bypass": False,
    }
    values.update(overrides)
    return Settings(**values)


def claims(**overrides) -> dict:
    value = {
        "sub": "uid-a",
        "uid": "uid-a",
        "email": "recruiter@example.com",
        "email_verified": True,
        "aud": "tars-test-project",
        "iss": "https://securetoken.google.com/tars-test-project",
    }
    value.update(overrides)
    return value


@pytest.fixture(autouse=True)
def isolate_main_state(monkeypatch):
    # 1. Capture original callable identities and entry values
    orig_set_current_auth = main.set_current_auth
    orig_reset_current_auth = main.reset_current_auth
    orig_set_auth_enforced = main.set_auth_enforced
    orig_reset_auth_enforced = main.reset_auth_enforced
    orig_current_auth = main.current_auth
    orig_auth_is_enforced = main.auth_is_enforced

    entry_current_auth_val = orig_current_auth()
    entry_auth_is_enforced_val = orig_auth_is_enforced()

    boundary_auth_token = None
    boundary_enforced_token = None
    cleanup_failures: list[str] = []

    # Outermost cleanup layer guarantees callable & token restoration even on setup failure
    try:
        try:
            boundary_auth_token = orig_set_current_auth(None)
        except BaseException:
            cleanup_failures.append("BOUNDARY_AUTH_TOKEN_SET_FAILED")

        try:
            boundary_enforced_token = orig_set_auth_enforced(False)
        except BaseException:
            cleanup_failures.append("BOUNDARY_ENFORCED_TOKEN_SET_FAILED")

        # 2. Capture ready & lock attributes
        orig_has_ready = hasattr(main.app.state, "ready")
        orig_ready = getattr(main.app.state, "ready", None)
        orig_has_native_sm_lock = hasattr(main, "native_sm_lock")
        orig_native_sm_lock = getattr(main, "native_sm_lock", None)

        # 3. Capture module singletons
        orig_settings = main.settings
        orig_session_mgr = main.session_mgr
        orig_firestore = main.firestore_storage
        orig_gcs = main.gcs_storage
        orig_gemini = main.gemini_client
        orig_context_window = main.context_window
        orig_ws_manager = main.ws_manager

        # 4. Capture mutable globals (object identity & state-family snapshots)
        flat_dict_names = (
            "ws_tickets",
            "stop_capabilities",
            "stream_keys",
            "extension_tokens",
            "context_windows",
            "session_stop_locks",
            "final_summary_tasks",
            "rolling_summary_tasks",
            "interview_suggestion_tasks",
            "single_source_check_tasks",
            "extension_capability_expiry",
            "interview_final_segment_counts",
            "interview_suggestion_counters",
            "speaker_correlators",
            "_clock_sync_timestamps",
        )
        flat_set_names = (
            "deleted_sessions",
            "transcript_persistence_failures",
            "session_deletion_fences",
            "final_summary_scheduled",
            "rolling_summary_followups",
            "single_source_warned",
        )

        flat_dict_refs = {name: getattr(main, name) for name in flat_dict_names}
        flat_dict_snaps = {name: dict(ref) for name, ref in flat_dict_refs.items()}

        flat_set_refs = {name: getattr(main, name) for name in flat_set_names}
        flat_set_snaps = {name: set(ref) for name, ref in flat_set_refs.items()}

        list_dict_names = (
            "pipeline_tasks",
            "audio_captures",
            "audio_buffers",
            "stream_managers",
        )
        list_dict_refs = {name: getattr(main, name) for name in list_dict_names}
        list_dict_snaps = {
            name: {k: (v, list(v)) for k, v in ref.items()}
            for name, ref in list_dict_refs.items()
        }

        dict_dict_names = (
            "native_stream_managers",
            "native_frame_last_seq",
            "interview_documents",
        )
        dict_dict_refs = {name: getattr(main, name) for name in dict_dict_names}
        dict_dict_snaps = {
            name: {k: (v, dict(v)) for k, v in ref.items()}
            for name, ref in dict_dict_refs.items()
        }

        health_ref = main.native_session_health
        health_snap = {}
        for sess_id, sess_map in health_ref.items():
            health_snap[sess_id] = {
                "__sess_dict__": sess_map,
                "sources": (sess_map.get("sources"), dict(sess_map.get("sources", {}))),
                "source_connections": (
                    sess_map.get("source_connections"),
                    dict(sess_map.get("source_connections", {})),
                ),
                "alerts": (sess_map.get("alerts"), dict(sess_map.get("alerts", {}))),
                "scalars": {
                    k: v
                    for k, v in sess_map.items()
                    if k not in ("sources", "source_connections", "alerts")
                },
            }

        # 5. Capture ws_manager internal mapping identities and contents
        orig_wsm_conn_ref = getattr(orig_ws_manager, "_connections", None)
        orig_wsm_buf_ref = getattr(orig_ws_manager, "_message_buffer", None)
        orig_wsm_seq_ref = getattr(orig_ws_manager, "_sequence_counters", None)

        orig_wsm_conn_snap = (
            {k: (v, list(v)) for k, v in orig_wsm_conn_ref.items()}
            if orig_wsm_conn_ref is not None
            else {}
        )
        orig_wsm_buf_snap = (
            {
                k: (v, list(v), getattr(v, "maxlen", None))
                for k, v in orig_wsm_buf_ref.items()
            }
            if orig_wsm_buf_ref is not None
            else {}
        )
        orig_wsm_seq_snap = (
            dict(orig_wsm_seq_ref) if orig_wsm_seq_ref is not None else {}
        )

        # 6. Cycle-safe recursive collector for Task objects
        def collect_tasks_recursive(obj, target_set: set[asyncio.Task], seen_containers: set[int]):
            if isinstance(obj, asyncio.Task):
                target_set.add(obj)
                return
            obj_id = id(obj)
            if obj_id in seen_containers:
                return
            seen_containers.add(obj_id)
            if isinstance(obj, Mapping):
                for val in obj.values():
                    collect_tasks_recursive(val, target_set, seen_containers)
            elif isinstance(obj, (list, tuple, set, frozenset, deque)):
                for item in obj:
                    collect_tasks_recursive(item, target_set, seen_containers)

        def scan_all_views(target_set: set[asyncio.Task], seen_set: set[int]):
            for name in flat_dict_names + flat_set_names + list_dict_names + dict_dict_names:
                collect_tasks_recursive(getattr(main, name, None), target_set, seen_set)
            collect_tasks_recursive(getattr(main, "native_session_health", None), target_set, seen_set)

            curr_wsm = getattr(main, "ws_manager", None)
            if curr_wsm is not None:
                collect_tasks_recursive(getattr(curr_wsm, "_connections", None), target_set, seen_set)
                collect_tasks_recursive(getattr(curr_wsm, "_message_buffer", None), target_set, seen_set)
                collect_tasks_recursive(getattr(curr_wsm, "_sequence_counters", None), target_set, seen_set)

            if orig_wsm_conn_ref is not None:
                collect_tasks_recursive(orig_wsm_conn_ref, target_set, seen_set)
            if orig_wsm_buf_ref is not None:
                collect_tasks_recursive(orig_wsm_buf_ref, target_set, seen_set)
            if orig_wsm_seq_ref is not None:
                collect_tasks_recursive(orig_wsm_seq_ref, target_set, seen_set)

            for ref in flat_dict_refs.values():
                collect_tasks_recursive(ref, target_set, seen_set)
            for snap in flat_dict_snaps.values():
                collect_tasks_recursive(snap, target_set, seen_set)
            for ref in flat_set_refs.values():
                collect_tasks_recursive(ref, target_set, seen_set)
            for snap in flat_set_snaps.values():
                collect_tasks_recursive(snap, target_set, seen_set)
            for ref in list_dict_refs.values():
                collect_tasks_recursive(ref, target_set, seen_set)
            for snap_map in list_dict_snaps.values():
                for inner_list, elems in snap_map.values():
                    collect_tasks_recursive(inner_list, target_set, seen_set)
                    collect_tasks_recursive(elems, target_set, seen_set)
            for ref in dict_dict_refs.values():
                collect_tasks_recursive(ref, target_set, seen_set)
            for snap_map in dict_dict_snaps.values():
                for inner_dict, items in snap_map.values():
                    collect_tasks_recursive(inner_dict, target_set, seen_set)
                    collect_tasks_recursive(items, target_set, seen_set)
            collect_tasks_recursive(health_ref, target_set, seen_set)
            for sess_info in health_snap.values():
                collect_tasks_recursive(sess_info.get("__sess_dict__"), target_set, seen_set)
                collect_tasks_recursive(sess_info.get("scalars"), target_set, seen_set)
                src_obj, src_items = sess_info.get("sources", (None, {}))
                collect_tasks_recursive(src_obj, target_set, seen_set)
                collect_tasks_recursive(src_items, target_set, seen_set)
                conn_obj, conn_items = sess_info.get("source_connections", (None, {}))
                collect_tasks_recursive(conn_obj, target_set, seen_set)
                collect_tasks_recursive(conn_items, target_set, seen_set)
                alt_obj, alt_items = sess_info.get("alerts", (None, {}))
                collect_tasks_recursive(alt_obj, target_set, seen_set)
                collect_tasks_recursive(alt_items, target_set, seen_set)
            for inner_list, elems in orig_wsm_conn_snap.values():
                collect_tasks_recursive(inner_list, target_set, seen_set)
                collect_tasks_recursive(elems, target_set, seen_set)
            for inner_deque, elems, _ in orig_wsm_buf_snap.values():
                collect_tasks_recursive(inner_deque, target_set, seen_set)
                collect_tasks_recursive(elems, target_set, seen_set)
            collect_tasks_recursive(orig_wsm_seq_snap, target_set, seen_set)

        entry_tasks: set[asyncio.Task] = set()
        seen_entry_containers: set[int] = set()
        scan_all_views(entry_tasks, seen_entry_containers)

        try:
            yield
        finally:
            # Step A: Collect and settle added tasks before undo
            try:
                teardown_tasks: set[asyncio.Task] = set()
                seen_td_containers: set[int] = set()
                scan_all_views(teardown_tasks, seen_td_containers)

                delta_tasks = teardown_tasks - entry_tasks

                def normalize_helper_res(res: Any) -> str:
                    if type(res) is not str:
                        return "INVALID_HELPER_RESULT"
                    if res == "SETTLED":
                        return "SETTLED"
                    elif res == "UNSETTLED_TASK_REMAINS":
                        return "UNSETTLED_TASK_REMAINS"
                    elif res == "TASK_TERMINAL_EXCEPTION":
                        return "TASK_TERMINAL_EXCEPTION"
                    elif res == "TASK_OUTCOME_RETRIEVAL_FAILED":
                        return "TASK_OUTCOME_RETRIEVAL_FAILED"
                    elif res == "WAIT_EXCEPTION":
                        return "WAIT_EXCEPTION"
                    else:
                        return "INVALID_HELPER_RESULT"

                # Coroutine helper for cancel and wait using asyncio.wait
                async def _cancel_and_wait(task: asyncio.Task) -> str:
                    if not task.done():
                        task.cancel()
                    try:
                        done, _ = await asyncio.wait({task}, timeout=2.0)
                        if not done:
                            return "UNSETTLED_TASK_REMAINS"
                    except BaseException:
                        return "WAIT_EXCEPTION"
                    if task.done():
                        try:
                            exc = task.exception()
                            if exc is not None:
                                return "TASK_TERMINAL_EXCEPTION"
                        except asyncio.CancelledError:
                            return "SETTLED"
                        except BaseException:
                            return "TASK_OUTCOME_RETRIEVAL_FAILED"
                        return "SETTLED"
                    return "UNSETTLED_TASK_REMAINS"

                for t in delta_tasks:
                    if t.done():
                        try:
                            exc = t.exception()
                            if exc is not None:
                                cleanup_failures.append("TASK_TERMINAL_EXCEPTION")
                        except asyncio.CancelledError:
                            pass
                        except BaseException:
                            cleanup_failures.append("TASK_OUTCOME_RETRIEVAL_FAILED")
                    else:
                        try:
                            t_loop = t.get_loop()
                            if t_loop.is_closed():
                                try:
                                    t.cancel()
                                except BaseException:
                                    pass
                                cleanup_failures.append("LOOP_CLOSED_WITH_PENDING_TASK")
                            elif not t_loop.is_running():
                                res = t_loop.run_until_complete(_cancel_and_wait(t))
                                norm_code = normalize_helper_res(res)
                                if norm_code != "SETTLED":
                                    cleanup_failures.append(norm_code)
                            else:
                                try:
                                    curr_loop = asyncio.get_running_loop()
                                except RuntimeError:
                                    curr_loop = None
                                if curr_loop is t_loop:
                                    try:
                                        t.cancel()
                                    except BaseException:
                                        pass
                                    cleanup_failures.append("PENDING_TASK_IN_CURRENT_RUNNING_LOOP")
                                else:
                                    coro = _cancel_and_wait(t)
                                    fut = None
                                    try:
                                        fut = asyncio.run_coroutine_threadsafe(coro, t_loop)
                                    except BaseException:
                                        try:
                                            coro.close()
                                        except BaseException:
                                            pass
                                        cleanup_failures.append("THREADSAFE_SUBMISSION_FAILED")

                                    if fut is not None:
                                        first_res_ok = False
                                        try:
                                            res = fut.result(timeout=3.0)
                                            norm_code = normalize_helper_res(res)
                                            if norm_code != "SETTLED":
                                                cleanup_failures.append(norm_code)
                                            else:
                                                first_res_ok = True
                                        except BaseException:
                                            cleanup_failures.append("THREADSAFE_FIRST_RESULT_FAILED")

                                        if not first_res_ok:
                                            try:
                                                cancel_res = fut.cancel()
                                                if type(cancel_res) is not bool or (not cancel_res and not fut.done()):
                                                    cleanup_failures.append("THREADSAFE_HELPER_CANCEL_FAILED")
                                            except BaseException:
                                                cleanup_failures.append("THREADSAFE_HELPER_CANCEL_FAILED")
                                            try:
                                                res2 = fut.result(timeout=0.2)
                                                norm2 = normalize_helper_res(res2)
                                                if norm2 != "SETTLED":
                                                    cleanup_failures.append(norm2)
                                            except concurrent.futures.CancelledError:
                                                pass
                                            except concurrent.futures.TimeoutError:
                                                cleanup_failures.append("THREADSAFE_SECOND_TIMEOUT")
                                            except BaseException:
                                                cleanup_failures.append("THREADSAFE_SECOND_RESULT_FAILED")
                                            if not fut.done():
                                                cleanup_failures.append("THREADSAFE_HELPER_STILL_PENDING")
                        except BaseException:
                            cleanup_failures.append("TASK_SETTLEMENT_FAILED")

                if any(not t.done() for t in delta_tasks):
                    cleanup_failures.append("UNSETTLED_TASK_REMAINS")
            except BaseException:
                cleanup_failures.append("TASK_CLEANUP_FAILED")

            # Step B: monkeypatch.undo()
            try:
                monkeypatch.undo()
            except BaseException:
                cleanup_failures.append("UNDO_FAILED")

            # Step C: Flag / lock restoration (per-object)
            try:
                if orig_has_ready:
                    main.app.state.ready = orig_ready
                elif hasattr(main.app.state, "ready"):
                    delattr(main.app.state, "ready")
            except BaseException:
                cleanup_failures.append("READY_RESTORE_FAILED")

            try:
                if orig_has_native_sm_lock:
                    main.native_sm_lock = orig_native_sm_lock
                elif hasattr(main, "native_sm_lock"):
                    delattr(main, "native_sm_lock")
            except BaseException:
                cleanup_failures.append("LOCK_RESTORE_FAILED")

            # Step D: Singleton restoration (per-object)
            try:
                main.settings = orig_settings
            except BaseException:
                cleanup_failures.append("SETTINGS_RESTORE_FAILED")

            try:
                main.session_mgr = orig_session_mgr
            except BaseException:
                cleanup_failures.append("SESSION_MGR_RESTORE_FAILED")

            try:
                main.firestore_storage = orig_firestore
            except BaseException:
                cleanup_failures.append("FIRESTORE_RESTORE_FAILED")

            try:
                main.gcs_storage = orig_gcs
            except BaseException:
                cleanup_failures.append("GCS_RESTORE_FAILED")

            try:
                main.gemini_client = orig_gemini
            except BaseException:
                cleanup_failures.append("GEMINI_RESTORE_FAILED")

            try:
                main.context_window = orig_context_window
            except BaseException:
                cleanup_failures.append("CONTEXT_WINDOW_RESTORE_FAILED")

            try:
                main.ws_manager = orig_ws_manager
            except BaseException:
                cleanup_failures.append("WS_MANAGER_RESTORE_FAILED")

            # Step E: Flat dicts and sets (per-object with literal closed codes)
            for name, orig_dict in flat_dict_refs.items():
                try:
                    setattr(main, name, orig_dict)
                    orig_dict.clear()
                    orig_dict.update(flat_dict_snaps[name])
                except BaseException:
                    cleanup_failures.append("FLAT_DICT_RESTORE_FAILED")

            for name, orig_set in flat_set_refs.items():
                try:
                    setattr(main, name, orig_set)
                    orig_set.clear()
                    orig_set.update(flat_set_snaps[name])
                except BaseException:
                    cleanup_failures.append("FLAT_SET_RESTORE_FAILED")

            # Step F: List dicts (split outer and inner attempts with literal closed codes)
            for name, orig_dict in list_dict_refs.items():
                try:
                    setattr(main, name, orig_dict)
                    orig_dict.clear()
                except BaseException:
                    cleanup_failures.append("LIST_DICT_OUTER_RESTORE_FAILED")
                for k, (orig_inner_list, elems) in list_dict_snaps[name].items():
                    try:
                        orig_inner_list.clear()
                        orig_inner_list.extend(elems)
                        orig_dict[k] = orig_inner_list
                    except BaseException:
                        cleanup_failures.append("LIST_DICT_INNER_RESTORE_FAILED")

            # Step G: Dict dicts (split outer and inner attempts with literal closed codes)
            for name, orig_dict in dict_dict_refs.items():
                try:
                    setattr(main, name, orig_dict)
                    orig_dict.clear()
                except BaseException:
                    cleanup_failures.append("DICT_DICT_OUTER_RESTORE_FAILED")
                for k, (orig_inner_dict, items) in dict_dict_snaps[name].items():
                    try:
                        orig_inner_dict.clear()
                        orig_inner_dict.update(items)
                        orig_dict[k] = orig_inner_dict
                    except BaseException:
                        cleanup_failures.append("DICT_DICT_INNER_RESTORE_FAILED")

            # Step H: Health restoration (split outer and per-field inner attempts)
            try:
                setattr(main, "native_session_health", health_ref)
                health_ref.clear()
            except BaseException:
                cleanup_failures.append("HEALTH_OUTER_RESTORE_FAILED")

            for sess_id, sess_info in health_snap.items():
                sess_dict = sess_info["__sess_dict__"]
                try:
                    sess_dict.clear()
                    sess_dict.update(sess_info["scalars"])
                except BaseException:
                    cleanup_failures.append("HEALTH_SESS_RESTORE_FAILED")

                try:
                    sources_obj, sources_items = sess_info["sources"]
                    if sources_obj is not None:
                        sources_obj.clear()
                        sources_obj.update(sources_items)
                        sess_dict["sources"] = sources_obj
                except BaseException:
                    cleanup_failures.append("HEALTH_SOURCES_RESTORE_FAILED")

                try:
                    source_conn_obj, source_conn_items = sess_info["source_connections"]
                    if source_conn_obj is not None:
                        source_conn_obj.clear()
                        source_conn_obj.update(source_conn_items)
                        sess_dict["source_connections"] = source_conn_obj
                except BaseException:
                    cleanup_failures.append("HEALTH_CONNECTIONS_RESTORE_FAILED")

                try:
                    alerts_obj, alerts_items = sess_info["alerts"]
                    if alerts_obj is not None:
                        alerts_obj.clear()
                        alerts_obj.update(alerts_items)
                        sess_dict["alerts"] = alerts_obj
                except BaseException:
                    cleanup_failures.append("HEALTH_ALERTS_RESTORE_FAILED")

                try:
                    health_ref[sess_id] = sess_dict
                except BaseException:
                    cleanup_failures.append("HEALTH_REINSERT_FAILED")

            # Step I: ws_manager internal mappings (split outer and inner attempts)
            if orig_wsm_conn_ref is not None:
                try:
                    setattr(orig_ws_manager, "_connections", orig_wsm_conn_ref)
                    orig_wsm_conn_ref.clear()
                except BaseException:
                    cleanup_failures.append("WSM_CONNECTIONS_OUTER_RESTORE_FAILED")
                for k, (orig_conn_list, elems) in orig_wsm_conn_snap.items():
                    try:
                        orig_conn_list.clear()
                        orig_conn_list.extend(elems)
                        orig_wsm_conn_ref[k] = orig_conn_list
                    except BaseException:
                        cleanup_failures.append("WSM_CONNECTIONS_INNER_RESTORE_FAILED")

            if orig_wsm_buf_ref is not None:
                try:
                    setattr(orig_ws_manager, "_message_buffer", orig_wsm_buf_ref)
                    orig_wsm_buf_ref.clear()
                except BaseException:
                    cleanup_failures.append("WSM_MESSAGE_BUFFER_OUTER_RESTORE_FAILED")
                for k, (orig_deque, elems, maxlen) in orig_wsm_buf_snap.items():
                    try:
                        orig_deque.clear()
                        orig_deque.extend(elems)
                        orig_wsm_buf_ref[k] = orig_deque
                    except BaseException:
                        cleanup_failures.append("WSM_MESSAGE_BUFFER_INNER_RESTORE_FAILED")

            if orig_wsm_seq_ref is not None:
                try:
                    setattr(orig_ws_manager, "_sequence_counters", orig_wsm_seq_ref)
                    orig_wsm_seq_ref.clear()
                    orig_wsm_seq_ref.update(orig_wsm_seq_snap)
                except BaseException:
                    cleanup_failures.append("WSM_SEQUENCE_COUNTERS_RESTORE_FAILED")
    finally:
        # Outermost final layer: token resets and callable restoration (each individually protected)
        if boundary_auth_token is not None:
            try:
                orig_reset_current_auth(boundary_auth_token)
            except BaseException:
                cleanup_failures.append("AUTH_TOKEN_RESET_FAILED")

        if boundary_enforced_token is not None:
            try:
                orig_reset_auth_enforced(boundary_enforced_token)
            except BaseException:
                cleanup_failures.append("ENFORCED_TOKEN_RESET_FAILED")

        try:
            main.set_current_auth = orig_set_current_auth
        except BaseException:
            cleanup_failures.append("RESTORE_CALLABLE_SET_AUTH_FAILED")

        try:
            main.reset_current_auth = orig_reset_current_auth
        except BaseException:
            cleanup_failures.append("RESTORE_CALLABLE_RESET_AUTH_FAILED")

        try:
            main.set_auth_enforced = orig_set_auth_enforced
        except BaseException:
            cleanup_failures.append("RESTORE_CALLABLE_SET_ENFORCED_FAILED")

        try:
            main.reset_auth_enforced = orig_reset_auth_enforced
        except BaseException:
            cleanup_failures.append("RESTORE_CALLABLE_RESET_ENFORCED_FAILED")

        try:
            main.current_auth = orig_current_auth
        except BaseException:
            cleanup_failures.append("RESTORE_CALLABLE_CURRENT_AUTH_FAILED")

        try:
            main.auth_is_enforced = orig_auth_is_enforced
        except BaseException:
            cleanup_failures.append("RESTORE_CALLABLE_AUTH_IS_ENFORCED_FAILED")

        try:
            if (
                orig_current_auth() is not entry_current_auth_val
                or orig_auth_is_enforced() != entry_auth_is_enforced_val
            ):
                cleanup_failures.append("AUTH_CONTEXT_MISMATCH")
        except BaseException:
            cleanup_failures.append("AUTH_CONTEXT_CHECK_FAILED")

        if cleanup_failures:
            raise AssertionError("FIXTURE_ISOLATION_FAILURE") from None


@pytest.mark.parametrize("authorization", [None, "", "Basic token", "Bearer "])
def test_missing_or_malformed_bearer_is_rejected_without_provider_call(authorization):
    with patch("backend.auth.firebase_auth.verify_id_token") as verify:
        with pytest.raises(AuthenticationError):
            verify_bearer_token(authorization, auth_settings())
    verify.assert_not_called()


def test_revoked_token_is_rejected_with_provider_revocation_check():
    with patch(
        "backend.auth.firebase_auth.verify_id_token",
        side_effect=RuntimeError("revoked"),
    ) as verify:
        with pytest.raises(AuthenticationError):
            verify_bearer_token("Bearer revoked", auth_settings())
    verify.assert_called_once_with("revoked", check_revoked=True)


@pytest.mark.parametrize(
    "token_claims",
    [
        claims(email_verified=False),
        claims(email="not-provisioned@example.com"),
        claims(aud="another-project"),
        claims(iss="https://securetoken.google.com/another-project"),
    ],
)
def test_unverified_unallowlisted_or_wrong_audience_identity_is_rejected(token_claims):
    with patch("backend.auth.firebase_auth.verify_id_token", return_value=token_claims):
        with pytest.raises(AuthenticationError):
            verify_bearer_token("Bearer token", auth_settings())


def _request(path: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> Request:
    encoded_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": encoded_headers,
            "client": ("testclient", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
            "app": main.app,
        }
    )


def _run_middleware(path: str, *, headers: dict[str, str] | None = None):
    async def endpoint(_request):
        return PlainTextResponse("ok")

    return asyncio.run(
        main.authenticate_api_requests(
            _request(path, headers=headers),
            endpoint,
        )
    )


def test_api_auth_boundary_rejects_route_requests_but_leaves_health_probe_public(
    monkeypatch,
):
    monkeypatch.setattr(main, "settings", auth_settings())
    main.app.state.ready = True
    denied = _run_middleware("/api/me")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert _run_middleware("/healthz").status_code == 200


@pytest.mark.parametrize(
    "path",
    sorted(
        {
            route.path
            for route in main.app.routes
            if getattr(route, "path", "").startswith("/api/")
        }
    ),
)
def test_every_api_route_pattern_is_inside_the_auth_boundary(path, monkeypatch):
    monkeypatch.setattr(main, "settings", auth_settings())
    main.app.state.ready = True
    response = _run_middleware(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_cors_headers_survive_auth_401():
    async def request():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(
                "/api/me",
                headers={"Origin": "http://localhost:3000"},
            )

    old_settings = main.settings
    old_ready = main.app.state.ready
    main.settings = auth_settings()
    main.app.state.ready = True
    try:
        response = asyncio.run(request())
    finally:
        main.settings = old_settings
        main.app.state.ready = old_ready

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_cross_owner_mutation_is_non_enumerating_404_at_http_boundary(monkeypatch):
    class FakeSessionManager:
        def get_session(self, _session_id):
            return SimpleNamespace(owner_id="uid-a", org_id="ella-internal")

    async def request():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/sessions/s1/speakers",
                headers={"Authorization": "Bearer token"},
                json={"candidate": "Candidato"},
            )

    monkeypatch.setattr(main, "settings", auth_settings())
    monkeypatch.setattr(main, "session_mgr", FakeSessionManager())
    monkeypatch.setattr(
        main,
        "verify_bearer_token",
        lambda _authorization, _settings: AuthContext(
            "uid-b", "other@example.com", "ella-internal"
        ),
    )
    main.app.state.ready = True
    response = asyncio.run(request())
    assert response.status_code == 404
    assert response.json() == {"detail": "Session not found"}


@pytest.mark.parametrize(
    "record",
    [
        {"ownerId": "uid-b", "orgId": "ella-internal"},
        {"ownerId": "uid-a", "orgId": "other-org"},
        {},
    ],
)
def test_child_records_missing_or_outside_parent_scope_are_rejected(record):
    user_token = main.set_current_auth(AuthContext("uid-a", "a@example.com", "ella-internal"))
    enforced_token = main.set_auth_enforced()
    session = SimpleNamespace(owner_id="uid-a", org_id="ella-internal")
    try:
        with pytest.raises(HTTPException) as exc_info:
            main._assert_child_scope([record], session)
        assert exc_info.value.status_code == 404
    finally:
        main.reset_current_auth(user_token)
        main.reset_auth_enforced(enforced_token)


def test_stop_capability_is_bounded_and_only_fallback_for_matching_stop_route(monkeypatch):
    settings = auth_settings(auth_stop_capability_ttl_seconds=120)
    monkeypatch.setattr(main, "settings", settings)
    main.app.state.ready = True
    main.stop_capabilities.clear()
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    try:
        minted = main._mint_capability(main.stop_capabilities, user, "s1", 120)
        owner, session_id, expires_at = main.stop_capabilities[minted]
        assert owner == user
        assert session_id == "s1"
        assert expires_at > datetime.now(timezone.utc) + timedelta(seconds=119)

        accepted = _run_middleware(
            "/api/sessions/s1/stop",
            headers={"X-TARS-Stop-Capability": minted},
        )
        assert accepted.status_code == 200

        wrong_session = _run_middleware(
            "/api/sessions/s2/stop",
            headers={"X-TARS-Stop-Capability": minted},
        )
        assert wrong_session.status_code == 401

        main.stop_capabilities[minted] = (user, "s1", datetime.now(timezone.utc) - timedelta(seconds=1))
        expired = _run_middleware(
            "/api/sessions/s1/stop",
            headers={"X-TARS-Stop-Capability": minted},
        )
        assert expired.status_code == 401
    finally:
        main.stop_capabilities.clear()


def test_incomplete_stop_keeps_recovery_capability_until_terminal_write_succeeds(
    monkeypatch,
):
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    session = SimpleNamespace(
        id="s1",
        owner_id=user.uid,
        org_id=user.org_id,
        status=SessionStatus.INCOMPLETE,
        mode="meeting",
    )

    class FakeSessionManager:
        def get_session(self, _session_id):
            return session

    class FlakyFirestore:
        def __init__(self):
            self.calls = 0

        async def save_session(self, _session):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient Firestore failure")

    firestore = FlakyFirestore()
    old_session_mgr = main.session_mgr
    old_firestore = main.firestore_storage
    old_locks = main.session_stop_locks.copy()
    main.session_stop_locks.clear()
    main.stop_capabilities.clear()
    main.session_mgr = FakeSessionManager()
    main.firestore_storage = firestore
    auth_token = main.set_current_auth(user)
    capability = main._mint_capability(main.stop_capabilities, user, session.id, 120)

    async def no_pipeline(_session_id):
        raise AssertionError("incomplete retry must not restart the pipeline")

    monkeypatch.setattr(main, "_stop_pipeline", no_pipeline)
    try:
        with pytest.raises(RuntimeError, match="transient Firestore failure"):
            asyncio.run(main.stop_session(session.id))
        assert capability in main.stop_capabilities

        result = asyncio.run(main.stop_session(session.id))
        assert result["transcription_complete"] is False
        assert capability not in main.stop_capabilities
        assert firestore.calls == 2
    finally:
        main.reset_current_auth(auth_token)
        main.stop_capabilities.clear()
        main.session_stop_locks.clear()
        main.session_stop_locks.update(old_locks)
        main.session_mgr = old_session_mgr
        main.firestore_storage = old_firestore


def test_active_session_delete_is_rejected_before_storage_mutation(monkeypatch):
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    session = SimpleNamespace(
        id="active-session",
        owner_id=user.uid,
        org_id=user.org_id,
        status=SessionStatus.ACTIVE,
    )

    class FakeSessionManager:
        def get_session(self, _session_id):
            return session

    class FakeFirestore:
        _get_db = AsyncMock()

    old_session_mgr = main.session_mgr
    old_firestore = main.firestore_storage
    main.session_mgr = FakeSessionManager()
    storage = FakeFirestore()
    main.firestore_storage = storage
    auth_token = main.set_current_auth(user)
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(main.delete_session(session.id))
        assert exc_info.value.status_code == 409
        assert "Stop the active session" in str(exc_info.value.detail)
        storage._get_db.assert_not_awaited()
    finally:
        main.reset_current_auth(auth_token)
        main.session_mgr = old_session_mgr
        main.firestore_storage = old_firestore


def test_delete_fences_late_callbacks_and_cancels_final_report(monkeypatch):
    session = SimpleNamespace(
        id="completed-session",
        owner_id=None,
        org_id=None,
        status=SessionStatus.COMPLETED,
    )

    class FakeSessionManager:
        def get_session(self, _session_id):
            return session

    class FakeFirestore:
        _get_db = AsyncMock(return_value=object())

    deletion = AsyncMock(return_value={"session_id": session.id})
    monkeypatch.setattr(
        "backend.storage.deletion.delete_session_everywhere",
        deletion,
    )
    old_session_mgr = main.session_mgr
    old_firestore = main.firestore_storage
    old_locks = main.session_stop_locks
    old_tasks = main.final_summary_tasks
    old_scheduled = main.final_summary_scheduled
    old_single_source_tasks = main.single_source_check_tasks
    old_fences = main.session_deletion_fences
    old_deleted = main.deleted_sessions
    old_documents = main.interview_documents.get(session.id)
    old_context_window = main.context_windows.get(session.id)
    cleanup_calls = []
    monkeypatch.setattr(
        main.ws_manager,
        "cleanup_session",
        lambda session_id: cleanup_calls.append(session_id),
    )
    main.session_mgr = FakeSessionManager()
    main.firestore_storage = FakeFirestore()
    main.session_stop_locks = {}
    main.final_summary_tasks = {}
    main.final_summary_scheduled = set()
    main.single_source_check_tasks = {}
    main.session_deletion_fences = set()
    main.deleted_sessions = set()
    main.interview_documents[session.id] = {"resume": "sensitive"}
    main.context_windows[session.id] = object()
    main.single_source_warned.add(session.id)

    async def pending_report():
        await asyncio.Event().wait()

    async def run():
        task = asyncio.create_task(pending_report())
        warning_task = asyncio.create_task(pending_report())
        main.final_summary_tasks[session.id] = task
        main.final_summary_scheduled.add(session.id)
        main.single_source_check_tasks[session.id] = warning_task
        result = await main.delete_session(session.id)
        return result, task, warning_task

    try:
        result, task, warning_task = asyncio.run(run())
        assert result["session_id"] == session.id
        assert task.cancelled()
        assert warning_task.cancelled()
        assert deletion.await_count == 1
        assert session.id in main.session_deletion_fences
        assert session.id in main.deleted_sessions
        assert session.id not in main.interview_documents
        assert session.id not in main.context_windows
        assert session.id not in main.single_source_check_tasks
        assert session.id not in main.single_source_warned
        assert cleanup_calls == [session.id]
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(main._read_session(session.id))
        assert exc_info.value.status_code == 404
    finally:
        main.session_mgr = old_session_mgr
        main.firestore_storage = old_firestore
        main.session_stop_locks = old_locks
        main.final_summary_tasks = old_tasks
        main.final_summary_scheduled = old_scheduled
        main.single_source_check_tasks = old_single_source_tasks
        main.session_deletion_fences = old_fences
        main.deleted_sessions = old_deleted
        if old_documents is None:
            main.interview_documents.pop(session.id, None)
        else:
            main.interview_documents[session.id] = old_documents
        if old_context_window is None:
            main.context_windows.pop(session.id, None)
        else:
            main.context_windows[session.id] = old_context_window
        main.single_source_warned.discard(session.id)


class FakeWebSocket:
    def __init__(self, ticket: str):
        self.headers = {"sec-websocket-protocol": f"tars-ticket, {ticket}"}
        self.query_params = {}
        self.app = main.app
        self.closed: list[dict] = []

    async def close(self, **kwargs):
        self.closed.append(kwargs)

    async def receive_json(self):
        raise WebSocketDisconnect(code=1000)


def test_websocket_ticket_is_single_use_and_bound_to_session(monkeypatch):
    ticket = "one-time-ticket"
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (
        user,
        "s1",
        datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    monkeypatch.setattr(main, "settings", auth_settings())
    main.app.state.ready = True

    async def read_session(_session_id):
        return SimpleNamespace(owner_id="uid-a", org_id="ella-internal")

    connect = AsyncMock()
    expiry = AsyncMock()
    monkeypatch.setattr(main, "_read_session", read_session)
    monkeypatch.setattr(main.ws_manager, "connect", connect)
    monkeypatch.setattr(main.ws_manager, "disconnect", lambda *_args: None)
    monkeypatch.setattr(main, "_close_ws_at_expiry", expiry)
    try:
        first = FakeWebSocket(ticket)
        asyncio.run(main.websocket_endpoint(first, "s1"))
        assert first.closed == []
        connect.assert_awaited_once()
        assert ticket not in main.ws_tickets

        replay = FakeWebSocket(ticket)
        asyncio.run(main.websocket_endpoint(replay, "s1"))
        assert replay.closed == [{"code": 1008}]
    finally:
        main.ws_tickets.clear()


def test_websocket_expiry_closes_socket_without_provider_or_http_calls():
    websocket = FakeWebSocket("unused")
    asyncio.run(
        main._close_ws_at_expiry(
            websocket,
            datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    assert websocket.closed == [{"code": 4001, "reason": "auth_expired"}]


def test_extension_bridge_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main, "settings", auth_settings(extension_enabled=False))
    main.extension_tokens["s1"] = "legacy-token"
    try:
        with pytest.raises(HTTPException) as exc_info:
            main._validate_extension_token("s1", "Bearer legacy-token")
        assert exc_info.value.status_code == 404
    finally:
        main.extension_tokens.clear()


def test_review_rejects_foreign_raw_record_before_deserialization(monkeypatch):
    class FakeFirestore:
        async def get_session_record(self, _session_id):
            return {
                "ownerId": "uid-b",
                "orgId": "ella-internal",
                "mode": object(),
            }

    monkeypatch.setattr(main, "firestore_storage", FakeFirestore())
    auth_token = main.set_current_auth(
        AuthContext("uid-a", "a@example.com", "ella-internal")
    )
    enforced_token = main.set_auth_enforced()
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(main.get_session_review("foreign"))
        assert exc_info.value.status_code == 404
    finally:
        main.reset_current_auth(auth_token)
        main.reset_auth_enforced(enforced_token)


# --- HTTP & WebSocket Readiness Boundary Tests ---

class InstrumentedTicketEntry:
    """Tuple proxy for ws_ticket entries tracking index-based property reads."""
    def __init__(self, user, session_id, exp_time, tracker: dict[str, Any]):
        self._tuple = (user, session_id, exp_time)
        self._tracker = tracker

    def __getitem__(self, idx: int):
        self._tracker["ticket_entry_index_reads"].append(idx)
        return self._tuple[idx]

    def __len__(self):
        return 3

    def __iter__(self):
        return iter(self._tuple)


class RecordingDict(dict):
    """Dictionary proxy recording all mutations and read accesses."""
    def __init__(self, name: str, tracker: dict[str, Any], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._name = name
        self._tracker = tracker

    def __setitem__(self, key, value):
        self._tracker["registry_mutations"].append((self._name, "setitem", key, value))
        super().__setitem__(key, value)

    def setdefault(self, key, default=None):
        self._tracker["registry_mutations"].append((self._name, "setdefault", key, default))
        return super().setdefault(key, default)
    def pop(self, key, *args):
        self._tracker["registry_mutations"].append((self._name, "pop", key, *args))
        return super().pop(key, *args)

    def popitem(self):
        self._tracker["registry_mutations"].append((self._name, "popitem"))
        return super().popitem()

    def __delitem__(self, key):
        self._tracker["registry_mutations"].append((self._name, "delitem", key))
        super().__delitem__(key)

    def clear(self):
        self._tracker["registry_mutations"].append((self._name, "clear"))
        super().clear()

    def update(self, *args, **kwargs):
        self._tracker["registry_mutations"].append((self._name, "update", args, kwargs))
        super().update(*args, **kwargs)

    def __ior__(self, other):
        self._tracker["registry_mutations"].append((self._name, "ior"))
        return super().__ior__(other)

    def get(self, key, default=None):
        self._tracker["registry_reads"].append((self._name, "get", key))
        return super().get(key, default)

    def __getitem__(self, key):
        self._tracker["registry_reads"].append((self._name, "getitem", key))
        return super().__getitem__(key)

    def __contains__(self, key):
        self._tracker["registry_reads"].append((self._name, "contains", key))
        return super().__contains__(key)

    def __len__(self):
        self._tracker["registry_reads"].append((self._name, "len"))
        return super().__len__()

    def __iter__(self):
        self._tracker["registry_reads"].append((self._name, "iter"))
        return super().__iter__()

    def keys(self):
        self._tracker["registry_reads"].append((self._name, "keys"))
        return super().keys()

    def values(self):
        self._tracker["registry_reads"].append((self._name, "values"))
        return super().values()

    def items(self):
        self._tracker["registry_reads"].append((self._name, "items"))
        return super().items()

    def copy(self):
        self._tracker["registry_reads"].append((self._name, "copy"))
        return super().copy()

    def __reversed__(self):
        self._tracker["registry_reads"].append((self._name, "reversed"))
        return super().__reversed__()

    def __repr__(self):
        self._tracker["registry_reads"].append((self._name, "repr"))
        return super().__repr__()

    def __str__(self):
        self._tracker["registry_reads"].append((self._name, "str"))
        return super().__str__()

    def __eq__(self, other):
        self._tracker["registry_reads"].append((self._name, "eq"))
        return super().__eq__(other)

    def __ne__(self, other):
        self._tracker["registry_reads"].append((self._name, "ne"))
        return super().__ne__(other)

    def __or__(self, other):
        self._tracker["registry_reads"].append((self._name, "or"))
        return super().__or__(other)

    def __ror__(self, other):
        self._tracker["registry_reads"].append((self._name, "ror"))
        return super().__ror__(other)

    def raw_set(self, key, value):
        super().__setitem__(key, value)

    def raw_clear(self):
        super().clear()

    def raw_dict(self):
        return dict(super().items())


class RecordingSet(set):
    """Set proxy recording all mutations and lookups."""
    def __init__(self, name: str, tracker: dict[str, Any], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._name = name
        self._tracker = tracker

    def add(self, element):
        self._tracker["registry_mutations"].append((self._name, "add", element))
        super().add(element)

    def discard(self, element):
        self._tracker["registry_mutations"].append((self._name, "discard", element))
        super().discard(element)

    def remove(self, element):
        self._tracker["registry_mutations"].append((self._name, "remove", element))
        super().remove(element)

    def pop(self):
        self._tracker["registry_mutations"].append((self._name, "pop"))
        return super().pop()

    def clear(self):
        self._tracker["registry_mutations"].append((self._name, "clear"))
        super().clear()

    def update(self, *s):
        self._tracker["registry_mutations"].append((self._name, "update"))
        super().update(*s)

    def intersection_update(self, *s):
        self._tracker["registry_mutations"].append((self._name, "intersection_update"))
        super().intersection_update(*s)

    def difference_update(self, *s):
        self._tracker["registry_mutations"].append((self._name, "difference_update"))
        super().difference_update(*s)

    def symmetric_difference_update(self, other):
        self._tracker["registry_mutations"].append((self._name, "symmetric_difference_update"))
        super().symmetric_difference_update(other)

    def __iand__(self, other):
        self._tracker["registry_mutations"].append((self._name, "iand"))
        return super().__iand__(other)

    def __ior__(self, other):
        self._tracker["registry_mutations"].append((self._name, "ior"))
        return super().__ior__(other)

    def __isub__(self, other):
        self._tracker["registry_mutations"].append((self._name, "isub"))
        return super().__isub__(other)

    def __ixor__(self, other):
        self._tracker["registry_mutations"].append((self._name, "ixor"))
        return super().__ixor__(other)

    def __contains__(self, element):
        self._tracker["registry_reads"].append((self._name, "contains", element))
        return super().__contains__(element)

    def __len__(self):
        self._tracker["registry_reads"].append((self._name, "len"))
        return super().__len__()

    def __iter__(self):
        self._tracker["registry_reads"].append((self._name, "iter"))
        return super().__iter__()

    def copy(self):
        self._tracker["registry_reads"].append((self._name, "copy"))
        return super().copy()

    def __repr__(self):
        self._tracker["registry_reads"].append((self._name, "repr"))
        return super().__repr__()

    def __str__(self):
        self._tracker["registry_reads"].append((self._name, "str"))
        return super().__str__()

    def __eq__(self, other):
        self._tracker["registry_reads"].append((self._name, "eq"))
        return super().__eq__(other)

    def __ne__(self, other):
        self._tracker["registry_reads"].append((self._name, "ne"))
        return super().__ne__(other)

    def __le__(self, other):
        self._tracker["registry_reads"].append((self._name, "compare"))
        return super().__le__(other)

    def __lt__(self, other):
        self._tracker["registry_reads"].append((self._name, "compare"))
        return super().__lt__(other)

    def __ge__(self, other):
        self._tracker["registry_reads"].append((self._name, "compare"))
        return super().__ge__(other)

    def __gt__(self, other):
        self._tracker["registry_reads"].append((self._name, "compare"))
        return super().__gt__(other)

    def __and__(self, other):
        self._tracker["registry_reads"].append((self._name, "and"))
        return super().__and__(other)

    def __rand__(self, other):
        self._tracker["registry_reads"].append((self._name, "and"))
        return super().__rand__(other)

    def __or__(self, other):
        self._tracker["registry_reads"].append((self._name, "or"))
        return super().__or__(other)

    def __ror__(self, other):
        self._tracker["registry_reads"].append((self._name, "or"))
        return super().__ror__(other)

    def __sub__(self, other):
        self._tracker["registry_reads"].append((self._name, "sub"))
        return super().__sub__(other)

    def __rsub__(self, other):
        self._tracker["registry_reads"].append((self._name, "sub"))
        return super().__rsub__(other)

    def __xor__(self, other):
        self._tracker["registry_reads"].append((self._name, "xor"))
        return super().__xor__(other)

    def __rxor__(self, other):
        self._tracker["registry_reads"].append((self._name, "xor"))
        return super().__rxor__(other)

    def isdisjoint(self, other):
        self._tracker["registry_reads"].append((self._name, "isdisjoint"))
        return super().isdisjoint(other)

    def issubset(self, other):
        self._tracker["registry_reads"].append((self._name, "issubset"))
        return super().issubset(other)

    def issuperset(self, other):
        self._tracker["registry_reads"].append((self._name, "issuperset"))
        return super().issuperset(other)

    def union(self, *s):
        self._tracker["registry_reads"].append((self._name, "union"))
        return super().union(*s)

    def intersection(self, *s):
        self._tracker["registry_reads"].append((self._name, "intersection"))
        return super().intersection(*s)

    def difference(self, *s):
        self._tracker["registry_reads"].append((self._name, "difference"))
        return super().difference(*s)

    def symmetric_difference(self, other):
        self._tracker["registry_reads"].append((self._name, "symmetric_difference"))
        return super().symmetric_difference(other)

    def raw_add(self, element):
        super().add(element)

    def raw_clear(self):
        super().clear()

    def raw_set(self):
        return set(super().__iter__())


class LoggerSpy:
    """Fixed logger spy recording exact calls without string formatting."""
    def __init__(self, tracker: dict[str, Any]):
        self._tracker = tracker

    def _log(self, level: str, event: str, *args, **kwargs):
        self._tracker["logger_events"].append((level, event, args, kwargs))

    def debug(self, event: str, *args, **kwargs):
        self._log("DEBUG", event, *args, **kwargs)

    def info(self, event: str, *args, **kwargs):
        self._log("INFO", event, *args, **kwargs)

    def warning(self, event: str, *args, **kwargs):
        self._log("WARNING", event, *args, **kwargs)

    def error(self, event: str, *args, **kwargs):
        self._log("ERROR", event, *args, **kwargs)

    def exception(self, event: str, *args, **kwargs):
        self._log("EXCEPTION", event, *args, **kwargs)

    def critical(self, event: str, *args, **kwargs):
        self._log("CRITICAL", event, *args, **kwargs)


class InstrumentedRequestHeaders:
    """Instrumented headers proxy tracking property access, lookups, and membership."""
    def __init__(self, raw: dict[str, str], tracker: dict[str, Any]):
        self._raw = {k.lower(): v for k, v in raw.items()}
        self._tracker = tracker

    def get(self, key: str, default=None):
        self._tracker["http_request_headers_reads"].append(("get", key.lower()))
        return self._raw.get(key.lower(), default)

    def __getitem__(self, key: str):
        self._tracker["http_request_headers_reads"].append(("getitem", key.lower()))
        return self._raw[key.lower()]

    def __contains__(self, key: str):
        self._tracker["http_request_headers_reads"].append(("contains", key.lower()))
        return key.lower() in self._raw


class InstrumentedRequestState:
    """Instrumented Request.state tracking auth_user assignment."""
    def __init__(self, tracker: dict[str, Any]):
        self._dict = {}
        self._tracker = tracker

    def __setattr__(self, name: str, value):
        if name in ("_dict", "_tracker"):
            super().__setattr__(name, value)
        else:
            if name == "auth_user":
                self._tracker["state_auth_user_writes"] += 1
            self._dict[name] = value

    def __getattr__(self, name: str):
        if name in ("_dict", "_tracker"):
            return super().__getattribute__(name)
        if name in self._dict:
            return self._dict[name]
        raise AttributeError(f"Request state has no attribute {name!r}")


class InstrumentedAppState:
    """Instrumented App.state tracking gate readiness reads."""
    def __init__(self, ready_val, tracker: dict[str, Any], gate_key: str, phase_fn):
        self._ready_val = ready_val
        self._tracker = tracker
        self._gate_key = gate_key
        self._phase_fn = phase_fn

    @property
    def ready(self):
        self._tracker[self._gate_key] += 1
        self._tracker["event_trace"].append(f"{self._phase_fn()}:ready_read")
        return self._ready_val


class InstrumentedApp:
    """Instrumented App proxy providing property-instrumented state."""
    def __init__(self, ready_val, tracker: dict[str, Any], gate_key: str, phase_fn):
        self._state = InstrumentedAppState(ready_val, tracker, gate_key, phase_fn)
        self._tracker = tracker
        self._phase_fn = phase_fn

    @property
    def state(self):
        self._tracker["app_state_property_reads"] += 1
        self._tracker["event_trace"].append(f"{self._phase_fn()}:app_state")
        return self._state


class InstrumentedRequest:
    """Direct HTTP request instrumenting headers, state, app property access, and gate read."""
    def __init__(
        self,
        path: str,
        method: str = "GET",
        headers: dict | None = None,
        ready_val=False,
        tracker: dict | None = None,
        phase: str = "http",
    ):
        self.url = SimpleNamespace(path=path)
        self.method = method
        t = tracker if tracker is not None else {}
        self._tracker = t
        self._phase = phase
        self._headers = InstrumentedRequestHeaders(headers or {}, t)
        self._state = InstrumentedRequestState(t)
        self._app = InstrumentedApp(ready_val, t, "http_gate_read_count", lambda: self._phase)

    @property
    def headers(self):
        self._tracker["http_request_headers_property_reads"] += 1
        return self._headers

    @property
    def state(self):
        self._tracker["http_request_state_property_reads"] += 1
        return self._state

    @property
    def app(self):
        self._tracker["http_request_app_property_reads"] += 1
        self._tracker["event_trace"].append(f"{self._phase}:request_app")
        return self._app


class InstrumentedSocketHeaders:
    """Instrumented socket headers tracking lookups."""
    def __init__(self, raw: dict[str, str], tracker: dict[str, Any]):
        self._raw = {k.lower(): v for k, v in raw.items()}
        self._tracker = tracker

    def get(self, key: str, default=None):
        self._tracker["ws_headers_reads"].append(("get", key.lower()))
        return self._raw.get(key.lower(), default)

    def __getitem__(self, key: str):
        self._tracker["ws_headers_reads"].append(("getitem", key.lower()))
        return self._raw[key.lower()]

    def __contains__(self, key: str):
        self._tracker["ws_headers_reads"].append(("contains", key.lower()))
        return key.lower() in self._raw


class InstrumentedSocketQueryParams:
    """Instrumented socket query params tracking lookups."""
    def __init__(self, raw: dict[str, str], tracker: dict[str, Any]):
        self._raw = dict(raw)
        self._tracker = tracker

    def get(self, key: str, default=None):
        self._tracker["ws_query_params_reads"].append(("get", key))
        return self._raw.get(key, default)

    def __getitem__(self, key: str):
        self._tracker["ws_query_params_reads"].append(("getitem", key))
        return self._raw[key]

    def __contains__(self, key: str):
        self._tracker["ws_query_params_reads"].append(("contains", key))
        return key in self._raw


class InstrumentedWebSocket:
    """Instrumented direct socket exposing property-based attributes and denial/fallback semantics."""
    def __init__(
        self,
        path: str,
        headers: dict | None = None,
        query_params: dict | None = None,
        ready_val=False,
        tracker: dict | None = None,
        gate_key: str = "browser_gate_read_count",
        has_denial: bool = True,
        scripted_messages: list | None = None,
        phase: str = "browser",
    ):
        self.url = SimpleNamespace(path=path)
        t = tracker if tracker is not None else {}
        self._tracker = t
        self._phase = phase
        self._headers = InstrumentedSocketHeaders(headers or {}, t)
        self._query_params = InstrumentedSocketQueryParams(query_params or {}, t)
        self._app = InstrumentedApp(ready_val, t, gate_key, lambda: self._phase)
        self.closed: list[dict] = []
        self.denial_responses: list = []
        self._scripted_messages = list(scripted_messages or [])
        self._gate_key = gate_key

        if has_denial:
            self.send_denial_response = self._send_denial_response

    @property
    def headers(self):
        self._tracker["ws_headers_property_reads"] += 1
        return self._headers

    @property
    def query_params(self):
        self._tracker["ws_query_params_property_reads"] += 1
        return self._query_params

    @property
    def app(self):
        self._tracker["ws_app_property_reads"] += 1
        self._tracker["event_trace"].append(f"{self._phase}:ws_app")
        return self._app

    async def _send_denial_response(self, response):
        rec = (
            self._gate_key,
            getattr(response, "status_code", None),
            getattr(response, "body", None),
            getattr(response, "media_type", None),
        )
        self._tracker["ws_denial_responses"].append(rec)
        self._tracker["event_trace"].append(f"{self._phase}:send_denial:{getattr(response, 'status_code', None)}")
        self.denial_responses.append(response)

    async def close(self, code: int = 1000, **kwargs):
        rec = {"code": code}
        rec.update(kwargs)
        self._tracker["ws_close_calls"].append((self._gate_key, rec))
        self._tracker["event_trace"].append(f"{self._phase}:close:{code}")
        self.closed.append(rec)

    async def accept(self, subprotocol: str | None = None):
        self._tracker["ws_accept_calls"].append((self._gate_key, subprotocol))

    async def receive_json(self):
        self._tracker["browser_receive_json_count"] += 1
        if self._scripted_messages:
            msg = self._scripted_messages.pop(0)
            if isinstance(msg, BaseException):
                raise msg
            return msg
        raise WebSocketDisconnect(1000)

    async def receive(self):
        self._tracker["native_receive_count"] += 1
        if self._scripted_messages:
            msg = self._scripted_messages.pop(0)
            if isinstance(msg, BaseException):
                raise msg
            return msg
        return {"type": "websocket.disconnect"}

    async def send_json(self, data: dict):
        self._tracker["ws_send_json_calls"].append((self._gate_key, data))

    async def send_bytes(self, data: bytes):
        self._tracker["ws_send_bytes_calls"].append((self._gate_key, data))


class SettingsAttributeSpy:
    """Proxy wrapping Settings to detect any attribute reads after readiness check."""
    def __init__(self, target: Settings, tracker: dict[str, Any]):
        self._target = target
        self._tracker = tracker

    def __getattr__(self, name: str):
        self._tracker["settings_reads"].append(name)
        return getattr(self._target, name)


class InstrumentedLock:
    """Instrumented Lock recording acquire and release calls with active auth context."""
    def __init__(self, tracker: dict[str, Any]):
        self._lock = asyncio.Lock()
        self._tracker = tracker

    async def __aenter__(self):
        self._tracker["native_sm_lock_calls"].append(("acquire", main.current_auth()))
        return await self._lock.__aenter__()

    async def __aexit__(self, *args):
        self._tracker["native_sm_lock_calls"].append(("release", main.current_auth()))
        return await self._lock.__aexit__(*args)


class TrackedTaskProxy:
    """Proxy exposing asyncio.Task surface while tracking cancellation, awaits, and state."""
    def __init__(
        self,
        real_task: asyncio.Task,
        tracker: dict[str, Any],
        is_browser: bool = True,
        source_coro=None,
        controlled_coro=None,
    ):
        self._task = real_task
        self._tracker = tracker
        self._is_browser = is_browser
        self._source_coro = source_coro
        self._controlled_coro = controlled_coro
        self.cancel_calls: list[dict] = []
        self.await_count = 0

    def cancel(self, *args, **kwargs):
        cancel_key = "browser_expiry_task_cancel_calls" if self._is_browser else "native_stall_task_cancel_calls"
        rec = {
            "args": args,
            "kwargs": kwargs,
            "auth": main.current_auth(),
        }
        self._tracker[cancel_key].append(rec)
        self.cancel_calls.append(rec)
        return self._task.cancel(*args, **kwargs)

    def done(self) -> bool:
        return self._task.done()

    def cancelled(self) -> bool:
        return self._task.cancelled()

    def exception(self):
        return self._task.exception()

    def result(self):
        return self._task.result()

    def add_done_callback(self, fn, *args, **kwargs):
        return self._task.add_done_callback(fn, *args, **kwargs)

    def remove_done_callback(self, fn):
        return self._task.remove_done_callback(fn)

    def __await__(self):
        await_key = "browser_expiry_task_await_calls" if self._is_browser else "native_stall_task_await_calls"
        self._tracker[await_key].append({"auth": main.current_auth()})
        self.await_count += 1
        return self._task.__await__()


class InterceptedTaskRecord:
    """Ledger record capturing exact source coroutine, origin effect record, controlled coroutine, real Task, and proxy."""
    def __init__(
        self,
        source_coro,
        origin_record: dict,
        controlled_coro,
        real_task: asyncio.Task,
        proxy: TrackedTaskProxy | None,
        category: str,
    ):
        self.source_coro = source_coro
        self.origin_record = origin_record
        self.controlled_coro = controlled_coro
        self.real_task = real_task
        self.proxy = proxy
        self.category = category


class MainDateTimeProxy:
    """Module-local datetime proxy tracking phase-aware now() reads."""
    def __init__(self, real_dt, tracker: dict[str, Any], phase_fn):
        self._real_dt = real_dt
        self._tracker = tracker
        self._phase_fn = phase_fn

    def __getattr__(self, name):
        return getattr(self._real_dt, name)

    def now(self, tz=None):
        phase = self._phase_fn()
        self._tracker["clock_reads"].append((phase, tz))
        return self._real_dt.now(tz)


class MainSecretsProxy:
    """Module-local secrets proxy tracking compare_digest calls."""
    def __init__(self, real_secrets, tracker: dict[str, Any]):
        self._real_secrets = real_secrets
        self._tracker = tracker

    def __getattr__(self, name):
        return getattr(self._real_secrets, name)

    def compare_digest(self, a, b):
        self._tracker["native_compare_digest_calls"].append((a, b))
        return self._real_secrets.compare_digest(a, b)


class MainJSONResponseRecorder:
    """Module-local JSONResponse recorder capturing status, content, and ordered event trace."""
    def __init__(self, real_cls, tracker: dict[str, Any], phase_fn):
        self._real_cls = real_cls
        self._tracker = tracker
        self._phase_fn = phase_fn

    def __call__(self, *args, **kwargs):
        phase = self._phase_fn()
        if "content" in kwargs:
            content = kwargs["content"]
        elif len(args) > 0:
            content = args[0]
        else:
            content = None

        if "status_code" in kwargs:
            status = kwargs["status_code"]
        elif len(args) > 1:
            status = args[1]
        else:
            status = 200

        self._tracker["json_response_calls"].append((phase, status, content))
        self._tracker["event_trace"].append(f"{phase}:json_response:{status}")
        return self._real_cls(*args, **kwargs)


class FakeHttpSessionManager:
    """Distinct HTTP session manager spy."""
    def __init__(self, tracker: dict[str, Any]):
        self._tracker = tracker
        self.calls: list[str] = []

    def get_session(self, session_id: str):
        self._tracker["http_session_mgr_reads"].append(session_id)
        self.calls.append(session_id)
        return None


class FakeNativeSessionManager:
    """Distinct Native session manager spy."""
    def __init__(self, tracker: dict[str, Any]):
        self._tracker = tracker
        self.calls: list[str] = []

    def get_session(self, session_id: str):
        self._tracker["native_session_mgr_reads"].append(session_id)
        self.calls.append(session_id)
        return SimpleNamespace(
            owner_id="uid-a",
            org_id="ella-internal",
            status=SessionStatus.ACTIVE,
        )


class FakeFirestoreStorage:
    """Fake Firestore storage spy."""
    def __init__(self, tracker: dict[str, Any]):
        self._tracker = tracker
        self.calls: list[str] = []

    async def get_session_record(self, session_id: str):
        self._tracker["storage_reads"].append(session_id)
        self.calls.append(session_id)
        return {
            "session_id": session_id,
            "owner_id": "uid-a",
            "org_id": "ella-internal",
            "status": "active",
        }


class FakeWSConnectionManager:
    """Isolated fake connection manager tracking connect, disconnect, broadcast, and sequences."""
    def __init__(self, tracker: dict[str, Any]):
        self._tracker = tracker
        self.connect_calls: list[dict] = []
        self.disconnect_calls: list[dict] = []
        self.broadcast_calls: list[dict] = []
        self.seq_calls: list[dict] = []
        self._seq = 0
        self.active_connections: dict[str, list] = {}

    def next_sequence(self, session_id: str) -> int:
        self._seq += 1
        rec = {
            "session_id": session_id,
            "seq": self._seq,
            "auth": main.current_auth(),
        }
        self._tracker["fake_wsm_next_seq_calls"].append(rec)
        self.seq_calls.append(rec)
        return self._seq

    async def connect(
        self,
        websocket,
        session_id: str,
        last_seq: int = 0,
        *,
        subprotocol: str | None = None,
    ):
        await websocket.accept(subprotocol=subprotocol)
        self.active_connections.setdefault(session_id, []).append(websocket)
        rec = {
            "ws": websocket,
            "session_id": session_id,
            "last_seq": last_seq,
            "subprotocol": subprotocol,
            "auth": main.current_auth(),
        }
        self._tracker["browser_connect_calls"].append(rec)
        self.connect_calls.append(rec)

    def disconnect(self, websocket, session_id: str):
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
        rec = {
            "ws": websocket,
            "session_id": session_id,
            "auth": main.current_auth(),
        }
        self._tracker["browser_disconnect_calls"].append(rec)
        self.disconnect_calls.append(rec)

    async def broadcast(self, session_id: str, msg):
        rec = {
            "session_id": session_id,
            "msg": msg,
            "auth": main.current_auth(),
        }
        self._tracker["fake_wsm_broadcast_calls"].append(rec)
        self.broadcast_calls.append(rec)


class FakeStreamManager:
    """Isolated fake StreamManager tracking construction, start, and audio sends."""
    def __init__(self, settings, on_transcript, source_label: str, tracker: dict[str, Any]):
        self._tracker = tracker
        self.settings = settings
        self.on_transcript = on_transcript
        self.source_label = source_label
        self.started = False
        self.start_calls: list[dict] = []
        self.audio_sends: list[bytes] = []
        self._tracker["fake_sm_construct_calls"].append({
            "source_label": source_label,
            "auth": main.current_auth(),
        })

    async def start(self):
        self.started = True
        rec = {"auth": main.current_auth()}
        self._tracker["fake_sm_start_calls"].append(rec)
        self.start_calls.append(rec)

    async def send_audio(self, payload: bytes):
        self._tracker["fake_sm_send_audio_calls"].append(payload)
        self.audio_sends.append(payload)


def make_wired_effect_schema():
    return {
        # HTTP effects
        "http_request_headers_property_reads": 0,
        "http_request_headers_reads": [],
        "http_request_state_property_reads": 0,
        "http_request_app_property_reads": 0,
        "settings_reads": [],
        "verify_bearer_calls": [],
        "set_current_auth_calls": [],
        "reset_current_auth_calls": [],
        "set_auth_enforced_calls": [],
        "reset_auth_enforced_calls": [],
        "state_auth_user_writes": 0,
        "call_next_calls": [],
        "json_response_calls": [],
        "http_session_mgr_reads": [],
        "storage_reads": [],
        "deserialize_session_calls": [],
        "model_dump_calls": [],
        # Browser WS effects
        "ws_headers_property_reads": 0,
        "ws_headers_reads": [],
        "ws_query_params_property_reads": 0,
        "ws_query_params_reads": [],
        "ws_app_property_reads": 0,
        "ticket_entry_index_reads": [],
        "browser_read_session_calls": [],
        "clock_reads": [],
        "browser_connect_calls": [],
        "browser_disconnect_calls": [],
        "browser_receive_json_count": 0,
        "browser_expiry_coro_create_count": 0,
        "browser_expiry_task_create_calls": [],
        "browser_expiry_task_cancel_calls": [],
        "browser_expiry_task_await_calls": [],
        # Native WS effects
        "native_session_mgr_reads": [],
        "native_compare_digest_calls": [],
        "native_receive_count": 0,
        "native_sm_lock_calls": [],
        "fake_sm_construct_calls": [],
        "fake_sm_start_calls": [],
        "fake_sm_send_audio_calls": [],
        "fake_wsm_next_seq_calls": [],
        "fake_wsm_broadcast_calls": [],
        "native_stall_task_create_calls": [],
        "native_stall_task_cancel_calls": [],
        "native_stall_task_await_calls": [],
        # Shared socket / gate / registry / logger records
        "unexpected_task_create_calls": [],
        "ws_denial_responses": [],
        "ws_close_calls": [],
        "ws_accept_calls": [],
        "ws_send_json_calls": [],
        "ws_send_bytes_calls": [],
        "logger_events": [],
        "registry_mutations": [],
        "registry_reads": [],
        "app_state_property_reads": 0,
        "http_gate_read_count": 0,
        "browser_gate_read_count": 0,
        "native_gate_read_count": 0,
        "event_trace": [],
    }


def _run_primary_negative_readiness_row(ready_val, settings_obj, monkeypatch):
    import json
    import struct
    import threading
    from starlette.testclient import TestClient, WebSocketDenialResponse
    from starlette.responses import PlainTextResponse

    # Baseline captures before any execution
    real_asyncio = main.asyncio
    real_datetime = main.datetime
    real_secrets = main.secrets
    real_json_response = main.JSONResponse

    effects = make_wired_effect_schema()

    # Set ready and settings
    main.app.state.ready = ready_val
    if settings_obj is not None:
        settings_spy = SettingsAttributeSpy(settings_obj, effects)
        monkeypatch.setattr(main, "settings", settings_spy)
    else:
        settings_spy = None
        monkeypatch.setattr(main, "settings", None)

    # Logger spy
    logger_spy = LoggerSpy(effects)
    monkeypatch.setattr(main, "logger", logger_spy)

    # Fresh user and mappings with recording proxies
    user = AuthContext("uid-a", "recruiter@example.com", "ella-internal")
    ticket = "test-ticket-unready"
    exp_time = datetime.now(timezone.utc) + timedelta(seconds=60)
    ticket_entry = InstrumentedTicketEntry(user, "s1", exp_time, effects)

    tickets_mapping = RecordingDict("ws_tickets", effects)
    tickets_mapping.raw_set(ticket, ticket_entry)
    monkeypatch.setattr(main, "ws_tickets", tickets_mapping)

    stream_keys_mapping = RecordingDict("stream_keys", effects)
    stream_keys_mapping.raw_set("s1", "test-stream-key")
    monkeypatch.setattr(main, "stream_keys", stream_keys_mapping)

    stop_cap_entry = (user, "s1", exp_time)
    stop_cap_mapping = RecordingDict("stop_capabilities", effects)
    stop_cap_mapping.raw_set("cap-1", stop_cap_entry)
    monkeypatch.setattr(main, "stop_capabilities", stop_cap_mapping)

    # Fresh registries
    fresh_context_windows = RecordingDict("context_windows", effects)
    monkeypatch.setattr(main, "context_windows", fresh_context_windows)
    fresh_pipeline_tasks = RecordingDict("pipeline_tasks", effects)
    monkeypatch.setattr(main, "pipeline_tasks", fresh_pipeline_tasks)
    fresh_native_health = RecordingDict("native_session_health", effects)
    monkeypatch.setattr(main, "native_session_health", fresh_native_health)
    fresh_native_frame_last_seq = RecordingDict("native_frame_last_seq", effects)
    monkeypatch.setattr(main, "native_frame_last_seq", fresh_native_frame_last_seq)
    fresh_native_sm = RecordingDict("native_stream_managers", effects)
    monkeypatch.setattr(main, "native_stream_managers", fresh_native_sm)
    fresh_stream_managers = RecordingDict("stream_managers", effects)
    monkeypatch.setattr(main, "stream_managers", fresh_stream_managers)
    fresh_deleted = RecordingSet("deleted_sessions", effects)
    monkeypatch.setattr(main, "deleted_sessions", fresh_deleted)

    fresh_lock = InstrumentedLock(effects)
    monkeypatch.setattr(main, "native_sm_lock", fresh_lock)

    # Fake managers & singletons
    fake_wsm = FakeWSConnectionManager(effects)
    monkeypatch.setattr(main, "ws_manager", fake_wsm)

    fake_http_sm = FakeHttpSessionManager(effects)
    fake_native_sm_inst = FakeNativeSessionManager(effects)
    monkeypatch.setattr(main, "session_mgr", fake_http_sm)

    fake_fs = FakeFirestoreStorage(effects)
    monkeypatch.setattr(main, "firestore_storage", fake_fs)

    def counting_verify(authorization=None, req_settings=None, *args, **kwargs):
        effects["verify_bearer_calls"].append((authorization, req_settings, args, kwargs))
        if authorization and authorization.startswith("Bearer "):
            return user
        raise AuthenticationError("Missing bearer token")

    monkeypatch.setattr(main, "verify_bearer_token", counting_verify)

    async def counting_read_session(session_id):
        effects["browser_read_session_calls"].append(session_id)
        return SimpleNamespace(
            owner_id="uid-a",
            org_id="ella-internal",
            status=SessionStatus.ACTIVE,
            model_dump=lambda: effects["model_dump_calls"].append(session_id) or {"session_id": session_id},
        )

    monkeypatch.setattr(main, "_read_session", counting_read_session)

    def counting_deserialize(session_id, record):
        effects["deserialize_session_calls"].append((session_id, record))
        return SimpleNamespace(
            owner_id="uid-a",
            org_id="ella-internal",
            status=SessionStatus.ACTIVE,
            model_dump=lambda: effects["model_dump_calls"].append(session_id) or {"session_id": session_id},
        )

    monkeypatch.setattr(main, "deserialize_session", counting_deserialize)

    orig_set_auth = main.set_current_auth

    def counting_set_auth(ctx):
        effects["set_current_auth_calls"].append(ctx)
        return orig_set_auth(ctx)

    monkeypatch.setattr(main, "set_current_auth", counting_set_auth)

    orig_reset_auth = main.reset_current_auth

    def counting_reset_auth(token):
        effects["reset_current_auth_calls"].append(token)
        return orig_reset_auth(token)

    monkeypatch.setattr(main, "reset_current_auth", counting_reset_auth)

    orig_set_enforced = main.set_auth_enforced

    def counting_set_enforced(value=True):
        effects["set_auth_enforced_calls"].append(value)
        return orig_set_enforced(value)

    monkeypatch.setattr(main, "set_auth_enforced", counting_set_enforced)

    orig_reset_enforced = main.reset_auth_enforced

    def counting_reset_enforced(token):
        effects["reset_auth_enforced_calls"].append(token)
        return orig_reset_enforced(token)

    monkeypatch.setattr(main, "reset_auth_enforced", counting_reset_enforced)

    # Expiry factory spy returning tracked coroutine object
    tracked_expiry_coro_obj = None

    async def fake_expiry_coro(ws, exp):
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise

    def counting_close_ws_factory(ws, exp):
        nonlocal tracked_expiry_coro_obj
        effects["browser_expiry_coro_create_count"] += 1
        tracked_expiry_coro_obj = fake_expiry_coro(ws, exp)
        return tracked_expiry_coro_obj

    monkeypatch.setattr(main, "_close_ws_at_expiry", counting_close_ws_factory)

    # Wrap StreamManager class
    class InstrumentedStreamManager(FakeStreamManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, tracker=effects)

    monkeypatch.setattr(main, "StreamManager", InstrumentedStreamManager)

    # 1. TestClient verification (one unconditional outer cleanup owner)
    tc_failures: list[str] = []
    tc_sentinel = object()
    client = None
    tc_asyncio_ok = False
    tc_datetime_ok = False
    tc_secrets_ok = False
    tc_json_ok = False

    try:
        try:
            client = TestClient(main.app, raise_server_exceptions=False)
        except BaseException:
            tc_failures.append("TESTCLIENT_CONSTRUCT_FAILED")

        if client is not None:
            # Bearer /api/me
            res_me = client.get("/api/me", headers={"Authorization": "Bearer valid-token"})
            assert res_me.status_code == 503
            assert res_me.json() == {"detail": "Service unavailable"}
            assert res_me.content == b'{"detail":"Service unavailable"}'
            assert res_me.headers.get("content-type") == "application/json"

            # Bearer protected session get
            res_sess = client.get("/api/sessions/s1", headers={"Authorization": "Bearer valid-token"})
            assert res_sess.status_code == 503
            assert res_sess.json() == {"detail": "Service unavailable"}
            assert res_sess.content == b'{"detail":"Service unavailable"}'
            assert res_sess.headers.get("content-type") == "application/json"

            # Stop capability post
            res_stop = client.post("/api/sessions/s1/stop", headers={"X-TARS-Stop-Capability": "cap-1"})
            assert res_stop.status_code == 503
            assert res_stop.json() == {"detail": "Service unavailable"}
            assert res_stop.content == b'{"detail":"Service unavailable"}'
            assert res_stop.headers.get("content-type") == "application/json"

            # Browser WS denial
            with pytest.raises(WebSocketDenialResponse) as exc_ws:
                with client.websocket_connect(
                    "/ws/s1", subprotocols=["tars-ticket", ticket]
                ):
                    pass
            assert exc_ws.value.status_code == 503
            assert exc_ws.value.json() == {"detail": "Service unavailable"}
            assert exc_ws.value.content == b'{"detail":"Service unavailable"}'
            assert exc_ws.value.headers.get("content-type") == "application/json"

            # Native WS denial
            with pytest.raises(WebSocketDenialResponse) as exc_native:
                with client.websocket_connect(
                    "/api/stream/native/s1",
                    subprotocols=["tars-stream", "test-stream-key"],
                ):
                    pass
            assert exc_native.value.status_code == 503
            assert exc_native.value.json() == {"detail": "Service unavailable"}
            assert exc_native.value.content == b'{"detail":"Service unavailable"}'
            assert exc_native.value.headers.get("content-type") == "application/json"
    finally:
        tc_asyncio_ok = (getattr(main, "asyncio", tc_sentinel) is real_asyncio)
        tc_datetime_ok = (getattr(main, "datetime", tc_sentinel) is real_datetime)
        tc_secrets_ok = (getattr(main, "secrets", tc_sentinel) is real_secrets)
        tc_json_ok = (getattr(main, "JSONResponse", tc_sentinel) is real_json_response)

        if not (tc_asyncio_ok and tc_datetime_ok and tc_secrets_ok and tc_json_ok):
            tc_failures.append("TESTCLIENT_PRE_CLOSE_PROXY_MISMATCH")

        if client is not None:
            try:
                client.close()
            except BaseException:
                tc_failures.append("TESTCLIENT_CLOSE_FAILED")

        try:
            main.asyncio = real_asyncio
        except BaseException:
            tc_failures.append("TESTCLIENT_RESTORE_ASYNCIO_FAILED")
        try:
            main.datetime = real_datetime
        except BaseException:
            tc_failures.append("TESTCLIENT_RESTORE_DATETIME_FAILED")
        try:
            main.secrets = real_secrets
        except BaseException:
            tc_failures.append("TESTCLIENT_RESTORE_SECRETS_FAILED")
        try:
            main.JSONResponse = real_json_response
        except BaseException:
            tc_failures.append("TESTCLIENT_RESTORE_JSONRESPONSE_FAILED")

    assert tc_failures == []
    assert tc_asyncio_ok
    assert tc_datetime_ok
    assert tc_secrets_ok
    assert tc_json_ok
    assert main.asyncio is real_asyncio
    assert main.datetime is real_datetime
    assert main.secrets is real_secrets
    assert main.JSONResponse is real_json_response
    assert main.session_mgr is fake_http_sm

    # Clear setup effects before direct invocations
    for k, v in list(effects.items()):
        if isinstance(v, list):
            v.clear()
        elif isinstance(v, int):
            effects[k] = 0

    # 2. Direct invocations inside an explicitly owned fresh event loop
    ORIG_FACTORY_UNSET = object()
    owned_loop = None
    orig_task_factory = ORIG_FACTORY_UNSET
    owner_thread_id = None
    dt_proxy = None
    secrets_proxy = None
    asyncio_proxy = None
    json_response_recorder = None

    installed_factory = False
    installed_factory_verified = False
    installed_dt = False
    installed_dt_verified = False
    installed_secrets = False
    installed_secrets_verified = False
    installed_asyncio = False
    installed_asyncio_verified = False
    installed_json = False
    installed_json_verified = False

    runner_coro_obj = None
    runner_task = None
    runner_timed_out = False

    intercepted_task_records: list[InterceptedTaskRecord] = []
    classified_task_proxies: list[TrackedTaskProxy] = []
    all_known_tasks: list[asyncio.Task] = []
    test_owned_tasks: list[asyncio.Task] = []
    test_owned_coroutines: list[Any] = []
    constructor_side_effect_tasks: list[asyncio.Task] = []

    interception_close_attempts: list[Any] = []
    interception_close_successes: list[Any] = []
    positional_close_attempts: list[Any] = []
    positional_close_successes: list[Any] = []
    other_rejection_close_attempts: list[Any] = []
    other_rejection_close_successes: list[Any] = []
    rescue_close_attempts: list[Any] = []
    rescue_close_successes: list[Any] = []

    test_close_attempts: list[Any] = []
    test_close_successes: list[Any] = []
    test_owned_unauthorized_rejection_close_attempts: list[Any] = []
    test_owned_unauthorized_rejection_close_successes: list[Any] = []

    harness_cancelled_tasks: list[asyncio.Task] = []
    normal_terminal_tasks: list[asyncio.Task] = []
    cancelled_terminal_tasks: list[asyncio.Task] = []
    exception_terminal_tasks: list[asyncio.Task] = []
    outcome_retrieved_tasks: list[asyncio.Task] = []
    runner_failures: list[str] = []

    cancel_control_origin_calls: list[dict[str, Any]] = []
    late_control_origin_calls: list[dict[str, Any]] = []
    create_task_control_origin_calls: list[dict[str, Any]] = []
    ensure_future_control_origin_calls: list[dict[str, Any]] = []
    resolver_miss_origin_calls: list[dict[str, Any]] = []

    control_proxy_tracker: dict[str, list[Any]] = {
        "browser_expiry_task_cancel_calls": [],
        "native_stall_task_cancel_calls": [],
        "browser_expiry_task_await_calls": [],
        "native_stall_task_await_calls": [],
        "proxies": [],
    }

    class InjectedConstructionControlException(BaseException):
        pass

    class ExpectedUnclassifiedFutureRejection(Exception):
        pass

    class ExpectedResolverMissRejection(Exception):
        pass

    class ExpectedPositionalRejection(Exception):
        pass

    class ExpectedTaskUnsupportedArgsException(Exception):
        pass

    class ExpectedTaskWrongLoopException(Exception):
        pass

    class ExpectedCreateTaskUnsupportedArgsException(Exception):
        pass

    class ExpectedCreateTaskWrongLoopException(Exception):
        pass

    class ExpectedEnsureFutureUnsupportedArgsException(Exception):
        pass

    class ExpectedEnsureFutureWrongLoopException(Exception):
        pass

    injected_construction_exception_instance = InjectedConstructionControlException()
    expected_unclassified_future_rejection_instance = ExpectedUnclassifiedFutureRejection()
    expected_resolver_miss_rejection_instance = ExpectedResolverMissRejection()
    expected_positional_rejection_instance = ExpectedPositionalRejection()
    expected_task_unsupported_args_exc = ExpectedTaskUnsupportedArgsException()
    expected_task_wrong_loop_exc = ExpectedTaskWrongLoopException()
    expected_create_task_unsupported_args_exc = ExpectedCreateTaskUnsupportedArgsException()
    expected_create_task_wrong_loop_exc = ExpectedCreateTaskWrongLoopException()
    expected_ensure_future_unsupported_args_exc = ExpectedEnsureFutureUnsupportedArgsException()
    expected_ensure_future_wrong_loop_exc = ExpectedEnsureFutureWrongLoopException()

    positional_probe_sentinel = object()
    other_rejection_positional_sentinel = object()
    other_rejection_wrong_loop_sentinel = object()
    non_owning_constructor_sentinel = object()
    direct_sentinel = object()

    construction_control_classification_records: list[tuple[Any, BaseException]] = []
    explicit_non_owning_classification_records: list[tuple[Any, Any]] = []
    expected_unclassified_future_rejections: list[BaseException] = []
    expected_resolver_miss_rejections: list[BaseException] = []
    expected_positional_rejections: list[BaseException] = []
    recorded_other_rejection_exceptions: list[BaseException] = []
    ensure_future_repass_proxy_returns: list[Any] = []
    ensure_future_repass_task_returns: list[Any] = []
    unclassified_future_cancels: list[bool] = []
    unclassified_future_caught_cancellations: list[BaseException] = []
    unclassified_future_retrievals: list[Any] = []
    resolver_miss_matched_records: list[InterceptedTaskRecord] = []
    resolver_miss_calls: list[Any] = []
    non_owning_constructor_calls: list[Any] = []

    final_inventory_authorized_tasks_cutoff: list[asyncio.Task] = []
    final_inventory_snapshots: list[list[asyncio.Task]] = []
    final_inventory_pass_tags: list[str] = []
    cleanup_discovered_tasks: list[asyncio.Task] = []

    cancel_control_coro_obj = None
    cancel_control_proxy = None
    create_task_control_coro_obj = None
    create_task_control_proxy = None
    ensure_future_control_coro_obj = None
    ensure_future_control_proxy = None
    resolver_miss_source_coro = None
    positional_probe_source_coro = None
    task_unsupported_args_source_coro = None
    task_wrong_loop_source_coro = None
    create_task_unsupported_args_source_coro = None
    create_task_wrong_loop_source_coro = None
    ensure_future_unsupported_args_source_coro = None
    ensure_future_wrong_loop_source_coro = None
    unclassified_future_obj = None
    late_control_coro_obj = None
    late_control_proxy = None
    late_control_scheduled = False
    construct_fail_coro_obj = None
    non_owning_control_coro_obj = None

    loop_closed_flag = False
    restored_dt = False
    restored_secrets = False
    restored_asyncio = False
    restored_json = False
    restored_factory = False

    def _execute_source_close(coro: Any, pre_attr_code: str, pre_check_code: str, close_code: str, post_attr_code: str, post_check_code: str) -> bool:
        if coro is None:
            return False
        pre_frame = None
        pre_running = None
        try:
            pre_frame = coro.cr_frame
            pre_running = coro.cr_running
        except BaseException:
            runner_failures.append(pre_attr_code)
            return False

        if pre_frame is None or pre_running is not False:
            runner_failures.append(pre_check_code)
            return False

        close_returned = False
        try:
            coro.close()
            close_returned = True
        except BaseException:
            runner_failures.append(close_code)
            return False

        if close_returned:
            post_frame = None
            post_running = None
            try:
                post_frame = coro.cr_frame
                post_running = coro.cr_running
            except BaseException:
                runner_failures.append(post_attr_code)
                return False

            if post_frame is None and post_running is False:
                return True
            else:
                runner_failures.append(post_check_code)
                return False
        return False

    def close_source_interception(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in interception_close_attempts):
            runner_failures.append("INTERCEPTION_SOURCE_DUPLICATE_HANDOFF")
            return False
        interception_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "INTERCEPTION_SOURCE_PRE_STATE_ATTR_FAILED",
            "INTERCEPTION_SOURCE_PRE_STATE_CHECK_FAILED",
            "INTERCEPTION_SOURCE_CLOSE_FAILED",
            "INTERCEPTION_SOURCE_POST_STATE_ATTR_FAILED",
            "INTERCEPTION_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            interception_close_successes.append(coro)
        return ok

    def close_source_positional(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in positional_close_attempts):
            runner_failures.append("POSITIONAL_SOURCE_DUPLICATE_HANDOFF")
            return False
        positional_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "POSITIONAL_SOURCE_PRE_STATE_ATTR_FAILED",
            "POSITIONAL_SOURCE_PRE_STATE_CHECK_FAILED",
            "POSITIONAL_SOURCE_CLOSE_FAILED",
            "POSITIONAL_SOURCE_POST_STATE_ATTR_FAILED",
            "POSITIONAL_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            positional_close_successes.append(coro)
        return ok

    def close_source_other_rejection(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in other_rejection_close_attempts):
            runner_failures.append("OTHER_REJECTION_SOURCE_DUPLICATE_HANDOFF")
            return False
        other_rejection_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "OTHER_REJECTION_SOURCE_PRE_STATE_ATTR_FAILED",
            "OTHER_REJECTION_SOURCE_PRE_STATE_CHECK_FAILED",
            "OTHER_REJECTION_SOURCE_CLOSE_FAILED",
            "OTHER_REJECTION_SOURCE_POST_STATE_ATTR_FAILED",
            "OTHER_REJECTION_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            other_rejection_close_successes.append(coro)
        return ok

    def close_source_rescue(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in rescue_close_attempts):
            runner_failures.append("RESCUE_SOURCE_DUPLICATE_HANDOFF")
            return False
        runner_failures.append("SOURCE_RESCUE_REQUIRED")
        rescue_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "RESCUE_SOURCE_PRE_STATE_ATTR_FAILED",
            "RESCUE_SOURCE_PRE_STATE_CHECK_FAILED",
            "RESCUE_SOURCE_CLOSE_FAILED",
            "RESCUE_SOURCE_POST_STATE_ATTR_FAILED",
            "RESCUE_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            rescue_close_successes.append(coro)
        return ok

    def close_test_owned_once(coro: Any) -> bool:
        if coro is None:
            return False
        for c in test_close_attempts:
            if c is coro:
                return False
        test_close_attempts.append(coro)

        close_returned = False
        try:
            coro.close()
            close_returned = True
        except BaseException:
            runner_failures.append("TEST_OWNED_CORO_CLOSE_FAILED")
            return False

        if close_returned:
            try:
                frame = coro.cr_frame
                running = coro.cr_running
                if frame is None and running is False:
                    test_close_successes.append(coro)
                    return True
                else:
                    runner_failures.append("TEST_OWNED_CORO_POST_STATE_CHECK_FAILED")
            except BaseException:
                runner_failures.append("TEST_OWNED_CORO_POST_STATE_CHECK_FAILED")
        return False

    def close_test_owned_unauthorized_rejection(coro: Any) -> bool:
        if coro is None:
            return False
        test_owned_unauthorized_rejection_close_attempts.append(coro)
        ok = close_test_owned_once(coro)
        if ok:
            test_owned_unauthorized_rejection_close_successes.append(coro)
        return ok

    def injected_task_constructor(coro, loop=None, **kwargs):
        raise injected_construction_exception_instance

    def explicit_non_owning_constructor(coro, loop=None, **kwargs):
        non_owning_constructor_calls.append((coro, loop))
        return non_owning_constructor_sentinel

    def construct_test_owned_task(coro: Any, loop: asyncio.AbstractEventLoop, task_constructor=real_asyncio.Task, **kwargs) -> asyncio.Task:
        if not any(c is coro for c in test_owned_coroutines):
            test_owned_coroutines.append(coro)

        if task_constructor is not real_asyncio.Task and task_constructor is not injected_task_constructor and task_constructor is not explicit_non_owning_constructor:
            runner_failures.append("UNAUTHORIZED_TASK_CONSTRUCTOR")
            close_test_owned_unauthorized_rejection(coro)
            raise RuntimeError("UNAUTHORIZED_TASK_CONSTRUCTOR")

        before_tasks = []
        try:
            before_tasks = list(real_asyncio.all_tasks(loop))
        except BaseException:
            runner_failures.append("CONSTRUCTOR_BEFORE_INVENTORY_FAILED")

        candidate = None
        try:
            if task_constructor is real_asyncio.Task:
                candidate = real_asyncio.Task(coro, loop=owned_loop, **kwargs)
            else:
                candidate = task_constructor(coro, loop=loop, **kwargs)
        except InjectedConstructionControlException as exc:
            after_tasks = []
            try:
                after_tasks = list(real_asyncio.all_tasks(loop))
            except BaseException:
                runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

            new_side_effects = [t for t in after_tasks if not any(b is t for b in before_tasks)]
            if new_side_effects:
                for t in new_side_effects:
                    constructor_side_effect_tasks.append(t)
                    if not any(k is t for k in all_known_tasks):
                        all_known_tasks.append(t)
                runner_failures.append("CONSTRUCTOR_RAISED_WITH_SIDE_EFFECT_TASK")
                raise

            inventory_equal = (
                len(before_tasks) == len(after_tasks)
                and all(any(a is b for a in after_tasks) for b in before_tasks)
                and all(any(b is a for b in before_tasks) for a in after_tasks)
            )
            if not inventory_equal:
                runner_failures.append("CONSTRUCTOR_INVENTORY_MUTATED_ON_EXCEPTION")
                raise

            if exc is injected_construction_exception_instance:
                closed_ok = close_test_owned_once(coro)
                if closed_ok:
                    construction_control_classification_records.append((coro, exc))
                else:
                    runner_failures.append("CONSTRUCTION_CONTROL_CLOSE_FAILED")
            else:
                runner_failures.append("CONSTRUCTION_CONTROL_WRONG_EXCEPTION_INSTANCE")
            raise
        except BaseException:
            after_tasks = []
            try:
                after_tasks = list(real_asyncio.all_tasks(loop))
            except BaseException:
                runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

            new_side_effects = [t for t in after_tasks if not any(b is t for b in before_tasks)]
            if new_side_effects:
                for t in new_side_effects:
                    constructor_side_effect_tasks.append(t)
                    if not any(k is t for k in all_known_tasks):
                        all_known_tasks.append(t)
                runner_failures.append("CONSTRUCTOR_RAISED_WITH_SIDE_EFFECT_TASK")
            else:
                runner_failures.append("TEST_OWNED_TASK_CONSTRUCT_FAILED")
            raise

        is_task = False
        try:
            is_task = isinstance(candidate, real_asyncio.Task)
        except BaseException:
            runner_failures.append("CANDIDATE_TASK_ISINSTANCE_FAILED")

        if is_task:
            if not any(k is candidate for k in all_known_tasks):
                all_known_tasks.append(candidate)
            if not any(k is candidate for k in test_owned_tasks):
                test_owned_tasks.append(candidate)

            after_tasks = []
            try:
                after_tasks = list(real_asyncio.all_tasks(loop))
            except BaseException:
                runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

            candidate_in_after = sum(1 for t in after_tasks if t is candidate)
            if candidate_in_after != 1:
                runner_failures.append("CANDIDATE_TASK_INVENTORY_COUNT_MISMATCH")

            for b in before_tasks:
                if sum(1 for a in after_tasks if a is b) != 1:
                    runner_failures.append("BEFORE_TASK_MISSING_FROM_AFTER_INVENTORY")

            new_side_effects = [t for t in after_tasks if (t is not candidate and not any(b is t for b in before_tasks))]
            if new_side_effects:
                for t in new_side_effects:
                    constructor_side_effect_tasks.append(t)
                    if not any(k is t for k in all_known_tasks):
                        all_known_tasks.append(t)
                runner_failures.append("CONSTRUCTOR_RETURNED_WITH_SIDE_EFFECT_TASK")

            cand_loop = None
            try:
                cand_loop = candidate.get_loop()
            except BaseException:
                runner_failures.append("CANDIDATE_GET_LOOP_FAILED")

            if cand_loop is not owned_loop:
                runner_failures.append("TASK_CONSTRUCTOR_LOOP_AUTHORITY_BREACH")
            return candidate

        after_tasks = []
        try:
            after_tasks = list(real_asyncio.all_tasks(loop))
        except BaseException:
            runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

        new_side_effects = [t for t in after_tasks if not any(b is t for b in before_tasks)]
        if new_side_effects:
            for t in new_side_effects:
                constructor_side_effect_tasks.append(t)
                if not any(k is t for k in all_known_tasks):
                    all_known_tasks.append(t)
            runner_failures.append("NON_TASK_RETURN_WITH_SIDE_EFFECT_TASK")
            raise RuntimeError("NON_TASK_RETURN_WITH_SIDE_EFFECT_TASK")

        inventory_equal = (
            len(before_tasks) == len(after_tasks)
            and all(any(a is b for a in after_tasks) for b in before_tasks)
            and all(any(b is a for b in before_tasks) for a in after_tasks)
        )
        if not inventory_equal:
            runner_failures.append("NON_OWNING_INVENTORY_MUTATED")
            raise RuntimeError("NON_OWNING_INVENTORY_MUTATED")

        if task_constructor is explicit_non_owning_constructor and candidate is non_owning_constructor_sentinel:
            if (
                len(non_owning_constructor_calls) == 1
                and non_owning_constructor_calls[0][0] is coro
                and non_owning_constructor_calls[0][1] is loop
            ):
                closed_ok = close_test_owned_once(coro)
                if closed_ok:
                    explicit_non_owning_classification_records.append((coro, candidate))
                    return candidate
                else:
                    runner_failures.append("NON_OWNING_CONTROL_CLOSE_FAILED")
            else:
                runner_failures.append("NON_OWNING_CONTROL_CALL_MISMATCH")
            raise RuntimeError("NON_OWNING_CONTROL_FAILED")

        runner_failures.append("UNAUTHORIZED_NON_TASK_RETURN")
        raise RuntimeError("UNAUTHORIZED_NON_TASK_RETURN")

    async def controlled_parking_coro():
        try:
            while True:
                await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            raise

    async def controlled_finishing_coro():
        pass

    def safe_bounded_drive(loop: asyncio.AbstractEventLoop, duration: float, reg_code: str, run_code: str, cancel_code: str) -> bool:
        if loop is None:
            runner_failures.append("DRIVE_LOOP_NONE")
            return False
        is_closed = False
        try:
            is_closed = loop.is_closed()
        except BaseException:
            runner_failures.append("LOOP_CLOSED_CHECK_FAILED")
            return False
        if is_closed:
            runner_failures.append("DRIVE_LOOP_CLOSED")
            return False

        if threading.get_ident() != owner_thread_id:
            runner_failures.append("DRIVE_WRONG_THREAD")
            return False

        if loop is not owned_loop:
            runner_failures.append("DRIVE_WRONG_LOOP")
            return False

        handle = None
        try:
            handle = loop.call_later(duration, loop.stop)
        except BaseException:
            runner_failures.append(reg_code)
            return False

        if handle is None:
            runner_failures.append(reg_code)
            return False

        drive_success = False
        cancel_success = False
        try:
            if not isinstance(handle, real_asyncio.TimerHandle):
                runner_failures.append(reg_code)
                return False

            is_cancelled = False
            try:
                is_cancelled = handle.cancelled()
            except BaseException:
                runner_failures.append(reg_code)
                return False

            if is_cancelled:
                runner_failures.append(reg_code)
                return False

            try:
                loop.run_forever()
                drive_success = True
            except BaseException:
                runner_failures.append(run_code)
        finally:
            try:
                handle.cancel()
                cancel_success = True
            except BaseException:
                runner_failures.append(cancel_code)

        return drive_success and cancel_success

    def resolve_intercepted_record(task: asyncio.Task, probe_resolver_miss: bool = False) -> InterceptedTaskRecord | None:
        for r in intercepted_task_records:
            if r.real_task is task:
                if probe_resolver_miss and r.source_coro is resolver_miss_source_coro:
                    resolver_miss_matched_records.append(r)
                    resolver_miss_calls.append(task)
                    return None
                return r
        return None

    def harness_cancel_once(task: asyncio.Task):
        if task is None:
            return
        for t in harness_cancelled_tasks:
            if t is task:
                return

        if threading.get_ident() != owner_thread_id:
            runner_failures.append("CANCEL_WRONG_THREAD")
            return

        try:
            if task.get_loop() is not owned_loop:
                runner_failures.append("CANCEL_WRONG_LOOP")
                return
        except BaseException:
            runner_failures.append("CANCEL_LOOP_CHECK_FAILED")
            return

        is_done = False
        try:
            is_done = task.done()
        except BaseException:
            runner_failures.append("CANCEL_DONE_CHECK_FAILED")
            return

        if is_done:
            return

        record = resolve_intercepted_record(task)
        designated_proxy = record.proxy if record is not None else None

        if record is not None and designated_proxy is None:
            runner_failures.append("CLASSIFIED_TASK_PROXY_MISSING")
            return

        harness_cancelled_tasks.append(task)

        if designated_proxy is not None:
            try:
                res = designated_proxy.cancel()
                if res is not True:
                    runner_failures.append("PROXY_CANCEL_NOT_TRUE")
            except BaseException:
                runner_failures.append("PROXY_CANCEL_FAILED")
        else:
            try:
                res = task.cancel()
                if res is not True:
                    runner_failures.append("TASK_CANCEL_NOT_TRUE")
            except BaseException:
                runner_failures.append("TASK_CANCEL_FAILED")

    def retrieve_terminal_outcome(task: asyncio.Task):
        if task is None:
            return
        for t in outcome_retrieved_tasks:
            if t is task:
                return

        is_done = False
        try:
            is_done = task.done()
        except BaseException:
            runner_failures.append("TASK_DONE_CHECK_FAILED")
            return

        if not is_done:
            runner_failures.append("TASK_NOT_DONE_AT_RETRIEVAL")
            return

        is_cancelled = False
        try:
            is_cancelled = task.cancelled()
        except BaseException:
            runner_failures.append("TASK_CANCELLED_CHECK_FAILED")

        if is_cancelled:
            cancelled_terminal_tasks.append(task)
            outcome_retrieved_tasks.append(task)
            if task is runner_task and not runner_timed_out:
                runner_failures.append("RUNNER_TASK_UNEXPECTED_CANCEL")
            return

        exc = None
        try:
            exc = task.exception()
        except BaseException:
            runner_failures.append("TASK_EXCEPTION_CHECK_FAILED")

        if exc is not None:
            exception_terminal_tasks.append(task)
            runner_failures.append("TASK_TERMINAL_EXCEPTION")
            outcome_retrieved_tasks.append(task)
        else:
            normal_terminal_tasks.append(task)
            outcome_retrieved_tasks.append(task)

    def custom_task_factory(loop, coro, **factory_kwargs):
        if threading.get_ident() != owner_thread_id:
            runner_failures.append("TASK_FACTORY_WRONG_THREAD")
            close_ok = close_source_interception(coro)
            if not close_ok:
                runner_failures.append("TASK_FACTORY_WRONG_THREAD_CLOSE_FAILED")
            raise RuntimeError("TASK_FACTORY_WRONG_THREAD")

        if loop is not owned_loop:
            runner_failures.append("TASK_FACTORY_WRONG_LOOP")
            close_ok = close_source_interception(coro)
            if not close_ok:
                runner_failures.append("TASK_FACTORY_WRONG_LOOP_CLOSE_FAILED")
            raise RuntimeError("TASK_FACTORY_WRONG_LOOP")

        category = "unexpected"
        is_browser = False
        if coro is tracked_expiry_coro_obj:
            category = "browser_expiry"
            is_browser = True
        elif getattr(getattr(coro, "cr_code", None), "co_name", "") == "stall_watchdog":
            category = "native_stall"
            is_browser = False
        elif coro is cancel_control_coro_obj:
            category = "cancel_control"
        elif coro is late_control_coro_obj:
            category = "late_control"
        elif coro is create_task_control_coro_obj:
            category = "create_task_control"
        elif coro is ensure_future_control_coro_obj:
            category = "ensure_future_control"
        elif coro is resolver_miss_source_coro:
            category = "create_task_resolver_miss_control"

        auth_val = None
        auth_captured = False
        try:
            auth_val = main.current_auth()
            auth_captured = True
        except BaseException:
            runner_failures.append("TASK_FACTORY_CURRENT_AUTH_FAILED")

        if not auth_captured:
            close_ok = close_source_interception(coro)
            if not close_ok:
                runner_failures.append("TASK_FACTORY_AUTH_FAIL_CLOSE_NOT_PROVEN")
            raise RuntimeError("TASK_FACTORY_CURRENT_AUTH_FAILED")

        origin_rec = {"coro": coro, "auth": auth_val}

        close_ok = close_source_interception(coro)
        if not close_ok:
            runner_failures.append("TASK_FACTORY_SOURCE_CLOSE_NOT_PROVEN")
            raise RuntimeError("TASK_FACTORY_SOURCE_CLOSE_NOT_PROVEN")

        if category in ("late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control"):
            park_coro = controlled_finishing_coro()
        else:
            park_coro = controlled_parking_coro()

        real_task = construct_test_owned_task(park_coro, loop, **factory_kwargs)

        proxy_tracker = control_proxy_tracker if category in ("cancel_control", "late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control") else effects

        proxy = None
        try:
            proxy = TrackedTaskProxy(
                real_task,
                proxy_tracker,
                is_browser=is_browser,
                source_coro=coro,
                controlled_coro=park_coro,
            )
        except BaseException:
            runner_failures.append("TASK_FACTORY_PROXY_CONSTRUCT_FAILED")
            raise RuntimeError("TASK_FACTORY_PROXY_CONSTRUCT_FAILED")

        record = None
        try:
            record = InterceptedTaskRecord(coro, origin_rec, park_coro, real_task, proxy, category)
        except BaseException:
            runner_failures.append("TASK_FACTORY_RECORD_CONSTRUCT_FAILED")
            raise RuntimeError("TASK_FACTORY_RECORD_CONSTRUCT_FAILED")

        try:
            if category == "browser_expiry":
                effects["browser_expiry_task_create_calls"].append(origin_rec)
            elif category == "native_stall":
                effects["native_stall_task_create_calls"].append(origin_rec)
            elif category == "unexpected":
                effects["unexpected_task_create_calls"].append(origin_rec)
            elif category == "cancel_control":
                cancel_control_origin_calls.append(origin_rec)
            elif category == "late_control":
                late_control_origin_calls.append(origin_rec)
            elif category == "create_task_control":
                create_task_control_origin_calls.append(origin_rec)
            elif category == "ensure_future_control":
                ensure_future_control_origin_calls.append(origin_rec)
            elif category == "create_task_resolver_miss_control":
                resolver_miss_origin_calls.append(origin_rec)
            else:
                runner_failures.append("TASK_FACTORY_UNKNOWN_CATEGORY")
                raise RuntimeError("TASK_FACTORY_UNKNOWN_CATEGORY")
        except RuntimeError:
            raise
        except BaseException:
            runner_failures.append("TASK_FACTORY_ORIGIN_PUBLICATION_FAILED")
            raise RuntimeError("TASK_FACTORY_ORIGIN_PUBLICATION_FAILED")

        try:
            classified_task_proxies.append(proxy)
            if proxy_tracker is control_proxy_tracker:
                control_proxy_tracker["proxies"].append(proxy)
        except BaseException:
            runner_failures.append("TASK_FACTORY_PROXY_PUBLICATION_FAILED")
            raise RuntimeError("TASK_FACTORY_PROXY_PUBLICATION_FAILED")

        try:
            intercepted_task_records.append(record)
        except BaseException:
            runner_failures.append("TASK_FACTORY_RECORD_PUBLICATION_FAILED")
            raise RuntimeError("TASK_FACTORY_RECORD_PUBLICATION_FAILED")

        return real_task

    class MainAsyncioProxy:
        def __getattr__(self, name):
            return getattr(real_asyncio, name)

        def Task(self, coro, *args, **kwargs):
            if args:
                if len(args) == 1 and args[0] is positional_probe_sentinel and coro is positional_probe_source_coro:
                    closed = close_source_positional(coro)
                    if closed:
                        raise expected_positional_rejection_instance
                    runner_failures.append("POSITIONAL_SOURCE_CLOSE_PROOF_FAILED")
                    raise RuntimeError("POSITIONAL_SOURCE_CLOSE_PROOF_FAILED")

                if len(args) == 1 and args[0] is other_rejection_positional_sentinel and coro is task_unsupported_args_source_coro:
                    closed = close_source_other_rejection(coro)
                    if closed:
                        raise expected_task_unsupported_args_exc
                    runner_failures.append("TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")
                    raise RuntimeError("TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")

                runner_failures.append("TASK_UNSUPPORTED_POSITIONAL_ARGS")
                if asyncio.iscoroutine(coro):
                    close_source_other_rejection(coro)
                raise RuntimeError("TASK_UNSUPPORTED_POSITIONAL_ARGS")

            loop = kwargs.pop("loop", owned_loop)
            if loop is not owned_loop:
                if loop is other_rejection_wrong_loop_sentinel and coro is task_wrong_loop_source_coro:
                    closed = close_source_other_rejection(coro)
                    if closed:
                        raise expected_task_wrong_loop_exc
                    runner_failures.append("TASK_WRONG_LOOP_CLOSE_FAILED")
                    raise RuntimeError("TASK_WRONG_LOOP_CLOSE_FAILED")

                runner_failures.append("PROXY_TASK_WRONG_LOOP")
                if asyncio.iscoroutine(coro):
                    close_source_other_rejection(coro)
                raise RuntimeError("PROXY_TASK_WRONG_LOOP")

            real_task = custom_task_factory(loop, coro, **kwargs)
            record = resolve_intercepted_record(real_task)
            if record is None or record.proxy is None:
                runner_failures.append("PROXY_RESOLUTION_FAILED")
                raise RuntimeError("PROXY_RESOLUTION_FAILED")
            return record.proxy

        def create_task(self, coro, *args, **kwargs):
            if args:
                if len(args) == 1 and args[0] is other_rejection_positional_sentinel and coro is create_task_unsupported_args_source_coro:
                    closed = close_source_other_rejection(coro)
                    if closed:
                        raise expected_create_task_unsupported_args_exc
                    runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")
                    raise RuntimeError("CREATE_TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")

                runner_failures.append("CREATE_TASK_UNSUPPORTED_POSITIONAL_ARGS")
                if asyncio.iscoroutine(coro):
                    close_source_other_rejection(coro)
                raise RuntimeError("CREATE_TASK_UNSUPPORTED_POSITIONAL_ARGS")

            if "loop" in kwargs:
                passed_loop = kwargs.pop("loop")
                if passed_loop is not owned_loop:
                    if passed_loop is other_rejection_wrong_loop_sentinel and coro is create_task_wrong_loop_source_coro:
                        closed = close_source_other_rejection(coro)
                        if closed:
                            raise expected_create_task_wrong_loop_exc
                        runner_failures.append("CREATE_TASK_WRONG_LOOP_CLOSE_FAILED")
                        raise RuntimeError("CREATE_TASK_WRONG_LOOP_CLOSE_FAILED")

                    runner_failures.append("CREATE_TASK_WRONG_LOOP")
                    if asyncio.iscoroutine(coro):
                        close_source_other_rejection(coro)
                    raise RuntimeError("CREATE_TASK_WRONG_LOOP")

            real_task = owned_loop.create_task(coro, *args, **kwargs)

            is_resolver_probe = (coro is resolver_miss_source_coro)
            record = resolve_intercepted_record(real_task, probe_resolver_miss=is_resolver_probe)

            if is_resolver_probe:
                if record is None and len(resolver_miss_matched_records) == 1 and len(resolver_miss_calls) == 1:
                    raise expected_resolver_miss_rejection_instance
                runner_failures.append("RESOLVER_MISS_SEAM_FAILED")
                raise RuntimeError("RESOLVER_MISS_SEAM_FAILED")

            if record is None or record.proxy is None:
                runner_failures.append("CREATE_TASK_PROXY_RESOLUTION_FAILED")
                raise RuntimeError("CREATE_TASK_PROXY_RESOLUTION_FAILED")
            return record.proxy

        def ensure_future(self, coro_or_future, *args, **kwargs):
            if args:
                if len(args) == 1 and args[0] is other_rejection_positional_sentinel and coro_or_future is ensure_future_unsupported_args_source_coro:
                    closed = close_source_other_rejection(coro_or_future)
                    if closed:
                        raise expected_ensure_future_unsupported_args_exc
                    runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_CLOSE_FAILED")
                    raise RuntimeError("ENSURE_FUTURE_UNSUPPORTED_ARGS_CLOSE_FAILED")

                runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_POSITIONAL_ARGS")
                if asyncio.iscoroutine(coro_or_future):
                    close_source_other_rejection(coro_or_future)
                raise RuntimeError("ENSURE_FUTURE_UNSUPPORTED_POSITIONAL_ARGS")

            passed_loop = kwargs.pop("loop", owned_loop)
            if passed_loop is not owned_loop:
                if passed_loop is other_rejection_wrong_loop_sentinel and coro_or_future is ensure_future_wrong_loop_source_coro:
                    closed = close_source_other_rejection(coro_or_future)
                    if closed:
                        raise expected_ensure_future_wrong_loop_exc
                    runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_CLOSE_FAILED")
                    raise RuntimeError("ENSURE_FUTURE_WRONG_LOOP_CLOSE_FAILED")

                runner_failures.append("ENSURE_FUTURE_WRONG_LOOP")
                if asyncio.iscoroutine(coro_or_future):
                    close_source_other_rejection(coro_or_future)
                raise RuntimeError("ENSURE_FUTURE_WRONG_LOOP")

            if isinstance(coro_or_future, TrackedTaskProxy):
                rec = resolve_intercepted_record(coro_or_future._task)
                if rec is not None and rec.proxy is coro_or_future and rec.real_task.get_loop() is owned_loop and coro_or_future._source_coro is rec.source_coro and coro_or_future._controlled_coro is rec.controlled_coro:
                    if any(k is coro_or_future._task for k in all_known_tasks) and any(k is coro_or_future._task for k in test_owned_tasks) and any(p is coro_or_future for p in classified_task_proxies):
                        return coro_or_future
                runner_failures.append("ENSURE_FUTURE_PROXY_VALIDATION_FAILED")
                raise RuntimeError("ENSURE_FUTURE_PROXY_VALIDATION_FAILED")

            if isinstance(coro_or_future, asyncio.Task):
                rec = resolve_intercepted_record(coro_or_future)
                if rec is not None and rec.proxy is not None and coro_or_future.get_loop() is owned_loop and rec.real_task is coro_or_future:
                    if any(k is coro_or_future for k in all_known_tasks) and any(k is coro_or_future for k in test_owned_tasks) and any(p is rec.proxy for p in classified_task_proxies):
                        return rec.proxy
                runner_failures.append("ENSURE_FUTURE_UNCLASSIFIED_TASK_REJECTED")
                raise RuntimeError("ENSURE_FUTURE_UNCLASSIFIED_TASK_REJECTED")

            if asyncio.iscoroutine(coro_or_future):
                return self.create_task(coro_or_future, *args, **kwargs)

            if coro_or_future is unclassified_future_obj:
                raise expected_unclassified_future_rejection_instance

            runner_failures.append("ENSURE_FUTURE_UNCLASSIFIED_AWAITABLE_REJECTED")
            raise RuntimeError("ENSURE_FUTURE_UNCLASSIFIED_AWAITABLE_REJECTED")

    try:
        owner_thread_id = None
        try:
            owner_thread_id = threading.get_ident()
        except BaseException:
            runner_failures.append("OWNER_THREAD_ID_CAPTURE_FAILED")

        try:
            owned_loop = asyncio.new_event_loop()
        except BaseException:
            runner_failures.append("OWNED_LOOP_CREATE_FAILED")

        if owned_loop is not None:
            try:
                orig_task_factory = owned_loop.get_task_factory()
            except BaseException:
                runner_failures.append("ORIG_TASK_FACTORY_CAPTURE_FAILED")

            try:
                owned_loop.set_task_factory(custom_task_factory)
                installed_factory = True
            except BaseException:
                runner_failures.append("TASK_FACTORY_INSTALL_FAILED")

            try:
                if owned_loop.get_task_factory() is custom_task_factory:
                    installed_factory_verified = True
                else:
                    runner_failures.append("TASK_FACTORY_INSTALL_CHECK_FAILED")
            except BaseException:
                runner_failures.append("TASK_FACTORY_INSTALL_CHECK_FAILED")

            current_phase = "setup"
            try:
                dt_proxy = MainDateTimeProxy(real_datetime, effects, lambda: current_phase)
                monkeypatch.setattr(main, "datetime", dt_proxy)
                installed_dt = True
            except BaseException:
                runner_failures.append("DATETIME_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "datetime", direct_sentinel) is dt_proxy:
                    installed_dt_verified = True
                else:
                    runner_failures.append("DATETIME_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("DATETIME_PROXY_VERIFY_FAILED")

            try:
                secrets_proxy = MainSecretsProxy(real_secrets, effects)
                monkeypatch.setattr(main, "secrets", secrets_proxy)
                installed_secrets = True
            except BaseException:
                runner_failures.append("SECRETS_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "secrets", direct_sentinel) is secrets_proxy:
                    installed_secrets_verified = True
                else:
                    runner_failures.append("SECRETS_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("SECRETS_PROXY_VERIFY_FAILED")

            try:
                asyncio_proxy = MainAsyncioProxy()
                monkeypatch.setattr(main, "asyncio", asyncio_proxy)
                installed_asyncio = True
            except BaseException:
                runner_failures.append("ASYNCIO_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "asyncio", direct_sentinel) is asyncio_proxy:
                    installed_asyncio_verified = True
                else:
                    runner_failures.append("ASYNCIO_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("ASYNCIO_PROXY_VERIFY_FAILED")

            try:
                json_response_recorder = MainJSONResponseRecorder(real_json_response, effects, lambda: current_phase)
                monkeypatch.setattr(main, "JSONResponse", json_response_recorder)
                installed_json = True
            except BaseException:
                runner_failures.append("JSONRESPONSE_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "JSONResponse", direct_sentinel) is json_response_recorder:
                    installed_json_verified = True
                else:
                    runner_failures.append("JSONRESPONSE_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("JSONRESPONSE_PROXY_VERIFY_FAILED")

        task_factory_verified_at_precondition = False
        if owned_loop is not None:
            try:
                if owned_loop.get_task_factory() is custom_task_factory:
                    task_factory_verified_at_precondition = True
                else:
                    runner_failures.append("TASK_FACTORY_PRECONDITION_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("TASK_FACTORY_PRECONDITION_VERIFY_FAILED")

        all_preconditions_met = (
            owner_thread_id is not None
            and owned_loop is not None
            and orig_task_factory is not ORIG_FACTORY_UNSET
            and installed_factory
            and installed_factory_verified
            and installed_dt
            and installed_dt_verified
            and installed_secrets
            and installed_secrets_verified
            and installed_asyncio
            and installed_asyncio_verified
            and installed_json
            and installed_json_verified
            and dt_proxy is not None
            and secrets_proxy is not None
            and asyncio_proxy is not None
            and json_response_recorder is not None
            and task_factory_verified_at_precondition
        )

        if all_preconditions_met:
            # 1. Construction-failure control
            async def dummy_construct_fail_coro():
                await asyncio.sleep(3600.0)
            try:
                construct_fail_coro_obj = dummy_construct_fail_coro()
                construct_test_owned_task(construct_fail_coro_obj, owned_loop, task_constructor=injected_task_constructor)
                runner_failures.append("CONSTRUCTION_CONTROL_UNEXPECTED_SUCCESS")
            except InjectedConstructionControlException:
                pass
            except BaseException:
                runner_failures.append("CONSTRUCTION_CONTROL_UNEXPECTED_EXCEPTION")

            # 2. Explicit non-owning-constructor control
            async def dummy_non_owning_coro():
                await asyncio.sleep(3600.0)
            try:
                non_owning_control_coro_obj = dummy_non_owning_coro()
                res_non_owning = construct_test_owned_task(non_owning_control_coro_obj, owned_loop, task_constructor=explicit_non_owning_constructor)
                if res_non_owning is not non_owning_constructor_sentinel:
                    runner_failures.append("NON_OWNING_CONTROL_WRONG_RETURN")
            except BaseException:
                runner_failures.append("NON_OWNING_CONTROL_UNEXPECTED_EXCEPTION")

            # 3. Cancellation control
            async def dummy_cancel_source():
                await asyncio.sleep(3600.0)
            try:
                cancel_control_coro_obj = dummy_cancel_source()
                cancel_control_proxy = asyncio_proxy.Task(cancel_control_coro_obj, loop=owned_loop)
                if not isinstance(cancel_control_proxy, TrackedTaskProxy):
                    runner_failures.append("CANCEL_CONTROL_NOT_PROXY")
            except BaseException:
                runner_failures.append("CANCEL_CONTROL_SETUP_FAILED")

            # 4. Two fresh native-source surface witnesses (Clause 3A)
            async def dummy_create_task_source():
                await asyncio.sleep(3600.0)
            try:
                create_task_control_coro_obj = dummy_create_task_source()
                create_task_control_proxy = asyncio_proxy.create_task(create_task_control_coro_obj)
                if not isinstance(create_task_control_proxy, TrackedTaskProxy):
                    runner_failures.append("CREATE_TASK_CONTROL_NOT_PROXY")
            except BaseException:
                runner_failures.append("CREATE_TASK_CONTROL_SETUP_FAILED")

            async def dummy_ensure_future_source():
                await asyncio.sleep(3600.0)
            try:
                ensure_future_control_coro_obj = dummy_ensure_future_source()
                ensure_future_control_proxy = asyncio_proxy.ensure_future(ensure_future_control_coro_obj, loop=owned_loop)
                if not isinstance(ensure_future_control_proxy, TrackedTaskProxy):
                    runner_failures.append("ENSURE_FUTURE_CONTROL_NOT_PROXY")
            except BaseException:
                runner_failures.append("ENSURE_FUTURE_CONTROL_SETUP_FAILED")

            # 5. Hostile surface probes (Clause 3B)
            # 5a. Re-pass probe
            if cancel_control_proxy is not None:
                pre_intercepted_count = len(intercepted_task_records)
                pre_source_close_counts = (
                    len(interception_close_attempts),
                    len(interception_close_successes),
                    len(positional_close_attempts),
                    len(positional_close_successes),
                    len(other_rejection_close_attempts),
                    len(other_rejection_close_successes),
                    len(rescue_close_attempts),
                    len(rescue_close_successes),
                )
                pre_proxies_count = len(classified_task_proxies)
                pre_control_proxies_count = len(control_proxy_tracker["proxies"])
                pre_known_tasks_count = len(all_known_tasks)
                pre_owned_coros_count = len(test_owned_coroutines)

                try:
                    repass1 = asyncio_proxy.ensure_future(cancel_control_proxy, loop=owned_loop)
                    ensure_future_repass_proxy_returns.append(repass1)
                except BaseException:
                    runner_failures.append("ENSURE_FUTURE_REPASS_PROXY_FAILED")
                try:
                    repass2 = asyncio_proxy.ensure_future(cancel_control_proxy._task, loop=owned_loop)
                    ensure_future_repass_task_returns.append(repass2)
                except BaseException:
                    runner_failures.append("ENSURE_FUTURE_REPASS_TASK_FAILED")

                post_intercepted_count = len(intercepted_task_records)
                post_source_close_counts = (
                    len(interception_close_attempts),
                    len(interception_close_successes),
                    len(positional_close_attempts),
                    len(positional_close_successes),
                    len(other_rejection_close_attempts),
                    len(other_rejection_close_successes),
                    len(rescue_close_attempts),
                    len(rescue_close_successes),
                )
                post_proxies_count = len(classified_task_proxies)
                post_control_proxies_count = len(control_proxy_tracker["proxies"])
                post_known_tasks_count = len(all_known_tasks)
                post_owned_coros_count = len(test_owned_coroutines)

                if (pre_intercepted_count, pre_source_close_counts, pre_proxies_count, pre_control_proxies_count, pre_known_tasks_count, pre_owned_coros_count) != (post_intercepted_count, post_source_close_counts, post_proxies_count, post_control_proxies_count, post_known_tasks_count, post_owned_coros_count):
                    runner_failures.append("ENSURE_FUTURE_REPASS_LEDGER_MUTATION")

            # 5b. Unclassified Future probe
            try:
                unclassified_future_obj = owned_loop.create_future()
                asyncio_proxy.ensure_future(unclassified_future_obj, loop=owned_loop)
                runner_failures.append("UNCLASSIFIED_FUTURE_NOT_REJECTED")
            except ExpectedUnclassifiedFutureRejection as exc:
                if exc is expected_unclassified_future_rejection_instance:
                    expected_unclassified_future_rejections.append(exc)
                else:
                    runner_failures.append("UNCLASSIFIED_FUTURE_WRONG_EXCEPTION_INSTANCE")
            except BaseException:
                runner_failures.append("UNCLASSIFIED_FUTURE_UNEXPECTED_EXCEPTION")

            # 5c. create_task resolver-miss probe
            async def dummy_resolver_miss_source():
                await asyncio.sleep(3600.0)
            try:
                resolver_miss_source_coro = dummy_resolver_miss_source()
                asyncio_proxy.create_task(resolver_miss_source_coro)
                runner_failures.append("RESOLVER_MISS_NOT_REJECTED")
            except ExpectedResolverMissRejection as exc:
                if exc is expected_resolver_miss_rejection_instance:
                    expected_resolver_miss_rejections.append(exc)
                else:
                    runner_failures.append("RESOLVER_MISS_WRONG_EXCEPTION_INSTANCE")
            except BaseException:
                runner_failures.append("RESOLVER_MISS_UNEXPECTED_EXCEPTION")

            # 5d. Task positional-rejection probe
            async def dummy_positional_source():
                await asyncio.sleep(3600.0)
            try:
                positional_probe_source_coro = dummy_positional_source()
                asyncio_proxy.Task(positional_probe_source_coro, positional_probe_sentinel, loop=owned_loop)
                runner_failures.append("POSITIONAL_PROBE_NOT_REJECTED")
            except ExpectedPositionalRejection as exc:
                if exc is expected_positional_rejection_instance:
                    expected_positional_rejections.append(exc)
                else:
                    runner_failures.append("POSITIONAL_PROBE_WRONG_EXCEPTION_INSTANCE")
            except BaseException:
                runner_failures.append("POSITIONAL_PROBE_UNEXPECTED_EXCEPTION")

            # 6. Six live other-rejection probes
            async def dummy_task_unsupported_args():
                await asyncio.sleep(3600.0)
            try:
                task_unsupported_args_source_coro = dummy_task_unsupported_args()
                asyncio_proxy.Task(task_unsupported_args_source_coro, other_rejection_positional_sentinel, loop=owned_loop)
                runner_failures.append("TASK_UNSUPPORTED_ARGS_NOT_REJECTED")
            except ExpectedTaskUnsupportedArgsException as exc:
                if exc is expected_task_unsupported_args_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("TASK_UNSUPPORTED_ARGS_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("TASK_UNSUPPORTED_ARGS_UNEXPECTED_EXCEPTION")

            async def dummy_task_wrong_loop():
                await asyncio.sleep(3600.0)
            try:
                task_wrong_loop_source_coro = dummy_task_wrong_loop()
                asyncio_proxy.Task(task_wrong_loop_source_coro, loop=other_rejection_wrong_loop_sentinel)
                runner_failures.append("TASK_WRONG_LOOP_NOT_REJECTED")
            except ExpectedTaskWrongLoopException as exc:
                if exc is expected_task_wrong_loop_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("TASK_WRONG_LOOP_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("TASK_WRONG_LOOP_UNEXPECTED_EXCEPTION")

            async def dummy_create_task_unsupported_args():
                await asyncio.sleep(3600.0)
            try:
                create_task_unsupported_args_source_coro = dummy_create_task_unsupported_args()
                asyncio_proxy.create_task(create_task_unsupported_args_source_coro, other_rejection_positional_sentinel)
                runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_NOT_REJECTED")
            except ExpectedCreateTaskUnsupportedArgsException as exc:
                if exc is expected_create_task_unsupported_args_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_UNEXPECTED_EXCEPTION")

            async def dummy_create_task_wrong_loop():
                await asyncio.sleep(3600.0)
            try:
                create_task_wrong_loop_source_coro = dummy_create_task_wrong_loop()
                asyncio_proxy.create_task(create_task_wrong_loop_source_coro, loop=other_rejection_wrong_loop_sentinel)
                runner_failures.append("CREATE_TASK_WRONG_LOOP_NOT_REJECTED")
            except ExpectedCreateTaskWrongLoopException as exc:
                if exc is expected_create_task_wrong_loop_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("CREATE_TASK_WRONG_LOOP_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("CREATE_TASK_WRONG_LOOP_UNEXPECTED_EXCEPTION")

            async def dummy_ensure_future_unsupported_args():
                await asyncio.sleep(3600.0)
            try:
                ensure_future_unsupported_args_source_coro = dummy_ensure_future_unsupported_args()
                asyncio_proxy.ensure_future(ensure_future_unsupported_args_source_coro, other_rejection_positional_sentinel, loop=owned_loop)
                runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_NOT_REJECTED")
            except ExpectedEnsureFutureUnsupportedArgsException as exc:
                if exc is expected_ensure_future_unsupported_args_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_UNEXPECTED_EXCEPTION")

            async def dummy_ensure_future_wrong_loop():
                await asyncio.sleep(3600.0)
            try:
                ensure_future_wrong_loop_source_coro = dummy_ensure_future_wrong_loop()
                asyncio_proxy.ensure_future(ensure_future_wrong_loop_source_coro, loop=other_rejection_wrong_loop_sentinel)
                runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_NOT_REJECTED")
            except ExpectedEnsureFutureWrongLoopException as exc:
                if exc is expected_ensure_future_wrong_loop_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_UNEXPECTED_EXCEPTION")

            # 7. Late-window control source initialization
            async def dummy_late_source():
                await asyncio.sleep(3600.0)
            late_control_coro_obj = dummy_late_source()

            # 8. Production direct invocations
            async def _exercise_direct_primary():
                nonlocal current_phase
                try:
                    current_phase = "http_req1"
                    req1 = InstrumentedRequest(
                        path="/api/me",
                        method="GET",
                        headers={"authorization": "Bearer valid-token"},
                        ready_val=ready_val,
                        tracker=effects,
                        phase="http_req1",
                    )
                    async def call_next_1(r):
                        effects["call_next_calls"].append(
                            (r, r.url.path, r.method, current_auth(), auth_is_enforced())
                        )
                        return PlainTextResponse("unexpected-downstream-1")
                    resp1 = await main.authenticate_api_requests(req1, call_next_1)
                    assert resp1.status_code == 503
                    assert resp1.body == b'{"detail":"Service unavailable"}'
                    assert resp1.headers.get("content-type") == "application/json" or getattr(resp1, "media_type", None) == "application/json"
                    assert effects["call_next_calls"] == []
                    assert main.session_mgr is fake_http_sm

                    # 2. Direct HTTP GET /api/sessions/s1
                    current_phase = "http_req2"
                    req2 = InstrumentedRequest(
                        path="/api/sessions/s1",
                        method="GET",
                        headers={"authorization": "Bearer valid-token"},
                        ready_val=ready_val,
                        tracker=effects,
                        phase="http_req2",
                    )
                    async def call_next_2(r):
                        effects["call_next_calls"].append(
                            (r, r.url.path, r.method, current_auth(), auth_is_enforced())
                        )
                        return PlainTextResponse("unexpected-downstream-2")
                    resp2 = await main.authenticate_api_requests(req2, call_next_2)
                    assert resp2.status_code == 503
                    assert resp2.body == b'{"detail":"Service unavailable"}'
                    assert resp2.headers.get("content-type") == "application/json" or getattr(resp2, "media_type", None) == "application/json"
                    assert effects["call_next_calls"] == []
                    assert main.session_mgr is fake_http_sm

                    # 3. Direct HTTP POST /api/sessions/s1/stop
                    current_phase = "http_req3"
                    req3 = InstrumentedRequest(
                        path="/api/sessions/s1/stop",
                        method="POST",
                        headers={"x-tars-stop-capability": "cap-1"},
                        ready_val=ready_val,
                        tracker=effects,
                        phase="http_req3",
                    )
                    async def call_next_3(r):
                        effects["call_next_calls"].append(
                            (r, r.url.path, r.method, current_auth(), auth_is_enforced())
                        )
                        return PlainTextResponse("unexpected-downstream-3")
                    resp3 = await main.authenticate_api_requests(req3, call_next_3)
                    assert resp3.status_code == 503
                    assert resp3.body == b'{"detail":"Service unavailable"}'
                    assert resp3.headers.get("content-type") == "application/json" or getattr(resp3, "media_type", None) == "application/json"
                    assert effects["call_next_calls"] == []
                    assert main.session_mgr is fake_http_sm

                    # 4. Direct Browser WS
                    current_phase = "browser"
                    ws_browser = InstrumentedWebSocket(
                        path="/ws/s1",
                        headers={"sec-websocket-protocol": f"tars-ticket,{ticket}"},
                        query_params={"last_seq": "7"},
                        ready_val=ready_val,
                        tracker=effects,
                        gate_key="browser_gate_read_count",
                        has_denial=True,
                        scripted_messages=[{"type": "ping"}, WebSocketDisconnect(1000)],
                        phase="browser",
                    )
                    await main.websocket_endpoint(ws_browser, "s1")
                    assert len(ws_browser.denial_responses) == 1
                    denial_resp_browser = ws_browser.denial_responses[0]
                    assert getattr(denial_resp_browser, "status_code", None) == 503
                    assert getattr(denial_resp_browser, "body", None) == b'{"detail":"Service unavailable"}'
                    assert (
                        getattr(denial_resp_browser, "media_type", None) == "application/json"
                        or getattr(denial_resp_browser, "headers", {}).get("content-type") == "application/json"
                    )
                    assert ws_browser.closed == []
                    assert main.session_mgr is fake_http_sm

                    # 5. Direct Native WS
                    current_phase = "native"
                    header_bytes = json.dumps(
                        {"session_id": "s1", "source": "microphone", "sequence": 1}
                    ).encode("utf-8")
                    raw_frame = struct.pack(">I", len(header_bytes)) + header_bytes + b"\x01\x02\x03\x04"

                    monkeypatch.setattr(main, "session_mgr", fake_native_sm_inst)
                    assert main.session_mgr is fake_native_sm_inst

                    ws_native = InstrumentedWebSocket(
                        path="/api/stream/native/s1",
                        headers={"sec-websocket-protocol": "tars-stream,test-stream-key"},
                        ready_val=ready_val,
                        tracker=effects,
                        gate_key="native_gate_read_count",
                        has_denial=True,
                        scripted_messages=[{"bytes": raw_frame}, {"type": "websocket.disconnect"}],
                        phase="native",
                    )
                    await main.native_stream_endpoint(ws_native, "s1")
                    assert len(ws_native.denial_responses) == 1
                    denial_resp_native = ws_native.denial_responses[0]
                    assert getattr(denial_resp_native, "status_code", None) == 503
                    assert getattr(denial_resp_native, "body", None) == b'{"detail":"Service unavailable"}'
                    assert (
                        getattr(denial_resp_native, "media_type", None) == "application/json"
                        or getattr(denial_resp_native, "headers", {}).get("content-type") == "application/json"
                    )
                    assert ws_native.closed == []
                finally:
                    current_phase = "cleanup"

            try:
                runner_coro_obj = _exercise_direct_primary()
                runner_task = construct_test_owned_task(runner_coro_obj, owned_loop, name="primary_runner")
            except BaseException:
                runner_failures.append("RUNNER_TASK_CONSTRUCT_FAILED")

            if runner_task is not None:
                try:
                    def stop_on_done(fut):
                        try:
                            owned_loop.stop()
                        except BaseException:
                            runner_failures.append("RUNNER_DONE_CALLBACK_STOP_FAILED")
                    runner_task.add_done_callback(stop_on_done)
                except BaseException:
                    runner_failures.append("RUNNER_DONE_CALLBACK_REGISTRATION_FAILED")

                drive_ok = safe_bounded_drive(owned_loop, 5.0, "RUNNER_TIMER_HANDLE_FAILED", "RUNNER_RUN_FOREVER_FAILED", "RUNNER_TIMER_CANCEL_FAILED")
                if not drive_ok:
                    runner_failures.append("RUNNER_DRIVE_FAILED")

                is_runner_done = False
                try:
                    is_runner_done = runner_task.done()
                except BaseException:
                    runner_failures.append("RUNNER_DONE_CHECK_FAILED")

                if not is_runner_done:
                    runner_timed_out = True
                    runner_failures.append("RUNNER_TASK_TIMEOUT")
    finally:
        try:
            # Phase 1: Structurally unconditional disposal inside inner try
            cleanup_factory_ok = False
            cleanup_asyncio_ok = False
            cleanup_datetime_ok = False
            cleanup_secrets_ok = False
            cleanup_json_ok = False

            owned_loop_is_open = False
            if owned_loop is not None:
                try:
                    owned_loop_is_open = not owned_loop.is_closed()
                except BaseException:
                    runner_failures.append("CLEANUP_LOOP_IS_CLOSED_CHECK_FAILED")

            if unclassified_future_obj is not None:
                try:
                    res = unclassified_future_obj.cancel()
                    if res is True:
                        unclassified_future_cancels.append(True)
                    else:
                        runner_failures.append("UNCLASSIFIED_FUTURE_CANCEL_NOT_TRUE")
                except BaseException:
                    runner_failures.append("UNCLASSIFIED_FUTURE_CANCEL_FAILED")

                try:
                    unclassified_future_obj.result()
                    runner_failures.append("UNCLASSIFIED_FUTURE_RESULT_DID_NOT_RAISE")
                except real_asyncio.CancelledError as exc:
                    unclassified_future_caught_cancellations.append(exc)
                    unclassified_future_retrievals.append(unclassified_future_obj)
                except BaseException:
                    runner_failures.append("UNCLASSIFIED_FUTURE_RESULT_WRONG_EXCEPTION")

            if owned_loop_is_open:
                try:
                    if owned_loop.get_task_factory() is not custom_task_factory:
                        runner_failures.append("CLEANUP_FACTORY_SEAM_LOST")
                        if installed_factory:
                            owned_loop.set_task_factory(custom_task_factory)
                    cleanup_factory_ok = (installed_factory and installed_factory_verified and (owned_loop.get_task_factory() is custom_task_factory))
                except BaseException:
                    runner_failures.append("CLEANUP_FACTORY_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "asyncio", direct_sentinel) is not asyncio_proxy or asyncio_proxy is None:
                        runner_failures.append("CLEANUP_ASYNCIO_SEAM_LOST")
                        if installed_asyncio and asyncio_proxy is not None:
                            main.asyncio = asyncio_proxy
                    cleanup_asyncio_ok = (installed_asyncio and installed_asyncio_verified and asyncio_proxy is not None and (getattr(main, "asyncio", direct_sentinel) is asyncio_proxy))
                except BaseException:
                    runner_failures.append("CLEANUP_ASYNCIO_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "datetime", direct_sentinel) is not dt_proxy or dt_proxy is None:
                        runner_failures.append("CLEANUP_DATETIME_SEAM_LOST")
                        if installed_dt and dt_proxy is not None:
                            main.datetime = dt_proxy
                    cleanup_datetime_ok = (installed_dt and installed_dt_verified and dt_proxy is not None and (getattr(main, "datetime", direct_sentinel) is dt_proxy))
                except BaseException:
                    runner_failures.append("CLEANUP_DATETIME_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "secrets", direct_sentinel) is not secrets_proxy or secrets_proxy is None:
                        runner_failures.append("CLEANUP_SECRETS_SEAM_LOST")
                        if installed_secrets and secrets_proxy is not None:
                            main.secrets = secrets_proxy
                    cleanup_secrets_ok = (installed_secrets and installed_secrets_verified and secrets_proxy is not None and (getattr(main, "secrets", direct_sentinel) is secrets_proxy))
                except BaseException:
                    runner_failures.append("CLEANUP_SECRETS_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "JSONResponse", direct_sentinel) is not json_response_recorder or json_response_recorder is None:
                        runner_failures.append("CLEANUP_JSON_SEAM_LOST")
                        if installed_json and json_response_recorder is not None:
                            main.JSONResponse = json_response_recorder
                    cleanup_json_ok = (installed_json and installed_json_verified and json_response_recorder is not None and (getattr(main, "JSONResponse", direct_sentinel) is json_response_recorder))
                except BaseException:
                    runner_failures.append("CLEANUP_JSON_SEAM_CHECK_FAILED")

                all_cleanup_seams_ok = (
                    cleanup_factory_ok
                    and cleanup_asyncio_ok
                    and cleanup_datetime_ok
                    and cleanup_secrets_ok
                    and cleanup_json_ok
                )

                if not all_cleanup_seams_ok:
                    runner_failures.append("CLEANUP_DRIVE_UNSAFE_SEAMS")

                # Unified cancellation, settlement, and scans (Repair L6, L7)
                if runner_timed_out and runner_task is not None:
                    try:
                        is_runner_done = False
                        try:
                            is_runner_done = runner_task.done()
                        except BaseException:
                            runner_failures.append("TIMEOUT_RUNNER_DONE_CHECK_FAILED")
                        if not is_runner_done:
                            harness_cancel_once(runner_task)
                            drive_ok = safe_bounded_drive(owned_loop, 1.0, "TIMEOUT_TIMER_HANDLE_FAILED", "TIMEOUT_RUN_FOREVER_FAILED", "TIMEOUT_TIMER_CANCEL_FAILED")
                            if not drive_ok:
                                runner_failures.append("TIMEOUT_DRIVE_FAILED")
                    except BaseException:
                        runner_failures.append("TIMEOUT_SETTLEMENT_FAILED")

                pending_init = []
                try:
                    for t in all_known_tasks:
                        try:
                            if not t.done():
                                pending_init.append(t)
                        except BaseException:
                            runner_failures.append("INITIAL_TASK_DONE_CHECK_FAILED")
                except BaseException:
                    runner_failures.append("INITIAL_PENDING_INVENTORY_FAILED")

                for t in pending_init:
                    try:
                        harness_cancel_once(t)
                    except BaseException:
                        runner_failures.append("INITIAL_TASK_CANCEL_FAILED")

                drive_ok = safe_bounded_drive(owned_loop, 0.5, "CANCEL_PROGRESS_TIMER_HANDLE_FAILED", "CANCEL_PROGRESS_RUN_FOREVER_FAILED", "CANCEL_PROGRESS_TIMER_CANCEL_FAILED")
                if not drive_ok:
                    runner_failures.append("CANCEL_PROGRESS_DRIVE_FAILED")

                for cp_idx in range(3):
                    try:
                        current_all = list(real_asyncio.all_tasks(owned_loop))
                        for t in current_all:
                            if not any(k is t for k in all_known_tasks):
                                if not any(d is t for d in cleanup_discovered_tasks):
                                    cleanup_discovered_tasks.append(t)
                                runner_failures.append("CHECKPOINT_UNRETAINED_TASK")

                        all_active_tasks = list(all_known_tasks) + [d for d in cleanup_discovered_tasks if not any(k is d for k in all_known_tasks)]
                        pending_to_cancel = []
                        for t in all_active_tasks:
                            try:
                                if not t.done():
                                    pending_to_cancel.append(t)
                            except BaseException:
                                runner_failures.append("CHECKPOINT_TASK_DONE_CHECK_FAILED")

                        for t in pending_to_cancel:
                            try:
                                harness_cancel_once(t)
                            except BaseException:
                                runner_failures.append("CHECKPOINT_TASK_CANCEL_FAILED")

                        pending_now = []
                        for t in all_active_tasks:
                            try:
                                if not t.done():
                                    pending_now.append(t)
                            except BaseException:
                                runner_failures.append("CHECKPOINT_TASK_DONE_CHECK_FAILED")

                        if not pending_now:
                            break
                        drive_ok = safe_bounded_drive(owned_loop, 0.5, "CHECKPOINT_TIMER_HANDLE_FAILED", "CHECKPOINT_RUN_FOREVER_FAILED", "CHECKPOINT_TIMER_CANCEL_FAILED")
                        if not drive_ok:
                            runner_failures.append("CHECKPOINT_DRIVE_FAILED")
                    except BaseException:
                        runner_failures.append("CHECKPOINT_INVENTORY_FAILED")

                if late_control_coro_obj is not None and all_cleanup_seams_ok:
                    try:
                        def schedule_late_control_cb():
                            nonlocal late_control_proxy
                            try:
                                late_control_proxy = asyncio_proxy.Task(late_control_coro_obj, loop=owned_loop)
                            except BaseException:
                                runner_failures.append("LATE_CONTROL_TASK_FAILED")

                        owned_loop.call_soon(schedule_late_control_cb)
                        late_control_scheduled = True
                    except BaseException:
                        runner_failures.append("LATE_CONTROL_SCHEDULE_FAILED")

                    drive_ok = safe_bounded_drive(owned_loop, 0.1, "LATE_WINDOW_TIMER_HANDLE_FAILED", "LATE_WINDOW_RUN_FOREVER_FAILED", "LATE_WINDOW_TIMER_CANCEL_FAILED")
                    if not drive_ok:
                        runner_failures.append("LATE_WINDOW_DRIVE_FAILED")

                    if not late_control_scheduled or late_control_proxy is None:
                        runner_failures.append("LATE_CONTROL_NOT_SCHEDULED")

                final_inventory_authorized_tasks_cutoff.extend(all_known_tasks)

                # Pass 1
                try:
                    tasks_pass1 = list(real_asyncio.all_tasks(owned_loop))
                    final_inventory_snapshots.append(tasks_pass1)
                    final_inventory_pass_tags.append("PASS_1_INITIAL")
                    for t in tasks_pass1:
                        if not any(c is t for c in final_inventory_authorized_tasks_cutoff):
                            runner_failures.append("FINAL_SCAN_FOUND_UNRETAINED_TASK")
                            if not any(d is t for d in cleanup_discovered_tasks):
                                cleanup_discovered_tasks.append(t)
                        try:
                            if not t.done():
                                harness_cancel_once(t)
                        except BaseException:
                            runner_failures.append("FINAL_PASS_1_CANCEL_FAILED")
                    drive_ok = safe_bounded_drive(owned_loop, 0.1, "FINAL_SETTLE_TIMER_HANDLE_FAILED", "FINAL_SETTLE_RUN_FOREVER_FAILED", "FINAL_SETTLE_TIMER_CANCEL_FAILED")
                    if not drive_ok:
                        runner_failures.append("FINAL_SETTLE_DRIVE_FAILED")
                except BaseException:
                    runner_failures.append("FINAL_INVENTORY_PASS_1_FAILED")

                # Pass 2
                try:
                    tasks_pass2 = list(real_asyncio.all_tasks(owned_loop))
                    final_inventory_snapshots.append(tasks_pass2)
                    final_inventory_pass_tags.append("PASS_2_POST_SETTLE")
                    for t in tasks_pass2:
                        if not any(c is t for c in final_inventory_authorized_tasks_cutoff):
                            runner_failures.append("FINAL_SCAN_FOUND_UNRETAINED_TASK")
                            if not any(d is t for d in cleanup_discovered_tasks):
                                cleanup_discovered_tasks.append(t)
                        try:
                            if not t.done():
                                harness_cancel_once(t)
                        except BaseException:
                            runner_failures.append("FINAL_PASS_2_CANCEL_FAILED")
                    needs_second_settle = False
                    for t in tasks_pass2:
                        try:
                            if not t.done():
                                needs_second_settle = True
                                break
                        except BaseException:
                            runner_failures.append("FINAL_PASS_2_DONE_CHECK_FAILED")
                    if needs_second_settle:
                        drive_ok = safe_bounded_drive(owned_loop, 0.1, "SECOND_SETTLE_TIMER_HANDLE_FAILED", "SECOND_SETTLE_RUN_FOREVER_FAILED", "SECOND_SETTLE_TIMER_CANCEL_FAILED")
                        if not drive_ok:
                            runner_failures.append("SECOND_SETTLE_DRIVE_FAILED")
                except BaseException:
                    runner_failures.append("FINAL_INVENTORY_PASS_2_FAILED")

                # Pass 3
                try:
                    tasks_pass3 = list(real_asyncio.all_tasks(owned_loop))
                    final_inventory_snapshots.append(tasks_pass3)
                    final_inventory_pass_tags.append("PASS_3_FINAL_VERIFY")
                    for t in tasks_pass3:
                        if not any(c is t for c in final_inventory_authorized_tasks_cutoff):
                            runner_failures.append("FINAL_SCAN_FOUND_UNRETAINED_TASK")
                            if not any(d is t for d in cleanup_discovered_tasks):
                                cleanup_discovered_tasks.append(t)
                        try:
                            if not t.done():
                                harness_cancel_once(t)
                                runner_failures.append("LOOP_TASKS_STILL_PENDING")
                        except BaseException:
                            runner_failures.append("FINAL_PASS_3_CANCEL_FAILED")
                    needs_third_settle = False
                    for t in tasks_pass3:
                        try:
                            if not t.done():
                                needs_third_settle = True
                                break
                        except BaseException:
                            runner_failures.append("FINAL_PASS_3_DONE_CHECK_FAILED")
                    if needs_third_settle:
                        drive_ok = safe_bounded_drive(owned_loop, 0.1, "THIRD_SETTLE_TIMER_HANDLE_FAILED", "THIRD_SETTLE_RUN_FOREVER_FAILED", "THIRD_SETTLE_TIMER_CANCEL_FAILED")
                        if not drive_ok:
                            runner_failures.append("THIRD_SETTLE_DRIVE_FAILED")
                except BaseException:
                    runner_failures.append("FINAL_INVENTORY_PASS_3_FAILED")

                # Terminal outcome retrieval for all known & discovered tasks
                all_tasks_for_retrieval = list(all_known_tasks) + [d for d in cleanup_discovered_tasks if not any(k is d for k in all_known_tasks)]
                for t in all_tasks_for_retrieval:
                    try:
                        if t.done():
                            retrieve_terminal_outcome(t)
                        else:
                            runner_failures.append("TASK_NOT_DONE_BEFORE_RETRIEVAL")
                    except BaseException:
                        runner_failures.append("TERMINAL_RETRIEVAL_SCAN_FAILED")

            # Unified cleanup source inventory & sweep (Repair L5)
            cleanup_source_candidates = [
                tracked_expiry_coro_obj,
                cancel_control_coro_obj,
                late_control_coro_obj,
                create_task_control_coro_obj,
                ensure_future_control_coro_obj,
                resolver_miss_source_coro,
                positional_probe_source_coro,
                task_unsupported_args_source_coro,
                task_wrong_loop_source_coro,
                create_task_unsupported_args_source_coro,
                create_task_wrong_loop_source_coro,
                ensure_future_unsupported_args_source_coro,
                ensure_future_wrong_loop_source_coro,
            ]
            for src in (
                interception_close_attempts
                + interception_close_successes
                + positional_close_attempts
                + positional_close_successes
                + other_rejection_close_attempts
                + other_rejection_close_successes
                + rescue_close_attempts
                + rescue_close_successes
                + [r.source_coro for r in intercepted_task_records if r.source_coro is not None]
            ):
                cleanup_source_candidates.append(src)

            cleanup_source_inventory = []
            for src in cleanup_source_candidates:
                if src is not None and not any(s is src for s in cleanup_source_inventory):
                    cleanup_source_inventory.append(src)

            for src in cleanup_source_inventory:
                is_proven_closed = any(
                    s is src for s in (
                        interception_close_successes
                        + positional_close_successes
                        + other_rejection_close_successes
                        + rescue_close_successes
                    )
                )
                if not is_proven_closed:
                    state_read_ok = False
                    is_open = False
                    try:
                        frame = src.cr_frame
                        running = src.cr_running
                        state_read_ok = True
                        if frame is not None or running is not False:
                            is_open = True
                    except BaseException:
                        runner_failures.append("CLEANUP_SOURCE_STATE_VERIFY_FAILED")

                    if state_read_ok and not is_open:
                        runner_failures.append("CLEANUP_SOURCE_MISSING_CLOSE_PROOF")
                    elif state_read_ok and is_open:
                        ok = close_source_rescue(src)
                        if not ok:
                            runner_failures.append("CLEANUP_SOURCE_RESCUE_PROOF_FAILED")
        finally:
            # Phase 2: Capture pre-restore identities
            direct_factory_ok = False
            direct_asyncio_ok = False
            direct_datetime_ok = False
            direct_secrets_ok = False
            direct_json_ok = False

            if owned_loop is not None:
                try:
                    direct_factory_ok = (owned_loop.get_task_factory() is custom_task_factory)
                except BaseException:
                    runner_failures.append("PRE_RESTORE_FACTORY_CHECK_FAILED")

            try:
                direct_asyncio_ok = (getattr(main, "asyncio", direct_sentinel) is asyncio_proxy)
            except BaseException:
                runner_failures.append("PRE_RESTORE_ASYNCIO_CHECK_FAILED")
            try:
                direct_datetime_ok = (getattr(main, "datetime", direct_sentinel) is dt_proxy)
            except BaseException:
                runner_failures.append("PRE_RESTORE_DATETIME_CHECK_FAILED")
            try:
                direct_secrets_ok = (getattr(main, "secrets", direct_sentinel) is secrets_proxy)
            except BaseException:
                runner_failures.append("PRE_RESTORE_SECRETS_CHECK_FAILED")
            try:
                direct_json_ok = (getattr(main, "JSONResponse", direct_sentinel) is json_response_recorder)
            except BaseException:
                runner_failures.append("PRE_RESTORE_JSON_CHECK_FAILED")

            # Phase 3: Five independent baseline restores + task factory restore + loop close
            try:
                main.datetime = real_datetime
                restored_dt = True
            except BaseException:
                runner_failures.append("DATETIME_BASELINE_RESTORE_FAILED")
            try:
                main.secrets = real_secrets
                restored_secrets = True
            except BaseException:
                runner_failures.append("SECRETS_BASELINE_RESTORE_FAILED")
            try:
                main.asyncio = real_asyncio
                restored_asyncio = True
            except BaseException:
                runner_failures.append("ASYNCIO_BASELINE_RESTORE_FAILED")
            try:
                main.JSONResponse = real_json_response
                restored_json = True
            except BaseException:
                runner_failures.append("JSONRESPONSE_BASELINE_RESTORE_FAILED")

            if owned_loop is not None:
                if orig_task_factory is not ORIG_FACTORY_UNSET:
                    try:
                        owned_loop.set_task_factory(orig_task_factory)
                        restored_factory = True
                    except BaseException:
                        runner_failures.append("TASK_FACTORY_RESTORE_FAILED")

                try:
                    owned_loop.close()
                    loop_closed_flag = owned_loop.is_closed()
                except BaseException:
                    runner_failures.append("LOOP_CLOSE_FAILED")

    try:
        if owned_loop is None or not loop_closed_flag:
            raise AssertionError("OWNED_LOOP_NOT_CLOSED")

        # Reverse and bidirectional assertions for 4 source-close ledgers (Repair L5 Item 6 / M5)
        non_rescue_attempts = [
            ("interception", interception_close_attempts),
            ("positional", positional_close_attempts),
            ("other", other_rejection_close_attempts),
        ]
        for tag1, l1 in non_rescue_attempts:
            for idx1, a1 in enumerate(l1):
                for idx2, a2 in enumerate(l1):
                    if idx1 != idx2 and a1 is a2:
                        raise AssertionError("DUPLICATE_IN_NON_RESCUE_ATTEMPT_LEDGER")
            for tag2, l2 in non_rescue_attempts:
                if tag1 != tag2:
                    for a1 in l1:
                        if any(a2 is a1 for a2 in l2):
                            raise AssertionError("NON_RESCUE_ATTEMPT_LEDGERS_NOT_DISJOINT")

        # 1. Every success maps by identity to exactly one attempt in its matching ledger
        for s in interception_close_successes:
            if sum(1 for a in interception_close_attempts if a is s) != 1:
                raise AssertionError("INTERCEPTION_SUCCESS_NOT_IN_ATTEMPTS")
            if sum(1 for r in intercepted_task_records if r.source_coro is s) != 1:
                raise AssertionError("INTERCEPTION_SUCCESS_NOT_IN_RECORDS")

        # 2 & 3. Each interception attempt has zero or one matching success; record count equals success count
        for a in interception_close_attempts:
            success_count = sum(1 for s in interception_close_successes if s is a)
            if success_count > 1:
                raise AssertionError("INTERCEPTION_ATTEMPT_MULTIPLE_SUCCESSES")
            if sum(1 for r in intercepted_task_records if r.source_coro is a) != success_count:
                raise AssertionError("INTERCEPTION_ATTEMPT_RECORD_COUNT_MISMATCH")

        # Positional successes and attempts
        for s in positional_close_successes:
            if sum(1 for a in positional_close_attempts if a is s) != 1:
                raise AssertionError("POSITIONAL_SUCCESS_NOT_IN_ATTEMPTS")
            if s is not positional_probe_source_coro:
                raise AssertionError("POSITIONAL_SUCCESS_WRONG_IDENTITY")
        for a in positional_close_attempts:
            if sum(1 for s in positional_close_successes if s is a) > 1:
                raise AssertionError("POSITIONAL_ATTEMPT_MULTIPLE_SUCCESSES")
            if a is not positional_probe_source_coro:
                raise AssertionError("POSITIONAL_ATTEMPT_WRONG_IDENTITY")

        # Other rejection successes and attempts
        expected_other_sources = (
            task_unsupported_args_source_coro,
            task_wrong_loop_source_coro,
            create_task_unsupported_args_source_coro,
            create_task_wrong_loop_source_coro,
            ensure_future_unsupported_args_source_coro,
            ensure_future_wrong_loop_source_coro,
        )
        for s in other_rejection_close_successes:
            if sum(1 for a in other_rejection_close_attempts if a is s) != 1:
                raise AssertionError("OTHER_REJECTION_SUCCESS_NOT_IN_ATTEMPTS")
            if not any(s is exp for exp in expected_other_sources):
                raise AssertionError("OTHER_REJECTION_SUCCESS_UNKNOWN_IDENTITY")
        for a in other_rejection_close_attempts:
            if sum(1 for s in other_rejection_close_successes if s is a) > 1:
                raise AssertionError("OTHER_REJECTION_ATTEMPT_MULTIPLE_SUCCESSES")
            if not any(a is exp for exp in expected_other_sources):
                raise AssertionError("OTHER_REJECTION_ATTEMPT_UNKNOWN_IDENTITY")

        all_non_rescue_successes = interception_close_successes + positional_close_successes + other_rejection_close_successes
        all_non_rescue_attempts = interception_close_attempts + positional_close_attempts + other_rejection_close_attempts

        for s in all_non_rescue_successes:
            if sum(1 for ra in rescue_close_attempts if ra is s) != 0:
                raise AssertionError("RESCUE_ATTEMPT_FOR_PROVEN_SUCCESSFUL_SOURCE")
            if sum(1 for rs in rescue_close_successes if rs is s) != 0:
                raise AssertionError("RESCUE_SUCCESS_FOR_PROVEN_SUCCESSFUL_SOURCE")

        for idx1, r1 in enumerate(rescue_close_attempts):
            for idx2, r2 in enumerate(rescue_close_attempts):
                if idx1 != idx2 and r1 is r2:
                    raise AssertionError("DUPLICATE_RESCUE_ATTEMPT")

            nr_attempt_count = sum(1 for a in all_non_rescue_attempts if a is r1)
            nr_success_count = sum(1 for s in all_non_rescue_successes if s is r1)
            if nr_attempt_count == 0:
                if nr_success_count != 0:
                    raise AssertionError("RESCUE_ATTEMPT_WITH_NON_RESCUE_SUCCESS")
            elif nr_attempt_count == 1:
                if nr_success_count != 0:
                    raise AssertionError("RESCUE_ATTEMPT_WITH_MATCHING_NON_RESCUE_SUCCESS")
            else:
                raise AssertionError("RESCUE_ATTEMPT_WITH_MULTIPLE_NON_RESCUE_ATTEMPTS")

        for idx1, s1 in enumerate(rescue_close_successes):
            for idx2, s2 in enumerate(rescue_close_successes):
                if idx1 != idx2 and s1 is s2:
                    raise AssertionError("DUPLICATE_RESCUE_SUCCESS")
            if sum(1 for a in rescue_close_attempts if a is s1) != 1:
                raise AssertionError("RESCUE_SUCCESS_WITHOUT_EXACTLY_ONE_ATTEMPT")

        if runner_failures:
            raise AssertionError("RUNNER_FAILURES_NOT_EMPTY")
        if not (installed_factory and installed_factory_verified and installed_dt and installed_dt_verified and installed_secrets and installed_secrets_verified and installed_asyncio and installed_asyncio_verified and installed_json and installed_json_verified):
            raise AssertionError("INSTALLATION_AUTHORITY_NOT_VERIFIED")
        if not (direct_factory_ok and direct_asyncio_ok and direct_datetime_ok and direct_secrets_ok and direct_json_ok):
            raise AssertionError("DIRECT_SEAMS_NOT_VERIFIED")
        if not (restored_asyncio and restored_dt and restored_secrets and restored_json and restored_factory):
            raise AssertionError("RESTORES_NOT_VERIFIED")
        if main.asyncio is not real_asyncio or main.datetime is not real_datetime or main.secrets is not real_secrets or main.JSONResponse is not real_json_response or owned_loop.get_task_factory() is not orig_task_factory:
            raise AssertionError("BASELINES_NOT_RESTORED")

        if len(rescue_close_attempts) != 0 or len(rescue_close_successes) != 0:
            raise AssertionError("RESCUE_CLOSE_LEDGER_NOT_EMPTY")

        # Intercepted records assertions
        for r in intercepted_task_records:
            if r.real_task.get_loop() is not owned_loop:
                raise AssertionError("INTERCEPTED_TASK_WRONG_LOOP")
            if r.source_coro is None or r.controlled_coro is None or r.proxy is None:
                raise AssertionError("INTERCEPTED_RECORD_INCOMPLETE")
            if sum(1 for s in interception_close_attempts if s is r.source_coro) != 1:
                raise AssertionError("RECORD_SOURCE_NOT_IN_INTERCEPTION_ATTEMPTS")
            if sum(1 for s in interception_close_successes if s is r.source_coro) != 1:
                raise AssertionError("RECORD_SOURCE_NOT_IN_INTERCEPTION_SUCCESSES")
            if sum(1 for s in positional_close_attempts if s is r.source_coro) != 0:
                raise AssertionError("RECORD_SOURCE_IN_POSITIONAL_ATTEMPTS")
            if sum(1 for s in other_rejection_close_attempts if s is r.source_coro) != 0:
                raise AssertionError("RECORD_SOURCE_IN_OTHER_REJECTION_ATTEMPTS")
            if sum(1 for s in rescue_close_attempts if s is r.source_coro) != 0:
                raise AssertionError("RECORD_SOURCE_IN_RESCUE_ATTEMPTS")
            if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is r.controlled_coro) != 0:
                raise AssertionError("CONTROLLED_CORO_IN_SOURCE_CLOSE")
            if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is runner_coro_obj) != 0:
                raise AssertionError("RUNNER_CORO_IN_SOURCE_CLOSE")
            if sum(1 for t in all_known_tasks if t is r.real_task) != 1:
                raise AssertionError("TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for t in test_owned_tasks if t is r.real_task) != 1:
                raise AssertionError("TASK_NOT_IN_TEST_OWNED")
            if sum(1 for c in test_owned_coroutines if c is r.controlled_coro) != 1:
                raise AssertionError("CONTROLLED_CORO_NOT_IN_TEST_OWNED")
            if r.origin_record.get("coro") is not r.source_coro:
                raise AssertionError("ORIGIN_RECORD_CORO_MISMATCH")

            if r.category == "browser_expiry":
                cat_calls = effects["browser_expiry_task_create_calls"]
            elif r.category == "native_stall":
                cat_calls = effects["native_stall_task_create_calls"]
            elif r.category == "unexpected":
                cat_calls = effects["unexpected_task_create_calls"]
            elif r.category == "cancel_control":
                cat_calls = cancel_control_origin_calls
            elif r.category == "late_control":
                cat_calls = late_control_origin_calls
            elif r.category == "create_task_control":
                cat_calls = create_task_control_origin_calls
            elif r.category == "ensure_future_control":
                cat_calls = ensure_future_control_origin_calls
            elif r.category == "create_task_resolver_miss_control":
                cat_calls = resolver_miss_origin_calls
            else:
                raise AssertionError("INVALID_INTERCEPTED_CATEGORY")

            if sum(1 for rec in cat_calls if rec is r.origin_record) != 1:
                raise AssertionError("ORIGIN_RECORD_NOT_IN_CATEGORY_LIST")
            if sum(1 for p in classified_task_proxies if p is r.proxy) != 1:
                raise AssertionError("PROXY_NOT_IN_CLASSIFIED_LIST")
            if r.category in ("cancel_control", "late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control"):
                if sum(1 for p in control_proxy_tracker["proxies"] if p is r.proxy) != 1:
                    raise AssertionError("PROXY_NOT_IN_CONTROL_TRACKER_PROXIES")
            if sum(1 for other in intercepted_task_records if other.proxy is r.proxy) != 1:
                raise AssertionError("PROXY_NOT_ONE_TO_ONE_WITH_RECORD")
            if r.proxy._task is not r.real_task or r.proxy._source_coro is not r.source_coro or r.proxy._controlled_coro is not r.controlled_coro:
                raise AssertionError("PROXY_FIELDS_MISMATCH")
            if hasattr(r.real_task, "get_coro") and r.real_task.get_coro() is not r.controlled_coro:
                raise AssertionError("REAL_TASK_CORO_MISMATCH")

        # Binding controls by identity, category, origin, task, proxy
        cancel_recs = [r for r in intercepted_task_records if r.category == "cancel_control"]
        if len(cancel_recs) != 1:
            raise AssertionError("CANCEL_CONTROL_RECORD_COUNT_MISMATCH")
        cancel_rec = cancel_recs[0]
        if (
            cancel_rec.source_coro is not cancel_control_coro_obj
            or cancel_rec.proxy is not cancel_control_proxy
            or cancel_rec.real_task is not cancel_control_proxy._task
            or len(cancel_control_origin_calls) != 1
            or cancel_rec.origin_record is not cancel_control_origin_calls[0]
        ):
            raise AssertionError("CANCEL_CONTROL_BINDING_MISMATCH")

        create_task_recs = [r for r in intercepted_task_records if r.category == "create_task_control"]
        if len(create_task_recs) != 1:
            raise AssertionError("CREATE_TASK_CONTROL_RECORD_COUNT_MISMATCH")
        create_task_rec = create_task_recs[0]
        if (
            create_task_rec.source_coro is not create_task_control_coro_obj
            or create_task_rec.proxy is not create_task_control_proxy
            or create_task_rec.real_task is not create_task_control_proxy._task
            or len(create_task_control_origin_calls) != 1
            or create_task_rec.origin_record is not create_task_control_origin_calls[0]
        ):
            raise AssertionError("CREATE_TASK_CONTROL_BINDING_MISMATCH")

        ensure_future_recs = [r for r in intercepted_task_records if r.category == "ensure_future_control"]
        if len(ensure_future_recs) != 1:
            raise AssertionError("ENSURE_FUTURE_CONTROL_RECORD_COUNT_MISMATCH")
        ensure_future_rec = ensure_future_recs[0]
        if (
            ensure_future_rec.source_coro is not ensure_future_control_coro_obj
            or ensure_future_rec.proxy is not ensure_future_control_proxy
            or ensure_future_rec.real_task is not ensure_future_control_proxy._task
            or len(ensure_future_control_origin_calls) != 1
            or ensure_future_rec.origin_record is not ensure_future_control_origin_calls[0]
        ):
            raise AssertionError("ENSURE_FUTURE_CONTROL_BINDING_MISMATCH")

        resolver_miss_recs = [r for r in intercepted_task_records if r.category == "create_task_resolver_miss_control"]
        if len(resolver_miss_recs) != 1:
            raise AssertionError("RESOLVER_MISS_RECORD_COUNT_MISMATCH")
        resolver_miss_rec = resolver_miss_recs[0]
        if (
            resolver_miss_rec.source_coro is not resolver_miss_source_coro
            or resolver_miss_rec is not resolver_miss_matched_records[0]
            or len(resolver_miss_origin_calls) != 1
            or resolver_miss_rec.origin_record is not resolver_miss_origin_calls[0]
        ):
            raise AssertionError("RESOLVER_MISS_BINDING_MISMATCH")

        late_recs = [r for r in intercepted_task_records if r.category == "late_control"]
        if len(late_recs) != 1:
            raise AssertionError("LATE_CONTROL_RECORD_COUNT_MISMATCH")
        late_rec = late_recs[0]
        if (
            late_rec.source_coro is not late_control_coro_obj
            or late_rec.proxy is not late_control_proxy
            or late_rec.real_task is not late_control_proxy._task
            or len(late_control_origin_calls) != 1
            or late_rec.origin_record is not late_control_origin_calls[0]
        ):
            raise AssertionError("LATE_CONTROL_BINDING_MISMATCH")

        # Pairwise distinct controls
        distinct_control_proxies = [cancel_control_proxy, create_task_control_proxy, ensure_future_control_proxy, resolver_miss_rec.proxy, late_control_proxy]
        distinct_control_tasks = [cancel_control_proxy._task, create_task_control_proxy._task, ensure_future_control_proxy._task, resolver_miss_rec.real_task, late_control_proxy._task]
        distinct_control_coros = [cancel_control_coro_obj, create_task_control_coro_obj, ensure_future_control_coro_obj, resolver_miss_source_coro, late_control_coro_obj]
        distinct_control_recs = [cancel_rec, create_task_rec, ensure_future_rec, resolver_miss_rec, late_rec]
        distinct_control_origins = [cancel_control_origin_calls[0], create_task_control_origin_calls[0], ensure_future_control_origin_calls[0], resolver_miss_origin_calls[0], late_control_origin_calls[0]]

        for idx1 in range(5):
            for idx2 in range(idx1 + 1, 5):
                if distinct_control_proxies[idx1] is distinct_control_proxies[idx2]:
                    raise AssertionError("CONTROL_PROXIES_NOT_PAIRWISE_DISTINCT")
                if distinct_control_tasks[idx1] is distinct_control_tasks[idx2]:
                    raise AssertionError("CONTROL_TASKS_NOT_PAIRWISE_DISTINCT")
                if distinct_control_coros[idx1] is distinct_control_coros[idx2]:
                    raise AssertionError("CONTROL_COROS_NOT_PAIRWISE_DISTINCT")
                if distinct_control_recs[idx1] is distinct_control_recs[idx2]:
                    raise AssertionError("CONTROL_RECORDS_NOT_PAIRWISE_DISTINCT")
                if distinct_control_origins[idx1] is distinct_control_origins[idx2]:
                    raise AssertionError("CONTROL_ORIGINS_NOT_PAIRWISE_DISTINCT")

        # Bidirectional proxy mappings
        for p in classified_task_proxies:
            if sum(1 for r in intercepted_task_records if r.proxy is p) != 1:
                raise AssertionError("CLASSIFIED_PROXY_NOT_IN_RECORDS")
        for p in control_proxy_tracker["proxies"]:
            if sum(1 for r in intercepted_task_records if r.proxy is p and r.category in ("cancel_control", "late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control")) != 1:
                raise AssertionError("CONTROL_TRACKER_PROXY_NOT_IN_RECORDS")

        # Bidirectional category origin mappings
        all_origin_lists = [
            effects["browser_expiry_task_create_calls"],
            effects["native_stall_task_create_calls"],
            effects["unexpected_task_create_calls"],
            cancel_control_origin_calls,
            late_control_origin_calls,
            create_task_control_origin_calls,
            ensure_future_control_origin_calls,
            resolver_miss_origin_calls,
        ]
        for olist in all_origin_lists:
            for orec in olist:
                if sum(1 for r in intercepted_task_records if r.origin_record is orec) != 1:
                    raise AssertionError("ORIGIN_RECORD_NOT_IN_INTERCEPTED_RECORDS")

        # Bidirectional task ledgers
        for t in test_owned_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("TEST_OWNED_TASK_NOT_IN_ALL_KNOWN")
        for t in all_known_tasks:
            if sum(1 for k in test_owned_tasks if k is t) != 1:
                raise AssertionError("ALL_KNOWN_TASK_NOT_IN_TEST_OWNED")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("TASK_OUTCOME_RETRIEVAL_MISMATCH")
            if not t.done():
                raise AssertionError("TASK_NOT_DONE")
            if hasattr(t, "get_coro") and t.get_coro() is not None:
                if sum(1 for c in test_owned_coroutines if c is t.get_coro()) != 1:
                    raise AssertionError("TASK_CORO_NOT_IN_TEST_OWNED_COROS")

        for o in outcome_retrieved_tasks:
            if sum(1 for k in all_known_tasks if k is o) != 1:
                raise AssertionError("RETRIEVED_TASK_NOT_IN_ALL_KNOWN")

        for t in all_known_tasks:
            in_normal = sum(1 for n in normal_terminal_tasks if n is t)
            in_cancelled = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exception = sum(1 for e in exception_terminal_tasks if e is t)
            if in_normal + in_cancelled + in_exception != 1:
                raise AssertionError("TASK_TERMINAL_CLASSIFICATION_NOT_EXACT_ONE")

        # Reverse terminal ledger assertions
        for t in normal_terminal_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("NORMAL_TERMINAL_TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("NORMAL_TERMINAL_TASK_NOT_IN_OUTCOME_RETRIEVED")
            in_norm = sum(1 for n in normal_terminal_tasks if n is t)
            in_canc = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exc = sum(1 for e in exception_terminal_tasks if e is t)
            if in_norm != 1 or in_canc != 0 or in_exc != 0:
                raise AssertionError("NORMAL_TERMINAL_TASK_MEMBERSHIP_INVALID")

        for t in cancelled_terminal_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("CANCELLED_TERMINAL_TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("CANCELLED_TERMINAL_TASK_NOT_IN_OUTCOME_RETRIEVED")
            in_norm = sum(1 for n in normal_terminal_tasks if n is t)
            in_canc = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exc = sum(1 for e in exception_terminal_tasks if e is t)
            if in_norm != 0 or in_canc != 1 or in_exc != 0:
                raise AssertionError("CANCELLED_TERMINAL_TASK_MEMBERSHIP_INVALID")

        for t in exception_terminal_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("EXCEPTION_TERMINAL_TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("EXCEPTION_TERMINAL_TASK_NOT_IN_OUTCOME_RETRIEVED")
            in_norm = sum(1 for n in normal_terminal_tasks if n is t)
            in_canc = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exc = sum(1 for e in exception_terminal_tasks if e is t)
            if in_norm != 0 or in_canc != 0 or in_exc != 1:
                raise AssertionError("EXCEPTION_TERMINAL_TASK_MEMBERSHIP_INVALID")

        if len(exception_terminal_tasks) != 0:
            raise AssertionError("EXCEPTION_TERMINAL_TASKS_NOT_EMPTY")

        for c in test_owned_coroutines:
            if not (c is runner_coro_obj or c is construct_fail_coro_obj or c is non_owning_control_coro_obj or any(r.controlled_coro is c for r in intercepted_task_records)):
                raise AssertionError("UNACCOUNTED_TEST_OWNED_CORO")

        # Runner task assertions
        if runner_task is None or runner_coro_obj is None:
            raise AssertionError("RUNNER_TASK_OR_CORO_NONE")
        if sum(1 for t in outcome_retrieved_tasks if t is runner_task) != 1:
            raise AssertionError("RUNNER_TASK_OUTCOME_RETRIEVAL_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is runner_task) != 1:
            raise AssertionError("RUNNER_TASK_NOT_IN_NORMAL_TERMINAL")
        if not runner_task.done() or runner_task.cancelled() or runner_task.exception() is not None:
            raise AssertionError("RUNNER_TASK_STATE_INVALID")
        if hasattr(runner_task, "get_coro") and runner_task.get_coro() is not runner_coro_obj:
            raise AssertionError("RUNNER_TASK_CORO_MISMATCH")
        if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is runner_coro_obj) != 0:
            raise AssertionError("RUNNER_CORO_IN_SOURCE_CLOSE_OUTSIDE")

        # Construction-control assertions
        if len(construction_control_classification_records) != 1:
            raise AssertionError("CONSTRUCTION_CONTROL_RECORD_COUNT_MISMATCH")
        if construction_control_classification_records[0][0] is not construct_fail_coro_obj or construction_control_classification_records[0][1] is not injected_construction_exception_instance:
            raise AssertionError("CONSTRUCTION_CONTROL_RECORD_IDENTITY_MISMATCH")
        if sum(1 for a in test_close_attempts if a is construct_fail_coro_obj) != 1:
            raise AssertionError("CONSTRUCTION_CONTROL_CLOSE_ATTEMPT_MISMATCH")
        if sum(1 for s in test_close_successes if s is construct_fail_coro_obj) != 1:
            raise AssertionError("CONSTRUCTION_CONTROL_CLOSE_SUCCESS_MISMATCH")
        if construct_fail_coro_obj.cr_frame is not None or construct_fail_coro_obj.cr_running is not False:
            raise AssertionError("CONSTRUCTION_CONTROL_NOT_CLOSED")
        if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is construct_fail_coro_obj) != 0:
            raise AssertionError("CONSTRUCTION_CONTROL_IN_SOURCE_CLOSE")
        if sum(1 for t in all_known_tasks if getattr(t, "get_coro", lambda: None)() is construct_fail_coro_obj) != 0:
            raise AssertionError("CONSTRUCTION_CONTROL_TASK_CREATED")

        # Explicit non-owning constructor control assertions
        if len(explicit_non_owning_classification_records) != 1:
            raise AssertionError("NON_OWNING_CONTROL_RECORD_COUNT_MISMATCH")
        if explicit_non_owning_classification_records[0][0] is not non_owning_control_coro_obj or explicit_non_owning_classification_records[0][1] is not non_owning_constructor_sentinel:
            raise AssertionError("NON_OWNING_CONTROL_RECORD_IDENTITY_MISMATCH")
        if sum(1 for a in test_close_attempts if a is non_owning_control_coro_obj) != 1:
            raise AssertionError("NON_OWNING_CONTROL_CLOSE_ATTEMPT_MISMATCH")
        if sum(1 for s in test_close_successes if s is non_owning_control_coro_obj) != 1:
            raise AssertionError("NON_OWNING_CONTROL_CLOSE_SUCCESS_MISMATCH")
        if non_owning_control_coro_obj.cr_frame is not None or non_owning_control_coro_obj.cr_running is not False:
            raise AssertionError("NON_OWNING_CONTROL_NOT_CLOSED")
        if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is non_owning_control_coro_obj) != 0:
            raise AssertionError("NON_OWNING_CONTROL_IN_SOURCE_CLOSE")
        if sum(1 for t in all_known_tasks if getattr(t, "get_coro", lambda: None)() is non_owning_control_coro_obj) != 0:
            raise AssertionError("NON_OWNING_CONTROL_TASK_CREATED")

        if len(test_close_attempts) != 2 or len(test_close_successes) != 2:
            raise AssertionError("TEST_CLOSE_LEDGER_SURPLUS")
        if len(test_owned_unauthorized_rejection_close_attempts) != 0 or len(test_owned_unauthorized_rejection_close_successes) != 0:
            raise AssertionError("UNAUTHORIZED_REJECTION_CLOSE_NOT_EMPTY")
        if len(constructor_side_effect_tasks) != 0:
            raise AssertionError("CONSTRUCTOR_SIDE_EFFECT_TASKS_NOT_EMPTY")

        # Cancellation control assertions
        if cancel_control_proxy is None:
            raise AssertionError("CANCEL_CONTROL_PROXY_NONE")
        if len(control_proxy_tracker["native_stall_task_cancel_calls"]) != 1:
            raise AssertionError("CANCEL_CONTROL_TRACKER_COUNT_MISMATCH")
        if len(cancel_control_proxy.cancel_calls) != 1:
            raise AssertionError("CANCEL_CONTROL_PROXY_CANCEL_COUNT_MISMATCH")
        if control_proxy_tracker["native_stall_task_cancel_calls"][0] is not cancel_control_proxy.cancel_calls[0]:
            raise AssertionError("CANCEL_CONTROL_RECORD_IDENTITY_MISMATCH")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is cancel_control_proxy) != 1:
            raise AssertionError("CANCEL_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in harness_cancelled_tasks if t is cancel_control_proxy._task) != 1:
            raise AssertionError("CANCEL_CONTROL_HARNESS_CANCEL_MISMATCH")
        if sum(1 for t in cancelled_terminal_tasks if t is cancel_control_proxy._task) != 1:
            raise AssertionError("CANCEL_CONTROL_CANCELLED_TERMINAL_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is cancel_control_proxy._task) != 0:
            raise AssertionError("CANCEL_CONTROL_IN_NORMAL_TERMINAL")
        if not cancel_control_proxy._task.cancelled():
            raise AssertionError("CANCEL_CONTROL_TASK_NOT_CANCELLED")

        # Surface witnesses assertions (Clause 3A)
        if create_task_control_proxy is None:
            raise AssertionError("CREATE_TASK_CONTROL_PROXY_NONE")
        if len(create_task_control_proxy.cancel_calls) != 0:
            raise AssertionError("CREATE_TASK_CONTROL_PROXY_CANCELLED")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is create_task_control_proxy) != 1:
            raise AssertionError("CREATE_TASK_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in harness_cancelled_tasks if t is create_task_control_proxy._task) != 0:
            raise AssertionError("CREATE_TASK_CONTROL_HARNESS_CANCELLED")
        if sum(1 for t in normal_terminal_tasks if t is create_task_control_proxy._task) != 1:
            raise AssertionError("CREATE_TASK_CONTROL_NORMAL_TERMINAL_MISMATCH")
        if create_task_control_proxy._task.cancelled():
            raise AssertionError("CREATE_TASK_CONTROL_TASK_CANCELLED")

        if ensure_future_control_proxy is None:
            raise AssertionError("ENSURE_FUTURE_CONTROL_PROXY_NONE")
        if len(ensure_future_control_proxy.cancel_calls) != 0:
            raise AssertionError("ENSURE_FUTURE_CONTROL_PROXY_CANCELLED")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is ensure_future_control_proxy) != 1:
            raise AssertionError("ENSURE_FUTURE_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in harness_cancelled_tasks if t is ensure_future_control_proxy._task) != 0:
            raise AssertionError("ENSURE_FUTURE_CONTROL_HARNESS_CANCELLED")
        if sum(1 for t in normal_terminal_tasks if t is ensure_future_control_proxy._task) != 1:
            raise AssertionError("ENSURE_FUTURE_CONTROL_NORMAL_TERMINAL_MISMATCH")
        if ensure_future_control_proxy._task.cancelled():
            raise AssertionError("ENSURE_FUTURE_CONTROL_TASK_CANCELLED")

        # Hostile surface probes assertions (Clause 3B)
        if len(ensure_future_repass_proxy_returns) != 1 or ensure_future_repass_proxy_returns[0] is not cancel_control_proxy:
            raise AssertionError("ENSURE_FUTURE_REPASS_PROXY_MISMATCH")
        if len(ensure_future_repass_task_returns) != 1 or ensure_future_repass_task_returns[0] is not cancel_control_proxy:
            raise AssertionError("ENSURE_FUTURE_REPASS_TASK_MISMATCH")

        if len(expected_unclassified_future_rejections) != 1 or expected_unclassified_future_rejections[0] is not expected_unclassified_future_rejection_instance:
            raise AssertionError("UNCLASSIFIED_FUTURE_REJECTION_MISMATCH")
        if len(unclassified_future_cancels) != 1 or unclassified_future_cancels[0] is not True:
            raise AssertionError("UNCLASSIFIED_FUTURE_CANCEL_MISMATCH")
        if len(unclassified_future_caught_cancellations) != 1 or not isinstance(unclassified_future_caught_cancellations[0], real_asyncio.CancelledError):
            raise AssertionError("UNCLASSIFIED_FUTURE_CAUGHT_CANCEL_MISMATCH")
        if len(unclassified_future_retrievals) != 1 or unclassified_future_retrievals[0] is not unclassified_future_obj:
            raise AssertionError("UNCLASSIFIED_FUTURE_RETRIEVAL_MISMATCH")
        if not unclassified_future_obj.cancelled():
            raise AssertionError("UNCLASSIFIED_FUTURE_NOT_CANCELLED")
        if sum(1 for t in all_known_tasks if t is unclassified_future_obj) != 0:
            raise AssertionError("UNCLASSIFIED_FUTURE_IN_ALL_KNOWN_TASKS")

        if len(expected_resolver_miss_rejections) != 1 or expected_resolver_miss_rejections[0] is not expected_resolver_miss_rejection_instance:
            raise AssertionError("RESOLVER_MISS_REJECTION_MISMATCH")
        if len(resolver_miss_matched_records) != 1 or resolver_miss_matched_records[0].source_coro is not resolver_miss_source_coro:
            raise AssertionError("RESOLVER_MISS_MATCHED_RECORD_MISMATCH")
        if len(resolver_miss_calls) != 1 or resolver_miss_calls[0] is not resolver_miss_matched_records[0].real_task:
            raise AssertionError("RESOLVER_MISS_CALL_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is resolver_miss_matched_records[0].real_task) != 1:
            raise AssertionError("RESOLVER_MISS_TASK_NORMAL_TERMINAL_MISMATCH")

        if len(expected_positional_rejections) != 1 or expected_positional_rejections[0] is not expected_positional_rejection_instance:
            raise AssertionError("POSITIONAL_REJECTION_MISMATCH")
        if sum(1 for s in positional_close_attempts if s is positional_probe_source_coro) != 1:
            raise AssertionError("POSITIONAL_PROBE_CLOSE_ATTEMPT_MISMATCH")
        if sum(1 for s in positional_close_successes if s is positional_probe_source_coro) != 1:
            raise AssertionError("POSITIONAL_PROBE_CLOSE_SUCCESS_MISMATCH")
        if sum(1 for s in interception_close_attempts if s is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_INTERCEPTION_CLOSE")
        if sum(1 for s in other_rejection_close_attempts if s is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_OTHER_REJECTION_CLOSE")
        if sum(1 for s in rescue_close_attempts if s is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_RESCUE_CLOSE")
        if sum(1 for r in intercepted_task_records if r.source_coro is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_INTERCEPTED_RECORDS")

        # Six other-rejection probes assertions
        expected_other_exceptions = [
            expected_task_unsupported_args_exc,
            expected_task_wrong_loop_exc,
            expected_create_task_unsupported_args_exc,
            expected_create_task_wrong_loop_exc,
            expected_ensure_future_unsupported_args_exc,
            expected_ensure_future_wrong_loop_exc,
        ]
        if len(recorded_other_rejection_exceptions) != 6:
            raise AssertionError("RECORDED_OTHER_REJECTIONS_COUNT_MISMATCH")
        for exp_exc in expected_other_exceptions:
            if sum(1 for r in recorded_other_rejection_exceptions if r is exp_exc) != 1:
                raise AssertionError("RECORDED_OTHER_REJECTION_IDENTITY_MISMATCH")

        expected_other_sources = [
            task_unsupported_args_source_coro,
            task_wrong_loop_source_coro,
            create_task_unsupported_args_source_coro,
            create_task_wrong_loop_source_coro,
            ensure_future_unsupported_args_source_coro,
            ensure_future_wrong_loop_source_coro,
        ]
        if len(other_rejection_close_attempts) != 6 or len(other_rejection_close_successes) != 6:
            raise AssertionError("OTHER_REJECTION_CLOSE_COUNT_MISMATCH")
        for exp_src in expected_other_sources:
            if sum(1 for a in other_rejection_close_attempts if a is exp_src) != 1:
                raise AssertionError("OTHER_REJECTION_ATTEMPT_MISMATCH")
            if sum(1 for s in other_rejection_close_successes if s is exp_src) != 1:
                raise AssertionError("OTHER_REJECTION_SUCCESS_MISMATCH")
            if sum(1 for r in intercepted_task_records if r.source_coro is exp_src) != 0:
                raise AssertionError("OTHER_REJECTION_SOURCE_IN_RECORDS")

        # Late-window control assertions
        if late_control_proxy is None:
            raise AssertionError("LATE_CONTROL_PROXY_NONE")
        if len(late_control_proxy.cancel_calls) != 0:
            raise AssertionError("LATE_CONTROL_PROXY_CANCELLED")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is late_control_proxy) != 1:
            raise AssertionError("LATE_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is late_control_proxy._task) != 1:
            raise AssertionError("LATE_CONTROL_NORMAL_TERMINAL_MISMATCH")
        if sum(1 for t in cancelled_terminal_tasks if t is late_control_proxy._task) != 0:
            raise AssertionError("LATE_CONTROL_IN_CANCELLED_TERMINAL")
        if late_control_proxy._task.cancelled():
            raise AssertionError("LATE_CONTROL_TASK_CANCELLED")

        # Final inventory assertions
        if final_inventory_pass_tags != ["PASS_1_INITIAL", "PASS_2_POST_SETTLE", "PASS_3_FINAL_VERIFY"]:
            raise AssertionError("FINAL_INVENTORY_PASS_TAGS_MISMATCH")
        if len(final_inventory_snapshots) != 3:
            raise AssertionError("FINAL_INVENTORY_SNAPSHOTS_COUNT_MISMATCH")
        if len(cleanup_discovered_tasks) != 0:
            raise AssertionError("CLEANUP_DISCOVERED_TASKS_NOT_EMPTY")
        for snap in final_inventory_snapshots:
            for t in snap:
                if sum(1 for c in final_inventory_authorized_tasks_cutoff if c is t) != 1:
                    raise AssertionError("SNAPSHOT_TASK_NOT_IN_CUTOFF")
        for c in final_inventory_authorized_tasks_cutoff:
            if sum(1 for k in all_known_tasks if k is c) != 1:
                raise AssertionError("CUTOFF_TASK_NOT_IN_ALL_KNOWN")

        # Unhanded expiry check if created
        if tracked_expiry_coro_obj is not None:
            if not any(r.source_coro is tracked_expiry_coro_obj for r in intercepted_task_records):
                if sum(1 for s in interception_close_attempts if s is tracked_expiry_coro_obj) != 1:
                    raise AssertionError("UNHANDED_EXPIRY_ATTEMPT_MISMATCH")
                if sum(1 for s in interception_close_successes if s is tracked_expiry_coro_obj) != 1:
                    raise AssertionError("UNHANDED_EXPIRY_SUCCESS_MISMATCH")
    except AssertionError:
        raise
    except BaseException:
        raise AssertionError("POST_LOOP_ASSERTION_EXECUTION_FAILED")


    # 3. Exact schema assertion
    expected_effects = {
        "http_request_headers_property_reads": 0,
        "http_request_headers_reads": [],
        "http_request_state_property_reads": 0,
        "http_request_app_property_reads": 0,
        "settings_reads": [],
        "verify_bearer_calls": [],
        "set_current_auth_calls": [],
        "reset_current_auth_calls": [],
        "set_auth_enforced_calls": [],
        "reset_auth_enforced_calls": [],
        "state_auth_user_writes": 0,
        "call_next_calls": [],
        "json_response_calls": [],
        "http_session_mgr_reads": [],
        "storage_reads": [],
        "deserialize_session_calls": [],
        "model_dump_calls": [],
        "ws_headers_property_reads": 0,
        "ws_headers_reads": [],
        "ws_query_params_property_reads": 0,
        "ws_query_params_reads": [],
        "ws_app_property_reads": 0,
        "ticket_entry_index_reads": [],
        "browser_read_session_calls": [],
        "clock_reads": [],
        "browser_connect_calls": [],
        "browser_disconnect_calls": [],
        "browser_receive_json_count": 0,
        "browser_expiry_coro_create_count": 0,
        "browser_expiry_task_create_calls": [],
        "browser_expiry_task_cancel_calls": [],
        "browser_expiry_task_await_calls": [],
        "native_session_mgr_reads": [],
        "native_compare_digest_calls": [],
        "native_receive_count": 0,
        "native_sm_lock_calls": [],
        "fake_sm_construct_calls": [],
        "fake_sm_start_calls": [],
        "fake_sm_send_audio_calls": [],
        "fake_wsm_next_seq_calls": [],
        "fake_wsm_broadcast_calls": [],
        "native_stall_task_create_calls": [],
        "native_stall_task_cancel_calls": [],
        "native_stall_task_await_calls": [],
        "unexpected_task_create_calls": [],
        "ws_denial_responses": [],
        "ws_close_calls": [],
        "ws_accept_calls": [],
        "ws_send_json_calls": [],
        "ws_send_bytes_calls": [],
        "logger_events": [],
        "registry_mutations": [],
        "registry_reads": [],
        "app_state_property_reads": 0,
        "http_gate_read_count": 0,
        "browser_gate_read_count": 0,
        "native_gate_read_count": 0,
        "event_trace": [],
    }
    expected_effects["http_gate_read_count"] = 3
    expected_effects["browser_gate_read_count"] = 1
    expected_effects["native_gate_read_count"] = 1
    expected_effects["http_request_app_property_reads"] = 3
    expected_effects["ws_app_property_reads"] = 2
    expected_effects["app_state_property_reads"] = 5
    expected_effects["json_response_calls"] = [
        ("http_req1", 503, {"detail": "Service unavailable"}),
        ("http_req2", 503, {"detail": "Service unavailable"}),
        ("http_req3", 503, {"detail": "Service unavailable"}),
    ]
    expected_effects["ws_denial_responses"] = [
        ("browser_gate_read_count", 503, b'{"detail":"Service unavailable"}', "application/json"),
        ("native_gate_read_count", 503, b'{"detail":"Service unavailable"}', "application/json"),
    ]
    expected_effects["event_trace"] = [
        "http_req1:request_app",
        "http_req1:app_state",
        "http_req1:ready_read",
        "http_req1:json_response:503",
        "http_req2:request_app",
        "http_req2:app_state",
        "http_req2:ready_read",
        "http_req2:json_response:503",
        "http_req3:request_app",
        "http_req3:app_state",
        "http_req3:ready_read",
        "http_req3:json_response:503",
        "browser:ws_app",
        "browser:app_state",
        "browser:ready_read",
        "browser:send_denial:503",
        "native:ws_app",
        "native:app_state",
        "native:ready_read",
        "native:send_denial:503",
    ]

    if effects != expected_effects:
        raise AssertionError("EFFECTS_SCHEMA_MISMATCH")

    # 4. Identity & content snapshots
    assert main.settings is (settings_spy if settings_obj is not None else None)
    assert main.logger is logger_spy
    assert main.ws_manager is fake_wsm
    assert main.session_mgr is fake_native_sm_inst
    assert main.firestore_storage is fake_fs
    assert main.verify_bearer_token is counting_verify
    assert main._read_session is counting_read_session
    assert main.deserialize_session is counting_deserialize
    assert main.set_current_auth is counting_set_auth
    assert main.reset_current_auth is counting_reset_auth
    assert main.set_auth_enforced is counting_set_enforced
    assert main.reset_auth_enforced is counting_reset_enforced
    assert main.StreamManager is InstrumentedStreamManager
    assert main._close_ws_at_expiry is counting_close_ws_factory
    assert main.native_sm_lock is fresh_lock

    assert main.ws_tickets is tickets_mapping
    assert main.stream_keys is stream_keys_mapping
    assert main.stop_capabilities is stop_cap_mapping
    assert main.context_windows is fresh_context_windows
    assert main.pipeline_tasks is fresh_pipeline_tasks
    assert main.native_session_health is fresh_native_health
    assert main.native_frame_last_seq is fresh_native_frame_last_seq
    assert main.native_stream_managers is fresh_native_sm
    assert main.stream_managers is fresh_stream_managers
    assert main.deleted_sessions is fresh_deleted

    assert tickets_mapping.raw_dict() == {ticket: ticket_entry}
    assert stream_keys_mapping.raw_dict() == {"s1": "test-stream-key"}
    assert stop_cap_mapping.raw_dict() == {"cap-1": stop_cap_entry}
    assert fresh_context_windows.raw_dict() == {}
    assert fresh_pipeline_tasks.raw_dict() == {}
    assert fresh_native_health.raw_dict() == {}
    assert fresh_native_frame_last_seq.raw_dict() == {}
    assert fresh_native_sm.raw_dict() == {}
    assert fresh_stream_managers.raw_dict() == {}
    assert fresh_deleted.raw_set() == set()

    assert fake_wsm.connect_calls == []
    assert fake_wsm.disconnect_calls == []
    assert fake_wsm.broadcast_calls == []
    assert fake_wsm.seq_calls == []
    assert current_auth() is None
    assert auth_is_enforced() is False


@pytest.mark.parametrize(
    "unready_val",
    [False, None, 0, 1, "true", "false", "1", object()],
)
def test_unready_readiness_effect_matrix_row(unready_val, monkeypatch):
    """Fresh per-row harness for each unready state proving exact 503 and zero post-guard effects."""
    s = auth_settings()
    _run_primary_negative_readiness_row(unready_val, s, monkeypatch)


def test_settings_none_readiness_effect_matrix_row(monkeypatch):
    """Fresh harness for settings=None with ready=True proving exact 503 and zero post-guard effects."""
    _run_primary_negative_readiness_row(True, None, monkeypatch)


@pytest.mark.parametrize(
    "unready_val, has_settings",
    [
        (False, True),
        (None, True),
        (0, True),
        (1, True),
        ("true", True),
        ("false", True),
        ("1", True),
        (object(), True),
        (True, False),
    ],
)
def test_unready_fallback_close_when_no_send_denial_response(unready_val, has_settings, monkeypatch):
    """WebSockets without send_denial_response capability close cleanly with code 1008 and zero post-guard effects."""
    import json
    import struct
    import threading

    real_asyncio = main.asyncio
    real_datetime = main.datetime
    real_secrets = main.secrets
    real_json_response = main.JSONResponse

    effects = make_wired_effect_schema()

    s = auth_settings() if has_settings else None
    main.app.state.ready = unready_val
    if s is not None:
        settings_spy = SettingsAttributeSpy(s, effects)
        monkeypatch.setattr(main, "settings", settings_spy)
    else:
        settings_spy = None
        monkeypatch.setattr(main, "settings", None)

    logger_spy = LoggerSpy(effects)
    monkeypatch.setattr(main, "logger", logger_spy)

    ticket = "test-ticket-fallback"
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    exp_time = datetime.now(timezone.utc) + timedelta(seconds=60)
    ticket_entry = InstrumentedTicketEntry(user, "s1", exp_time, effects)

    tickets_mapping = RecordingDict("ws_tickets", effects)
    tickets_mapping.raw_set(ticket, ticket_entry)
    monkeypatch.setattr(main, "ws_tickets", tickets_mapping)

    stream_keys_mapping = RecordingDict("stream_keys", effects)
    stream_keys_mapping.raw_set("s1", "test-stream-key")
    monkeypatch.setattr(main, "stream_keys", stream_keys_mapping)

    stop_cap_entry = (user, "s1", exp_time)
    stop_cap_mapping = RecordingDict("stop_capabilities", effects)
    stop_cap_mapping.raw_set("cap-1", stop_cap_entry)
    monkeypatch.setattr(main, "stop_capabilities", stop_cap_mapping)

    fresh_context_windows = RecordingDict("context_windows", effects)
    monkeypatch.setattr(main, "context_windows", fresh_context_windows)
    fresh_pipeline_tasks = RecordingDict("pipeline_tasks", effects)
    monkeypatch.setattr(main, "pipeline_tasks", fresh_pipeline_tasks)
    fresh_native_health = RecordingDict("native_session_health", effects)
    monkeypatch.setattr(main, "native_session_health", fresh_native_health)
    fresh_native_frame_last_seq = RecordingDict("native_frame_last_seq", effects)
    monkeypatch.setattr(main, "native_frame_last_seq", fresh_native_frame_last_seq)
    fresh_native_sm = RecordingDict("native_stream_managers", effects)
    monkeypatch.setattr(main, "native_stream_managers", fresh_native_sm)
    fresh_stream_managers = RecordingDict("stream_managers", effects)
    monkeypatch.setattr(main, "stream_managers", fresh_stream_managers)
    fresh_deleted = RecordingSet("deleted_sessions", effects)
    monkeypatch.setattr(main, "deleted_sessions", fresh_deleted)
    fresh_lock = InstrumentedLock(effects)
    monkeypatch.setattr(main, "native_sm_lock", fresh_lock)

    fake_wsm = FakeWSConnectionManager(effects)
    monkeypatch.setattr(main, "ws_manager", fake_wsm)

    fake_http_sm = FakeHttpSessionManager(effects)
    fake_native_sm_inst = FakeNativeSessionManager(effects)
    monkeypatch.setattr(main, "session_mgr", fake_http_sm)

    fake_fs = FakeFirestoreStorage(effects)
    monkeypatch.setattr(main, "firestore_storage", fake_fs)

    def counting_verify(authorization=None, req_settings=None, *args, **kwargs):
        effects["verify_bearer_calls"].append((authorization, req_settings, args, kwargs))
        if authorization and authorization.startswith("Bearer "):
            return user
        raise AuthenticationError("Missing bearer token")

    monkeypatch.setattr(main, "verify_bearer_token", counting_verify)

    async def counting_read_session(session_id):
        effects["browser_read_session_calls"].append(session_id)
        return SimpleNamespace(
            owner_id="uid-a",
            org_id="ella-internal",
            status=SessionStatus.ACTIVE,
            model_dump=lambda: effects["model_dump_calls"].append(session_id) or {"session_id": session_id},
        )

    monkeypatch.setattr(main, "_read_session", counting_read_session)

    def counting_deserialize(session_id, record):
        effects["deserialize_session_calls"].append((session_id, record))
        return SimpleNamespace(
            owner_id="uid-a",
            org_id="ella-internal",
            status=SessionStatus.ACTIVE,
            model_dump=lambda: effects["model_dump_calls"].append(session_id) or {"session_id": session_id},
        )

    monkeypatch.setattr(main, "deserialize_session", counting_deserialize)

    orig_set_auth = main.set_current_auth

    def counting_set_auth(ctx):
        effects["set_current_auth_calls"].append(ctx)
        return orig_set_auth(ctx)

    monkeypatch.setattr(main, "set_current_auth", counting_set_auth)

    orig_reset_auth = main.reset_current_auth

    def counting_reset_auth(token):
        effects["reset_current_auth_calls"].append(token)
        return orig_reset_auth(token)

    monkeypatch.setattr(main, "reset_current_auth", counting_reset_auth)

    orig_set_enforced = main.set_auth_enforced

    def counting_set_enforced(value=True):
        effects["set_auth_enforced_calls"].append(value)
        return orig_set_enforced(value)

    monkeypatch.setattr(main, "set_auth_enforced", counting_set_enforced)

    orig_reset_enforced = main.reset_auth_enforced

    def counting_reset_enforced(token):
        effects["reset_auth_enforced_calls"].append(token)
        return orig_reset_enforced(token)

    monkeypatch.setattr(main, "reset_auth_enforced", counting_reset_enforced)

    tracked_expiry_coro_obj = None

    async def fake_expiry_coro(ws, exp):
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise

    def counting_close_ws_factory(ws, exp):
        nonlocal tracked_expiry_coro_obj
        effects["browser_expiry_coro_create_count"] += 1
        tracked_expiry_coro_obj = fake_expiry_coro(ws, exp)
        return tracked_expiry_coro_obj

    monkeypatch.setattr(main, "_close_ws_at_expiry", counting_close_ws_factory)

    class InstrumentedStreamManager(FakeStreamManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, tracker=effects)

    monkeypatch.setattr(main, "StreamManager", InstrumentedStreamManager)

    # Direct invocations in explicitly owned event loop
    ORIG_FACTORY_UNSET = object()
    owned_loop = None
    orig_task_factory = ORIG_FACTORY_UNSET
    owner_thread_id = None
    dt_proxy = None
    secrets_proxy = None
    asyncio_proxy = None
    json_response_recorder = None

    installed_factory = False
    installed_factory_verified = False
    installed_dt = False
    installed_dt_verified = False
    installed_secrets = False
    installed_secrets_verified = False
    installed_asyncio = False
    installed_asyncio_verified = False
    installed_json = False
    installed_json_verified = False

    runner_coro_obj = None
    runner_task = None
    runner_timed_out = False

    intercepted_task_records: list[InterceptedTaskRecord] = []
    classified_task_proxies: list[TrackedTaskProxy] = []
    all_known_tasks: list[asyncio.Task] = []
    test_owned_tasks: list[asyncio.Task] = []
    test_owned_coroutines: list[Any] = []
    constructor_side_effect_tasks: list[asyncio.Task] = []

    interception_close_attempts: list[Any] = []
    interception_close_successes: list[Any] = []
    positional_close_attempts: list[Any] = []
    positional_close_successes: list[Any] = []
    other_rejection_close_attempts: list[Any] = []
    other_rejection_close_successes: list[Any] = []
    rescue_close_attempts: list[Any] = []
    rescue_close_successes: list[Any] = []

    test_close_attempts: list[Any] = []
    test_close_successes: list[Any] = []
    test_owned_unauthorized_rejection_close_attempts: list[Any] = []
    test_owned_unauthorized_rejection_close_successes: list[Any] = []

    harness_cancelled_tasks: list[asyncio.Task] = []
    normal_terminal_tasks: list[asyncio.Task] = []
    cancelled_terminal_tasks: list[asyncio.Task] = []
    exception_terminal_tasks: list[asyncio.Task] = []
    outcome_retrieved_tasks: list[asyncio.Task] = []
    runner_failures: list[str] = []

    cancel_control_origin_calls: list[dict[str, Any]] = []
    late_control_origin_calls: list[dict[str, Any]] = []
    create_task_control_origin_calls: list[dict[str, Any]] = []
    ensure_future_control_origin_calls: list[dict[str, Any]] = []
    resolver_miss_origin_calls: list[dict[str, Any]] = []

    control_proxy_tracker: dict[str, list[Any]] = {
        "browser_expiry_task_cancel_calls": [],
        "native_stall_task_cancel_calls": [],
        "browser_expiry_task_await_calls": [],
        "native_stall_task_await_calls": [],
        "proxies": [],
    }

    class InjectedConstructionControlException(BaseException):
        pass

    class ExpectedUnclassifiedFutureRejection(Exception):
        pass

    class ExpectedResolverMissRejection(Exception):
        pass

    class ExpectedPositionalRejection(Exception):
        pass

    class ExpectedTaskUnsupportedArgsException(Exception):
        pass

    class ExpectedTaskWrongLoopException(Exception):
        pass

    class ExpectedCreateTaskUnsupportedArgsException(Exception):
        pass

    class ExpectedCreateTaskWrongLoopException(Exception):
        pass

    class ExpectedEnsureFutureUnsupportedArgsException(Exception):
        pass

    class ExpectedEnsureFutureWrongLoopException(Exception):
        pass

    injected_construction_exception_instance = InjectedConstructionControlException()
    expected_unclassified_future_rejection_instance = ExpectedUnclassifiedFutureRejection()
    expected_resolver_miss_rejection_instance = ExpectedResolverMissRejection()
    expected_positional_rejection_instance = ExpectedPositionalRejection()
    expected_task_unsupported_args_exc = ExpectedTaskUnsupportedArgsException()
    expected_task_wrong_loop_exc = ExpectedTaskWrongLoopException()
    expected_create_task_unsupported_args_exc = ExpectedCreateTaskUnsupportedArgsException()
    expected_create_task_wrong_loop_exc = ExpectedCreateTaskWrongLoopException()
    expected_ensure_future_unsupported_args_exc = ExpectedEnsureFutureUnsupportedArgsException()
    expected_ensure_future_wrong_loop_exc = ExpectedEnsureFutureWrongLoopException()

    positional_probe_sentinel = object()
    other_rejection_positional_sentinel = object()
    other_rejection_wrong_loop_sentinel = object()
    non_owning_constructor_sentinel = object()
    direct_sentinel = object()

    construction_control_classification_records: list[tuple[Any, BaseException]] = []
    explicit_non_owning_classification_records: list[tuple[Any, Any]] = []
    expected_unclassified_future_rejections: list[BaseException] = []
    expected_resolver_miss_rejections: list[BaseException] = []
    expected_positional_rejections: list[BaseException] = []
    recorded_other_rejection_exceptions: list[BaseException] = []
    ensure_future_repass_proxy_returns: list[Any] = []
    ensure_future_repass_task_returns: list[Any] = []
    unclassified_future_cancels: list[bool] = []
    unclassified_future_caught_cancellations: list[BaseException] = []
    unclassified_future_retrievals: list[Any] = []
    resolver_miss_matched_records: list[InterceptedTaskRecord] = []
    resolver_miss_calls: list[Any] = []
    non_owning_constructor_calls: list[Any] = []

    final_inventory_authorized_tasks_cutoff: list[asyncio.Task] = []
    final_inventory_snapshots: list[list[asyncio.Task]] = []
    final_inventory_pass_tags: list[str] = []
    cleanup_discovered_tasks: list[asyncio.Task] = []

    cancel_control_coro_obj = None
    cancel_control_proxy = None
    create_task_control_coro_obj = None
    create_task_control_proxy = None
    ensure_future_control_coro_obj = None
    ensure_future_control_proxy = None
    resolver_miss_source_coro = None
    positional_probe_source_coro = None
    task_unsupported_args_source_coro = None
    task_wrong_loop_source_coro = None
    create_task_unsupported_args_source_coro = None
    create_task_wrong_loop_source_coro = None
    ensure_future_unsupported_args_source_coro = None
    ensure_future_wrong_loop_source_coro = None
    unclassified_future_obj = None
    late_control_coro_obj = None
    late_control_proxy = None
    late_control_scheduled = False
    construct_fail_coro_obj = None
    non_owning_control_coro_obj = None

    loop_closed_flag = False
    restored_dt = False
    restored_secrets = False
    restored_asyncio = False
    restored_json = False
    restored_factory = False

    def _execute_source_close(coro: Any, pre_attr_code: str, pre_check_code: str, close_code: str, post_attr_code: str, post_check_code: str) -> bool:
        if coro is None:
            return False
        pre_frame = None
        pre_running = None
        try:
            pre_frame = coro.cr_frame
            pre_running = coro.cr_running
        except BaseException:
            runner_failures.append(pre_attr_code)
            return False

        if pre_frame is None or pre_running is not False:
            runner_failures.append(pre_check_code)
            return False

        close_returned = False
        try:
            coro.close()
            close_returned = True
        except BaseException:
            runner_failures.append(close_code)
            return False

        if close_returned:
            post_frame = None
            post_running = None
            try:
                post_frame = coro.cr_frame
                post_running = coro.cr_running
            except BaseException:
                runner_failures.append(post_attr_code)
                return False

            if post_frame is None and post_running is False:
                return True
            else:
                runner_failures.append(post_check_code)
                return False
        return False

    def close_source_interception(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in interception_close_attempts):
            runner_failures.append("INTERCEPTION_SOURCE_DUPLICATE_HANDOFF")
            return False
        interception_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "INTERCEPTION_SOURCE_PRE_STATE_ATTR_FAILED",
            "INTERCEPTION_SOURCE_PRE_STATE_CHECK_FAILED",
            "INTERCEPTION_SOURCE_CLOSE_FAILED",
            "INTERCEPTION_SOURCE_POST_STATE_ATTR_FAILED",
            "INTERCEPTION_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            interception_close_successes.append(coro)
        return ok

    def close_source_positional(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in positional_close_attempts):
            runner_failures.append("POSITIONAL_SOURCE_DUPLICATE_HANDOFF")
            return False
        positional_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "POSITIONAL_SOURCE_PRE_STATE_ATTR_FAILED",
            "POSITIONAL_SOURCE_PRE_STATE_CHECK_FAILED",
            "POSITIONAL_SOURCE_CLOSE_FAILED",
            "POSITIONAL_SOURCE_POST_STATE_ATTR_FAILED",
            "POSITIONAL_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            positional_close_successes.append(coro)
        return ok

    def close_source_other_rejection(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in other_rejection_close_attempts):
            runner_failures.append("OTHER_REJECTION_SOURCE_DUPLICATE_HANDOFF")
            return False
        other_rejection_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "OTHER_REJECTION_SOURCE_PRE_STATE_ATTR_FAILED",
            "OTHER_REJECTION_SOURCE_PRE_STATE_CHECK_FAILED",
            "OTHER_REJECTION_SOURCE_CLOSE_FAILED",
            "OTHER_REJECTION_SOURCE_POST_STATE_ATTR_FAILED",
            "OTHER_REJECTION_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            other_rejection_close_successes.append(coro)
        return ok

    def close_source_rescue(coro: Any) -> bool:
        if coro is None:
            return False
        if any(c is coro for c in rescue_close_attempts):
            runner_failures.append("RESCUE_SOURCE_DUPLICATE_HANDOFF")
            return False
        runner_failures.append("SOURCE_RESCUE_REQUIRED")
        rescue_close_attempts.append(coro)
        ok = _execute_source_close(
            coro,
            "RESCUE_SOURCE_PRE_STATE_ATTR_FAILED",
            "RESCUE_SOURCE_PRE_STATE_CHECK_FAILED",
            "RESCUE_SOURCE_CLOSE_FAILED",
            "RESCUE_SOURCE_POST_STATE_ATTR_FAILED",
            "RESCUE_SOURCE_POST_STATE_CHECK_FAILED",
        )
        if ok:
            rescue_close_successes.append(coro)
        return ok

    def close_test_owned_once(coro: Any) -> bool:
        if coro is None:
            return False
        for c in test_close_attempts:
            if c is coro:
                return False
        test_close_attempts.append(coro)

        close_returned = False
        try:
            coro.close()
            close_returned = True
        except BaseException:
            runner_failures.append("TEST_OWNED_CORO_CLOSE_FAILED")
            return False

        if close_returned:
            try:
                frame = coro.cr_frame
                running = coro.cr_running
                if frame is None and running is False:
                    test_close_successes.append(coro)
                    return True
                else:
                    runner_failures.append("TEST_OWNED_CORO_POST_STATE_CHECK_FAILED")
            except BaseException:
                runner_failures.append("TEST_OWNED_CORO_POST_STATE_CHECK_FAILED")
        return False

    def close_test_owned_unauthorized_rejection(coro: Any) -> bool:
        if coro is None:
            return False
        test_owned_unauthorized_rejection_close_attempts.append(coro)
        ok = close_test_owned_once(coro)
        if ok:
            test_owned_unauthorized_rejection_close_successes.append(coro)
        return ok

    def injected_task_constructor(coro, loop=None, **kwargs):
        raise injected_construction_exception_instance

    def explicit_non_owning_constructor(coro, loop=None, **kwargs):
        non_owning_constructor_calls.append((coro, loop))
        return non_owning_constructor_sentinel

    def construct_test_owned_task(coro: Any, loop: asyncio.AbstractEventLoop, task_constructor=real_asyncio.Task, **kwargs) -> asyncio.Task:
        if not any(c is coro for c in test_owned_coroutines):
            test_owned_coroutines.append(coro)

        if task_constructor is not real_asyncio.Task and task_constructor is not injected_task_constructor and task_constructor is not explicit_non_owning_constructor:
            runner_failures.append("UNAUTHORIZED_TASK_CONSTRUCTOR")
            close_test_owned_unauthorized_rejection(coro)
            raise RuntimeError("UNAUTHORIZED_TASK_CONSTRUCTOR")

        before_tasks = []
        try:
            before_tasks = list(real_asyncio.all_tasks(loop))
        except BaseException:
            runner_failures.append("CONSTRUCTOR_BEFORE_INVENTORY_FAILED")

        candidate = None
        try:
            if task_constructor is real_asyncio.Task:
                candidate = real_asyncio.Task(coro, loop=owned_loop, **kwargs)
            else:
                candidate = task_constructor(coro, loop=loop, **kwargs)
        except InjectedConstructionControlException as exc:
            after_tasks = []
            try:
                after_tasks = list(real_asyncio.all_tasks(loop))
            except BaseException:
                runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

            new_side_effects = [t for t in after_tasks if not any(b is t for b in before_tasks)]
            if new_side_effects:
                for t in new_side_effects:
                    constructor_side_effect_tasks.append(t)
                    if not any(k is t for k in all_known_tasks):
                        all_known_tasks.append(t)
                runner_failures.append("CONSTRUCTOR_RAISED_WITH_SIDE_EFFECT_TASK")
                raise

            inventory_equal = (
                len(before_tasks) == len(after_tasks)
                and all(any(a is b for a in after_tasks) for b in before_tasks)
                and all(any(b is a for b in before_tasks) for a in after_tasks)
            )
            if not inventory_equal:
                runner_failures.append("CONSTRUCTOR_INVENTORY_MUTATED_ON_EXCEPTION")
                raise

            if exc is injected_construction_exception_instance:
                closed_ok = close_test_owned_once(coro)
                if closed_ok:
                    construction_control_classification_records.append((coro, exc))
                else:
                    runner_failures.append("CONSTRUCTION_CONTROL_CLOSE_FAILED")
            else:
                runner_failures.append("CONSTRUCTION_CONTROL_WRONG_EXCEPTION_INSTANCE")
            raise
        except BaseException:
            after_tasks = []
            try:
                after_tasks = list(real_asyncio.all_tasks(loop))
            except BaseException:
                runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

            new_side_effects = [t for t in after_tasks if not any(b is t for b in before_tasks)]
            if new_side_effects:
                for t in new_side_effects:
                    constructor_side_effect_tasks.append(t)
                    if not any(k is t for k in all_known_tasks):
                        all_known_tasks.append(t)
                runner_failures.append("CONSTRUCTOR_RAISED_WITH_SIDE_EFFECT_TASK")
            else:
                runner_failures.append("TEST_OWNED_TASK_CONSTRUCT_FAILED")
            raise

        is_task = False
        try:
            is_task = isinstance(candidate, real_asyncio.Task)
        except BaseException:
            runner_failures.append("CANDIDATE_TASK_ISINSTANCE_FAILED")

        if is_task:
            if not any(k is candidate for k in all_known_tasks):
                all_known_tasks.append(candidate)
            if not any(k is candidate for k in test_owned_tasks):
                test_owned_tasks.append(candidate)

            after_tasks = []
            try:
                after_tasks = list(real_asyncio.all_tasks(loop))
            except BaseException:
                runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

            candidate_in_after = sum(1 for t in after_tasks if t is candidate)
            if candidate_in_after != 1:
                runner_failures.append("CANDIDATE_TASK_INVENTORY_COUNT_MISMATCH")

            for b in before_tasks:
                if sum(1 for a in after_tasks if a is b) != 1:
                    runner_failures.append("BEFORE_TASK_MISSING_FROM_AFTER_INVENTORY")

            new_side_effects = [t for t in after_tasks if (t is not candidate and not any(b is t for b in before_tasks))]
            if new_side_effects:
                for t in new_side_effects:
                    constructor_side_effect_tasks.append(t)
                    if not any(k is t for k in all_known_tasks):
                        all_known_tasks.append(t)
                runner_failures.append("CONSTRUCTOR_RETURNED_WITH_SIDE_EFFECT_TASK")

            cand_loop = None
            try:
                cand_loop = candidate.get_loop()
            except BaseException:
                runner_failures.append("CANDIDATE_GET_LOOP_FAILED")

            if cand_loop is not owned_loop:
                runner_failures.append("TASK_CONSTRUCTOR_LOOP_AUTHORITY_BREACH")
            return candidate

        after_tasks = []
        try:
            after_tasks = list(real_asyncio.all_tasks(loop))
        except BaseException:
            runner_failures.append("CONSTRUCTOR_AFTER_INVENTORY_FAILED")

        new_side_effects = [t for t in after_tasks if not any(b is t for b in before_tasks)]
        if new_side_effects:
            for t in new_side_effects:
                constructor_side_effect_tasks.append(t)
                if not any(k is t for k in all_known_tasks):
                    all_known_tasks.append(t)
            runner_failures.append("NON_TASK_RETURN_WITH_SIDE_EFFECT_TASK")
            raise RuntimeError("NON_TASK_RETURN_WITH_SIDE_EFFECT_TASK")

        inventory_equal = (
            len(before_tasks) == len(after_tasks)
            and all(any(a is b for a in after_tasks) for b in before_tasks)
            and all(any(b is a for b in before_tasks) for a in after_tasks)
        )
        if not inventory_equal:
            runner_failures.append("NON_OWNING_INVENTORY_MUTATED")
            raise RuntimeError("NON_OWNING_INVENTORY_MUTATED")

        if task_constructor is explicit_non_owning_constructor and candidate is non_owning_constructor_sentinel:
            if (
                len(non_owning_constructor_calls) == 1
                and non_owning_constructor_calls[0][0] is coro
                and non_owning_constructor_calls[0][1] is loop
            ):
                closed_ok = close_test_owned_once(coro)
                if closed_ok:
                    explicit_non_owning_classification_records.append((coro, candidate))
                    return candidate
                else:
                    runner_failures.append("NON_OWNING_CONTROL_CLOSE_FAILED")
            else:
                runner_failures.append("NON_OWNING_CONTROL_CALL_MISMATCH")
            raise RuntimeError("NON_OWNING_CONTROL_FAILED")

        runner_failures.append("UNAUTHORIZED_NON_TASK_RETURN")
        raise RuntimeError("UNAUTHORIZED_NON_TASK_RETURN")

    async def controlled_parking_coro():
        try:
            while True:
                await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            raise

    async def controlled_finishing_coro():
        pass

    def safe_bounded_drive(loop: asyncio.AbstractEventLoop, duration: float, reg_code: str, run_code: str, cancel_code: str) -> bool:
        if loop is None:
            runner_failures.append("DRIVE_LOOP_NONE")
            return False
        is_closed = False
        try:
            is_closed = loop.is_closed()
        except BaseException:
            runner_failures.append("LOOP_CLOSED_CHECK_FAILED")
            return False
        if is_closed:
            runner_failures.append("DRIVE_LOOP_CLOSED")
            return False

        if threading.get_ident() != owner_thread_id:
            runner_failures.append("DRIVE_WRONG_THREAD")
            return False

        if loop is not owned_loop:
            runner_failures.append("DRIVE_WRONG_LOOP")
            return False

        handle = None
        try:
            handle = loop.call_later(duration, loop.stop)
        except BaseException:
            runner_failures.append(reg_code)
            return False

        if handle is None:
            runner_failures.append(reg_code)
            return False

        drive_success = False
        cancel_success = False
        try:
            if not isinstance(handle, real_asyncio.TimerHandle):
                runner_failures.append(reg_code)
                return False

            is_cancelled = False
            try:
                is_cancelled = handle.cancelled()
            except BaseException:
                runner_failures.append(reg_code)
                return False

            if is_cancelled:
                runner_failures.append(reg_code)
                return False

            try:
                loop.run_forever()
                drive_success = True
            except BaseException:
                runner_failures.append(run_code)
        finally:
            try:
                handle.cancel()
                cancel_success = True
            except BaseException:
                runner_failures.append(cancel_code)

        return drive_success and cancel_success

    def resolve_intercepted_record(task: asyncio.Task, probe_resolver_miss: bool = False) -> InterceptedTaskRecord | None:
        for r in intercepted_task_records:
            if r.real_task is task:
                if probe_resolver_miss and r.source_coro is resolver_miss_source_coro:
                    resolver_miss_matched_records.append(r)
                    resolver_miss_calls.append(task)
                    return None
                return r
        return None

    def harness_cancel_once(task: asyncio.Task):
        if task is None:
            return
        for t in harness_cancelled_tasks:
            if t is task:
                return

        if threading.get_ident() != owner_thread_id:
            runner_failures.append("CANCEL_WRONG_THREAD")
            return

        try:
            if task.get_loop() is not owned_loop:
                runner_failures.append("CANCEL_WRONG_LOOP")
                return
        except BaseException:
            runner_failures.append("CANCEL_LOOP_CHECK_FAILED")
            return

        is_done = False
        try:
            is_done = task.done()
        except BaseException:
            runner_failures.append("CANCEL_DONE_CHECK_FAILED")
            return

        if is_done:
            return

        record = resolve_intercepted_record(task)
        designated_proxy = record.proxy if record is not None else None

        if record is not None and designated_proxy is None:
            runner_failures.append("CLASSIFIED_TASK_PROXY_MISSING")
            return

        harness_cancelled_tasks.append(task)

        if designated_proxy is not None:
            try:
                res = designated_proxy.cancel()
                if res is not True:
                    runner_failures.append("PROXY_CANCEL_NOT_TRUE")
            except BaseException:
                runner_failures.append("PROXY_CANCEL_FAILED")
        else:
            try:
                res = task.cancel()
                if res is not True:
                    runner_failures.append("TASK_CANCEL_NOT_TRUE")
            except BaseException:
                runner_failures.append("TASK_CANCEL_FAILED")

    def retrieve_terminal_outcome(task: asyncio.Task):
        if task is None:
            return
        for t in outcome_retrieved_tasks:
            if t is task:
                return

        is_done = False
        try:
            is_done = task.done()
        except BaseException:
            runner_failures.append("TASK_DONE_CHECK_FAILED")
            return

        if not is_done:
            runner_failures.append("TASK_NOT_DONE_AT_RETRIEVAL")
            return

        is_cancelled = False
        try:
            is_cancelled = task.cancelled()
        except BaseException:
            runner_failures.append("TASK_CANCELLED_CHECK_FAILED")

        if is_cancelled:
            cancelled_terminal_tasks.append(task)
            outcome_retrieved_tasks.append(task)
            if task is runner_task and not runner_timed_out:
                runner_failures.append("RUNNER_TASK_UNEXPECTED_CANCEL")
            return

        exc = None
        try:
            exc = task.exception()
        except BaseException:
            runner_failures.append("TASK_EXCEPTION_CHECK_FAILED")

        if exc is not None:
            exception_terminal_tasks.append(task)
            runner_failures.append("TASK_TERMINAL_EXCEPTION")
            outcome_retrieved_tasks.append(task)
        else:
            normal_terminal_tasks.append(task)
            outcome_retrieved_tasks.append(task)

    def custom_task_factory(loop, coro, **factory_kwargs):
        if threading.get_ident() != owner_thread_id:
            runner_failures.append("TASK_FACTORY_WRONG_THREAD")
            close_ok = close_source_interception(coro)
            if not close_ok:
                runner_failures.append("TASK_FACTORY_WRONG_THREAD_CLOSE_FAILED")
            raise RuntimeError("TASK_FACTORY_WRONG_THREAD")

        if loop is not owned_loop:
            runner_failures.append("TASK_FACTORY_WRONG_LOOP")
            close_ok = close_source_interception(coro)
            if not close_ok:
                runner_failures.append("TASK_FACTORY_WRONG_LOOP_CLOSE_FAILED")
            raise RuntimeError("TASK_FACTORY_WRONG_LOOP")

        category = "unexpected"
        is_browser = False
        if coro is tracked_expiry_coro_obj:
            category = "browser_expiry"
            is_browser = True
        elif getattr(getattr(coro, "cr_code", None), "co_name", "") == "stall_watchdog":
            category = "native_stall"
            is_browser = False
        elif coro is cancel_control_coro_obj:
            category = "cancel_control"
        elif coro is late_control_coro_obj:
            category = "late_control"
        elif coro is create_task_control_coro_obj:
            category = "create_task_control"
        elif coro is ensure_future_control_coro_obj:
            category = "ensure_future_control"
        elif coro is resolver_miss_source_coro:
            category = "create_task_resolver_miss_control"

        auth_val = None
        auth_captured = False
        try:
            auth_val = main.current_auth()
            auth_captured = True
        except BaseException:
            runner_failures.append("TASK_FACTORY_CURRENT_AUTH_FAILED")

        if not auth_captured:
            close_ok = close_source_interception(coro)
            if not close_ok:
                runner_failures.append("TASK_FACTORY_AUTH_FAIL_CLOSE_NOT_PROVEN")
            raise RuntimeError("TASK_FACTORY_CURRENT_AUTH_FAILED")

        origin_rec = {"coro": coro, "auth": auth_val}

        close_ok = close_source_interception(coro)
        if not close_ok:
            runner_failures.append("TASK_FACTORY_SOURCE_CLOSE_NOT_PROVEN")
            raise RuntimeError("TASK_FACTORY_SOURCE_CLOSE_NOT_PROVEN")

        if category in ("late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control"):
            park_coro = controlled_finishing_coro()
        else:
            park_coro = controlled_parking_coro()

        real_task = construct_test_owned_task(park_coro, loop, **factory_kwargs)

        proxy_tracker = control_proxy_tracker if category in ("cancel_control", "late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control") else effects

        proxy = None
        try:
            proxy = TrackedTaskProxy(
                real_task,
                proxy_tracker,
                is_browser=is_browser,
                source_coro=coro,
                controlled_coro=park_coro,
            )
        except BaseException:
            runner_failures.append("TASK_FACTORY_PROXY_CONSTRUCT_FAILED")
            raise RuntimeError("TASK_FACTORY_PROXY_CONSTRUCT_FAILED")

        record = None
        try:
            record = InterceptedTaskRecord(coro, origin_rec, park_coro, real_task, proxy, category)
        except BaseException:
            runner_failures.append("TASK_FACTORY_RECORD_CONSTRUCT_FAILED")
            raise RuntimeError("TASK_FACTORY_RECORD_CONSTRUCT_FAILED")

        try:
            if category == "browser_expiry":
                effects["browser_expiry_task_create_calls"].append(origin_rec)
            elif category == "native_stall":
                effects["native_stall_task_create_calls"].append(origin_rec)
            elif category == "unexpected":
                effects["unexpected_task_create_calls"].append(origin_rec)
            elif category == "cancel_control":
                cancel_control_origin_calls.append(origin_rec)
            elif category == "late_control":
                late_control_origin_calls.append(origin_rec)
            elif category == "create_task_control":
                create_task_control_origin_calls.append(origin_rec)
            elif category == "ensure_future_control":
                ensure_future_control_origin_calls.append(origin_rec)
            elif category == "create_task_resolver_miss_control":
                resolver_miss_origin_calls.append(origin_rec)
            else:
                runner_failures.append("TASK_FACTORY_UNKNOWN_CATEGORY")
                raise RuntimeError("TASK_FACTORY_UNKNOWN_CATEGORY")
        except RuntimeError:
            raise
        except BaseException:
            runner_failures.append("TASK_FACTORY_ORIGIN_PUBLICATION_FAILED")
            raise RuntimeError("TASK_FACTORY_ORIGIN_PUBLICATION_FAILED")

        try:
            classified_task_proxies.append(proxy)
            if proxy_tracker is control_proxy_tracker:
                control_proxy_tracker["proxies"].append(proxy)
        except BaseException:
            runner_failures.append("TASK_FACTORY_PROXY_PUBLICATION_FAILED")
            raise RuntimeError("TASK_FACTORY_PROXY_PUBLICATION_FAILED")

        try:
            intercepted_task_records.append(record)
        except BaseException:
            runner_failures.append("TASK_FACTORY_RECORD_PUBLICATION_FAILED")
            raise RuntimeError("TASK_FACTORY_RECORD_PUBLICATION_FAILED")

        return real_task

    class MainAsyncioProxy:
        def __getattr__(self, name):
            return getattr(real_asyncio, name)

        def Task(self, coro, *args, **kwargs):
            if args:
                if len(args) == 1 and args[0] is positional_probe_sentinel and coro is positional_probe_source_coro:
                    closed = close_source_positional(coro)
                    if closed:
                        raise expected_positional_rejection_instance
                    runner_failures.append("POSITIONAL_SOURCE_CLOSE_PROOF_FAILED")
                    raise RuntimeError("POSITIONAL_SOURCE_CLOSE_PROOF_FAILED")

                if len(args) == 1 and args[0] is other_rejection_positional_sentinel and coro is task_unsupported_args_source_coro:
                    closed = close_source_other_rejection(coro)
                    if closed:
                        raise expected_task_unsupported_args_exc
                    runner_failures.append("TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")
                    raise RuntimeError("TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")

                runner_failures.append("TASK_UNSUPPORTED_POSITIONAL_ARGS")
                if asyncio.iscoroutine(coro):
                    close_source_other_rejection(coro)
                raise RuntimeError("TASK_UNSUPPORTED_POSITIONAL_ARGS")

            loop = kwargs.pop("loop", owned_loop)
            if loop is not owned_loop:
                if loop is other_rejection_wrong_loop_sentinel and coro is task_wrong_loop_source_coro:
                    closed = close_source_other_rejection(coro)
                    if closed:
                        raise expected_task_wrong_loop_exc
                    runner_failures.append("TASK_WRONG_LOOP_CLOSE_FAILED")
                    raise RuntimeError("TASK_WRONG_LOOP_CLOSE_FAILED")

                runner_failures.append("PROXY_TASK_WRONG_LOOP")
                if asyncio.iscoroutine(coro):
                    close_source_other_rejection(coro)
                raise RuntimeError("PROXY_TASK_WRONG_LOOP")

            real_task = custom_task_factory(loop, coro, **kwargs)
            record = resolve_intercepted_record(real_task)
            if record is None or record.proxy is None:
                runner_failures.append("PROXY_RESOLUTION_FAILED")
                raise RuntimeError("PROXY_RESOLUTION_FAILED")
            return record.proxy

        def create_task(self, coro, *args, **kwargs):
            if args:
                if len(args) == 1 and args[0] is other_rejection_positional_sentinel and coro is create_task_unsupported_args_source_coro:
                    closed = close_source_other_rejection(coro)
                    if closed:
                        raise expected_create_task_unsupported_args_exc
                    runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")
                    raise RuntimeError("CREATE_TASK_UNSUPPORTED_ARGS_CLOSE_FAILED")

                runner_failures.append("CREATE_TASK_UNSUPPORTED_POSITIONAL_ARGS")
                if asyncio.iscoroutine(coro):
                    close_source_other_rejection(coro)
                raise RuntimeError("CREATE_TASK_UNSUPPORTED_POSITIONAL_ARGS")

            if "loop" in kwargs:
                passed_loop = kwargs.pop("loop")
                if passed_loop is not owned_loop:
                    if passed_loop is other_rejection_wrong_loop_sentinel and coro is create_task_wrong_loop_source_coro:
                        closed = close_source_other_rejection(coro)
                        if closed:
                            raise expected_create_task_wrong_loop_exc
                        runner_failures.append("CREATE_TASK_WRONG_LOOP_CLOSE_FAILED")
                        raise RuntimeError("CREATE_TASK_WRONG_LOOP_CLOSE_FAILED")

                    runner_failures.append("CREATE_TASK_WRONG_LOOP")
                    if asyncio.iscoroutine(coro):
                        close_source_other_rejection(coro)
                    raise RuntimeError("CREATE_TASK_WRONG_LOOP")

            real_task = owned_loop.create_task(coro, *args, **kwargs)

            is_resolver_probe = (coro is resolver_miss_source_coro)
            record = resolve_intercepted_record(real_task, probe_resolver_miss=is_resolver_probe)

            if is_resolver_probe:
                if record is None and len(resolver_miss_matched_records) == 1 and len(resolver_miss_calls) == 1:
                    raise expected_resolver_miss_rejection_instance
                runner_failures.append("RESOLVER_MISS_SEAM_FAILED")
                raise RuntimeError("RESOLVER_MISS_SEAM_FAILED")

            if record is None or record.proxy is None:
                runner_failures.append("CREATE_TASK_PROXY_RESOLUTION_FAILED")
                raise RuntimeError("CREATE_TASK_PROXY_RESOLUTION_FAILED")
            return record.proxy

        def ensure_future(self, coro_or_future, *args, **kwargs):
            if args:
                if len(args) == 1 and args[0] is other_rejection_positional_sentinel and coro_or_future is ensure_future_unsupported_args_source_coro:
                    closed = close_source_other_rejection(coro_or_future)
                    if closed:
                        raise expected_ensure_future_unsupported_args_exc
                    runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_CLOSE_FAILED")
                    raise RuntimeError("ENSURE_FUTURE_UNSUPPORTED_ARGS_CLOSE_FAILED")

                runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_POSITIONAL_ARGS")
                if asyncio.iscoroutine(coro_or_future):
                    close_source_other_rejection(coro_or_future)
                raise RuntimeError("ENSURE_FUTURE_UNSUPPORTED_POSITIONAL_ARGS")

            passed_loop = kwargs.pop("loop", owned_loop)
            if passed_loop is not owned_loop:
                if passed_loop is other_rejection_wrong_loop_sentinel and coro_or_future is ensure_future_wrong_loop_source_coro:
                    closed = close_source_other_rejection(coro_or_future)
                    if closed:
                        raise expected_ensure_future_wrong_loop_exc
                    runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_CLOSE_FAILED")
                    raise RuntimeError("ENSURE_FUTURE_WRONG_LOOP_CLOSE_FAILED")

                runner_failures.append("ENSURE_FUTURE_WRONG_LOOP")
                if asyncio.iscoroutine(coro_or_future):
                    close_source_other_rejection(coro_or_future)
                raise RuntimeError("ENSURE_FUTURE_WRONG_LOOP")

            if isinstance(coro_or_future, TrackedTaskProxy):
                rec = resolve_intercepted_record(coro_or_future._task)
                if rec is not None and rec.proxy is coro_or_future and rec.real_task.get_loop() is owned_loop and coro_or_future._source_coro is rec.source_coro and coro_or_future._controlled_coro is rec.controlled_coro:
                    if any(k is coro_or_future._task for k in all_known_tasks) and any(k is coro_or_future._task for k in test_owned_tasks) and any(p is coro_or_future for p in classified_task_proxies):
                        return coro_or_future
                runner_failures.append("ENSURE_FUTURE_PROXY_VALIDATION_FAILED")
                raise RuntimeError("ENSURE_FUTURE_PROXY_VALIDATION_FAILED")

            if isinstance(coro_or_future, asyncio.Task):
                rec = resolve_intercepted_record(coro_or_future)
                if rec is not None and rec.proxy is not None and coro_or_future.get_loop() is owned_loop and rec.real_task is coro_or_future:
                    if any(k is coro_or_future for k in all_known_tasks) and any(k is coro_or_future for k in test_owned_tasks) and any(p is rec.proxy for p in classified_task_proxies):
                        return rec.proxy
                runner_failures.append("ENSURE_FUTURE_UNCLASSIFIED_TASK_REJECTED")
                raise RuntimeError("ENSURE_FUTURE_UNCLASSIFIED_TASK_REJECTED")

            if asyncio.iscoroutine(coro_or_future):
                return self.create_task(coro_or_future, *args, **kwargs)

            if coro_or_future is unclassified_future_obj:
                raise expected_unclassified_future_rejection_instance

            runner_failures.append("ENSURE_FUTURE_UNCLASSIFIED_AWAITABLE_REJECTED")
            raise RuntimeError("ENSURE_FUTURE_UNCLASSIFIED_AWAITABLE_REJECTED")

    try:
        owner_thread_id = None
        try:
            owner_thread_id = threading.get_ident()
        except BaseException:
            runner_failures.append("OWNER_THREAD_ID_CAPTURE_FAILED")

        try:
            owned_loop = asyncio.new_event_loop()
        except BaseException:
            runner_failures.append("OWNED_LOOP_CREATE_FAILED")

        if owned_loop is not None:
            try:
                orig_task_factory = owned_loop.get_task_factory()
            except BaseException:
                runner_failures.append("ORIG_TASK_FACTORY_CAPTURE_FAILED")

            try:
                owned_loop.set_task_factory(custom_task_factory)
                installed_factory = True
            except BaseException:
                runner_failures.append("TASK_FACTORY_INSTALL_FAILED")

            try:
                if owned_loop.get_task_factory() is custom_task_factory:
                    installed_factory_verified = True
                else:
                    runner_failures.append("TASK_FACTORY_INSTALL_CHECK_FAILED")
            except BaseException:
                runner_failures.append("TASK_FACTORY_INSTALL_CHECK_FAILED")

            current_phase = "setup"
            try:
                dt_proxy = MainDateTimeProxy(real_datetime, effects, lambda: current_phase)
                monkeypatch.setattr(main, "datetime", dt_proxy)
                installed_dt = True
            except BaseException:
                runner_failures.append("DATETIME_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "datetime", direct_sentinel) is dt_proxy:
                    installed_dt_verified = True
                else:
                    runner_failures.append("DATETIME_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("DATETIME_PROXY_VERIFY_FAILED")

            try:
                secrets_proxy = MainSecretsProxy(real_secrets, effects)
                monkeypatch.setattr(main, "secrets", secrets_proxy)
                installed_secrets = True
            except BaseException:
                runner_failures.append("SECRETS_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "secrets", direct_sentinel) is secrets_proxy:
                    installed_secrets_verified = True
                else:
                    runner_failures.append("SECRETS_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("SECRETS_PROXY_VERIFY_FAILED")

            try:
                asyncio_proxy = MainAsyncioProxy()
                monkeypatch.setattr(main, "asyncio", asyncio_proxy)
                installed_asyncio = True
            except BaseException:
                runner_failures.append("ASYNCIO_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "asyncio", direct_sentinel) is asyncio_proxy:
                    installed_asyncio_verified = True
                else:
                    runner_failures.append("ASYNCIO_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("ASYNCIO_PROXY_VERIFY_FAILED")

            try:
                json_response_recorder = MainJSONResponseRecorder(real_json_response, effects, lambda: current_phase)
                monkeypatch.setattr(main, "JSONResponse", json_response_recorder)
                installed_json = True
            except BaseException:
                runner_failures.append("JSONRESPONSE_PROXY_INSTALL_FAILED")

            try:
                if getattr(main, "JSONResponse", direct_sentinel) is json_response_recorder:
                    installed_json_verified = True
                else:
                    runner_failures.append("JSONRESPONSE_PROXY_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("JSONRESPONSE_PROXY_VERIFY_FAILED")

        task_factory_verified_at_precondition = False
        if owned_loop is not None:
            try:
                if owned_loop.get_task_factory() is custom_task_factory:
                    task_factory_verified_at_precondition = True
                else:
                    runner_failures.append("TASK_FACTORY_PRECONDITION_VERIFY_FAILED")
            except BaseException:
                runner_failures.append("TASK_FACTORY_PRECONDITION_VERIFY_FAILED")

        all_preconditions_met = (
            owner_thread_id is not None
            and owned_loop is not None
            and orig_task_factory is not ORIG_FACTORY_UNSET
            and installed_factory
            and installed_factory_verified
            and installed_dt
            and installed_dt_verified
            and installed_secrets
            and installed_secrets_verified
            and installed_asyncio
            and installed_asyncio_verified
            and installed_json
            and installed_json_verified
            and dt_proxy is not None
            and secrets_proxy is not None
            and asyncio_proxy is not None
            and json_response_recorder is not None
            and task_factory_verified_at_precondition
        )

        if all_preconditions_met:
            # 1. Construction-failure control
            async def dummy_construct_fail_coro():
                await asyncio.sleep(3600.0)
            try:
                construct_fail_coro_obj = dummy_construct_fail_coro()
                construct_test_owned_task(construct_fail_coro_obj, owned_loop, task_constructor=injected_task_constructor)
                runner_failures.append("CONSTRUCTION_CONTROL_UNEXPECTED_SUCCESS")
            except InjectedConstructionControlException:
                pass
            except BaseException:
                runner_failures.append("CONSTRUCTION_CONTROL_UNEXPECTED_EXCEPTION")

            # 2. Explicit non-owning-constructor control
            async def dummy_non_owning_coro():
                await asyncio.sleep(3600.0)
            try:
                non_owning_control_coro_obj = dummy_non_owning_coro()
                res_non_owning = construct_test_owned_task(non_owning_control_coro_obj, owned_loop, task_constructor=explicit_non_owning_constructor)
                if res_non_owning is not non_owning_constructor_sentinel:
                    runner_failures.append("NON_OWNING_CONTROL_WRONG_RETURN")
            except BaseException:
                runner_failures.append("NON_OWNING_CONTROL_UNEXPECTED_EXCEPTION")

            # 3. Cancellation control
            async def dummy_cancel_source():
                await asyncio.sleep(3600.0)
            try:
                cancel_control_coro_obj = dummy_cancel_source()
                cancel_control_proxy = asyncio_proxy.Task(cancel_control_coro_obj, loop=owned_loop)
                if not isinstance(cancel_control_proxy, TrackedTaskProxy):
                    runner_failures.append("CANCEL_CONTROL_NOT_PROXY")
            except BaseException:
                runner_failures.append("CANCEL_CONTROL_SETUP_FAILED")

            # 4. Two fresh native-source surface witnesses (Clause 3A)
            async def dummy_create_task_source():
                await asyncio.sleep(3600.0)
            try:
                create_task_control_coro_obj = dummy_create_task_source()
                create_task_control_proxy = asyncio_proxy.create_task(create_task_control_coro_obj)
                if not isinstance(create_task_control_proxy, TrackedTaskProxy):
                    runner_failures.append("CREATE_TASK_CONTROL_NOT_PROXY")
            except BaseException:
                runner_failures.append("CREATE_TASK_CONTROL_SETUP_FAILED")

            async def dummy_ensure_future_source():
                await asyncio.sleep(3600.0)
            try:
                ensure_future_control_coro_obj = dummy_ensure_future_source()
                ensure_future_control_proxy = asyncio_proxy.ensure_future(ensure_future_control_coro_obj, loop=owned_loop)
                if not isinstance(ensure_future_control_proxy, TrackedTaskProxy):
                    runner_failures.append("ENSURE_FUTURE_CONTROL_NOT_PROXY")
            except BaseException:
                runner_failures.append("ENSURE_FUTURE_CONTROL_SETUP_FAILED")

            # 5. Hostile surface probes (Clause 3B)
            # 5a. Re-pass probe
            if cancel_control_proxy is not None:
                pre_intercepted_count = len(intercepted_task_records)
                pre_source_close_counts = (
                    len(interception_close_attempts),
                    len(interception_close_successes),
                    len(positional_close_attempts),
                    len(positional_close_successes),
                    len(other_rejection_close_attempts),
                    len(other_rejection_close_successes),
                    len(rescue_close_attempts),
                    len(rescue_close_successes),
                )
                pre_proxies_count = len(classified_task_proxies)
                pre_control_proxies_count = len(control_proxy_tracker["proxies"])
                pre_known_tasks_count = len(all_known_tasks)
                pre_owned_coros_count = len(test_owned_coroutines)

                try:
                    repass1 = asyncio_proxy.ensure_future(cancel_control_proxy, loop=owned_loop)
                    ensure_future_repass_proxy_returns.append(repass1)
                except BaseException:
                    runner_failures.append("ENSURE_FUTURE_REPASS_PROXY_FAILED")
                try:
                    repass2 = asyncio_proxy.ensure_future(cancel_control_proxy._task, loop=owned_loop)
                    ensure_future_repass_task_returns.append(repass2)
                except BaseException:
                    runner_failures.append("ENSURE_FUTURE_REPASS_TASK_FAILED")

                post_intercepted_count = len(intercepted_task_records)
                post_source_close_counts = (
                    len(interception_close_attempts),
                    len(interception_close_successes),
                    len(positional_close_attempts),
                    len(positional_close_successes),
                    len(other_rejection_close_attempts),
                    len(other_rejection_close_successes),
                    len(rescue_close_attempts),
                    len(rescue_close_successes),
                )
                post_proxies_count = len(classified_task_proxies)
                post_control_proxies_count = len(control_proxy_tracker["proxies"])
                post_known_tasks_count = len(all_known_tasks)
                post_owned_coros_count = len(test_owned_coroutines)

                if (pre_intercepted_count, pre_source_close_counts, pre_proxies_count, pre_control_proxies_count, pre_known_tasks_count, pre_owned_coros_count) != (post_intercepted_count, post_source_close_counts, post_proxies_count, post_control_proxies_count, post_known_tasks_count, post_owned_coros_count):
                    runner_failures.append("ENSURE_FUTURE_REPASS_LEDGER_MUTATION")

            # 5b. Unclassified Future probe
            try:
                unclassified_future_obj = owned_loop.create_future()
                asyncio_proxy.ensure_future(unclassified_future_obj, loop=owned_loop)
                runner_failures.append("UNCLASSIFIED_FUTURE_NOT_REJECTED")
            except ExpectedUnclassifiedFutureRejection as exc:
                if exc is expected_unclassified_future_rejection_instance:
                    expected_unclassified_future_rejections.append(exc)
                else:
                    runner_failures.append("UNCLASSIFIED_FUTURE_WRONG_EXCEPTION_INSTANCE")
            except BaseException:
                runner_failures.append("UNCLASSIFIED_FUTURE_UNEXPECTED_EXCEPTION")

            # 5c. create_task resolver-miss probe
            async def dummy_resolver_miss_source():
                await asyncio.sleep(3600.0)
            try:
                resolver_miss_source_coro = dummy_resolver_miss_source()
                asyncio_proxy.create_task(resolver_miss_source_coro)
                runner_failures.append("RESOLVER_MISS_NOT_REJECTED")
            except ExpectedResolverMissRejection as exc:
                if exc is expected_resolver_miss_rejection_instance:
                    expected_resolver_miss_rejections.append(exc)
                else:
                    runner_failures.append("RESOLVER_MISS_WRONG_EXCEPTION_INSTANCE")
            except BaseException:
                runner_failures.append("RESOLVER_MISS_UNEXPECTED_EXCEPTION")

            # 5d. Task positional-rejection probe
            async def dummy_positional_source():
                await asyncio.sleep(3600.0)
            try:
                positional_probe_source_coro = dummy_positional_source()
                asyncio_proxy.Task(positional_probe_source_coro, positional_probe_sentinel, loop=owned_loop)
                runner_failures.append("POSITIONAL_PROBE_NOT_REJECTED")
            except ExpectedPositionalRejection as exc:
                if exc is expected_positional_rejection_instance:
                    expected_positional_rejections.append(exc)
                else:
                    runner_failures.append("POSITIONAL_PROBE_WRONG_EXCEPTION_INSTANCE")
            except BaseException:
                runner_failures.append("POSITIONAL_PROBE_UNEXPECTED_EXCEPTION")

            # 6. Six live other-rejection probes
            async def dummy_task_unsupported_args():
                await asyncio.sleep(3600.0)
            try:
                task_unsupported_args_source_coro = dummy_task_unsupported_args()
                asyncio_proxy.Task(task_unsupported_args_source_coro, other_rejection_positional_sentinel, loop=owned_loop)
                runner_failures.append("TASK_UNSUPPORTED_ARGS_NOT_REJECTED")
            except ExpectedTaskUnsupportedArgsException as exc:
                if exc is expected_task_unsupported_args_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("TASK_UNSUPPORTED_ARGS_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("TASK_UNSUPPORTED_ARGS_UNEXPECTED_EXCEPTION")

            async def dummy_task_wrong_loop():
                await asyncio.sleep(3600.0)
            try:
                task_wrong_loop_source_coro = dummy_task_wrong_loop()
                asyncio_proxy.Task(task_wrong_loop_source_coro, loop=other_rejection_wrong_loop_sentinel)
                runner_failures.append("TASK_WRONG_LOOP_NOT_REJECTED")
            except ExpectedTaskWrongLoopException as exc:
                if exc is expected_task_wrong_loop_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("TASK_WRONG_LOOP_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("TASK_WRONG_LOOP_UNEXPECTED_EXCEPTION")

            async def dummy_create_task_unsupported_args():
                await asyncio.sleep(3600.0)
            try:
                create_task_unsupported_args_source_coro = dummy_create_task_unsupported_args()
                asyncio_proxy.create_task(create_task_unsupported_args_source_coro, other_rejection_positional_sentinel)
                runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_NOT_REJECTED")
            except ExpectedCreateTaskUnsupportedArgsException as exc:
                if exc is expected_create_task_unsupported_args_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("CREATE_TASK_UNSUPPORTED_ARGS_UNEXPECTED_EXCEPTION")

            async def dummy_create_task_wrong_loop():
                await asyncio.sleep(3600.0)
            try:
                create_task_wrong_loop_source_coro = dummy_create_task_wrong_loop()
                asyncio_proxy.create_task(create_task_wrong_loop_source_coro, loop=other_rejection_wrong_loop_sentinel)
                runner_failures.append("CREATE_TASK_WRONG_LOOP_NOT_REJECTED")
            except ExpectedCreateTaskWrongLoopException as exc:
                if exc is expected_create_task_wrong_loop_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("CREATE_TASK_WRONG_LOOP_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("CREATE_TASK_WRONG_LOOP_UNEXPECTED_EXCEPTION")

            async def dummy_ensure_future_unsupported_args():
                await asyncio.sleep(3600.0)
            try:
                ensure_future_unsupported_args_source_coro = dummy_ensure_future_unsupported_args()
                asyncio_proxy.ensure_future(ensure_future_unsupported_args_source_coro, other_rejection_positional_sentinel, loop=owned_loop)
                runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_NOT_REJECTED")
            except ExpectedEnsureFutureUnsupportedArgsException as exc:
                if exc is expected_ensure_future_unsupported_args_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("ENSURE_FUTURE_UNSUPPORTED_ARGS_UNEXPECTED_EXCEPTION")

            async def dummy_ensure_future_wrong_loop():
                await asyncio.sleep(3600.0)
            try:
                ensure_future_wrong_loop_source_coro = dummy_ensure_future_wrong_loop()
                asyncio_proxy.ensure_future(ensure_future_wrong_loop_source_coro, loop=other_rejection_wrong_loop_sentinel)
                runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_NOT_REJECTED")
            except ExpectedEnsureFutureWrongLoopException as exc:
                if exc is expected_ensure_future_wrong_loop_exc:
                    recorded_other_rejection_exceptions.append(exc)
                else:
                    runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_WRONG_EXCEPTION")
            except BaseException:
                runner_failures.append("ENSURE_FUTURE_WRONG_LOOP_UNEXPECTED_EXCEPTION")

            # 7. Late-window control source initialization
            async def dummy_late_source():
                await asyncio.sleep(3600.0)
            late_control_coro_obj = dummy_late_source()

            # 8. Production direct invocations
            async def _exercise_direct_fallback():
                nonlocal current_phase
                try:
                    # 1. Direct Browser WS fallback close (has_denial=False)
                    current_phase = "browser"
                    ws_browser = InstrumentedWebSocket(
                        path="/ws/s1",
                        headers={"sec-websocket-protocol": f"tars-ticket,{ticket}"},
                        query_params={"last_seq": "7"},
                        ready_val=unready_val,
                        tracker=effects,
                        gate_key="browser_gate_read_count",
                        has_denial=False,
                        scripted_messages=[{"type": "ping"}, WebSocketDisconnect(1000)],
                        phase="browser",
                    )
                    await main.websocket_endpoint(ws_browser, "s1")
                    assert ws_browser.closed == [{"code": 1008}]
                    assert hasattr(ws_browser, "send_denial_response") is False
                    assert main.session_mgr is fake_http_sm

                    # 2. Direct Native WS fallback close (has_denial=False)
                    current_phase = "native"
                    header_bytes = json.dumps(
                        {"session_id": "s1", "source": "microphone", "sequence": 1}
                    ).encode("utf-8")
                    raw_frame = struct.pack(">I", len(header_bytes)) + header_bytes + b"\x01\x02\x03\x04"

                    monkeypatch.setattr(main, "session_mgr", fake_native_sm_inst)
                    assert main.session_mgr is fake_native_sm_inst

                    ws_native = InstrumentedWebSocket(
                        path="/api/stream/native/s1",
                        headers={"sec-websocket-protocol": "tars-stream,test-stream-key"},
                        ready_val=unready_val,
                        tracker=effects,
                        gate_key="native_gate_read_count",
                        has_denial=False,
                        scripted_messages=[{"bytes": raw_frame}, {"type": "websocket.disconnect"}],
                        phase="native",
                    )
                    await main.native_stream_endpoint(ws_native, "s1")
                    assert ws_native.closed == [{"code": 1008}]
                    assert hasattr(ws_native, "send_denial_response") is False
                finally:
                    current_phase = "cleanup"

            try:
                runner_coro_obj = _exercise_direct_fallback()
                runner_task = construct_test_owned_task(runner_coro_obj, owned_loop, name="fallback_runner")
            except BaseException:
                runner_failures.append("RUNNER_TASK_CONSTRUCT_FAILED")

            if runner_task is not None:
                try:
                    def stop_on_done(fut):
                        try:
                            owned_loop.stop()
                        except BaseException:
                            runner_failures.append("RUNNER_DONE_CALLBACK_STOP_FAILED")
                    runner_task.add_done_callback(stop_on_done)
                except BaseException:
                    runner_failures.append("RUNNER_DONE_CALLBACK_REGISTRATION_FAILED")

                drive_ok = safe_bounded_drive(owned_loop, 5.0, "RUNNER_TIMER_HANDLE_FAILED", "RUNNER_RUN_FOREVER_FAILED", "RUNNER_TIMER_CANCEL_FAILED")
                if not drive_ok:
                    runner_failures.append("RUNNER_DRIVE_FAILED")

                is_runner_done = False
                try:
                    is_runner_done = runner_task.done()
                except BaseException:
                    runner_failures.append("RUNNER_DONE_CHECK_FAILED")

                if not is_runner_done:
                    runner_timed_out = True
                    runner_failures.append("RUNNER_TASK_TIMEOUT")
    finally:
        try:
            # Phase 1: Structurally unconditional disposal inside inner try
            cleanup_factory_ok = False
            cleanup_asyncio_ok = False
            cleanup_datetime_ok = False
            cleanup_secrets_ok = False
            cleanup_json_ok = False

            owned_loop_is_open = False
            if owned_loop is not None:
                try:
                    owned_loop_is_open = not owned_loop.is_closed()
                except BaseException:
                    runner_failures.append("CLEANUP_LOOP_IS_CLOSED_CHECK_FAILED")

            if unclassified_future_obj is not None:
                try:
                    res = unclassified_future_obj.cancel()
                    if res is True:
                        unclassified_future_cancels.append(True)
                    else:
                        runner_failures.append("UNCLASSIFIED_FUTURE_CANCEL_NOT_TRUE")
                except BaseException:
                    runner_failures.append("UNCLASSIFIED_FUTURE_CANCEL_FAILED")

                try:
                    unclassified_future_obj.result()
                    runner_failures.append("UNCLASSIFIED_FUTURE_RESULT_DID_NOT_RAISE")
                except real_asyncio.CancelledError as exc:
                    unclassified_future_caught_cancellations.append(exc)
                    unclassified_future_retrievals.append(unclassified_future_obj)
                except BaseException:
                    runner_failures.append("UNCLASSIFIED_FUTURE_RESULT_WRONG_EXCEPTION")

            if owned_loop_is_open:
                try:
                    if owned_loop.get_task_factory() is not custom_task_factory:
                        runner_failures.append("CLEANUP_FACTORY_SEAM_LOST")
                        if installed_factory:
                            owned_loop.set_task_factory(custom_task_factory)
                    cleanup_factory_ok = (installed_factory and installed_factory_verified and (owned_loop.get_task_factory() is custom_task_factory))
                except BaseException:
                    runner_failures.append("CLEANUP_FACTORY_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "asyncio", direct_sentinel) is not asyncio_proxy or asyncio_proxy is None:
                        runner_failures.append("CLEANUP_ASYNCIO_SEAM_LOST")
                        if installed_asyncio and asyncio_proxy is not None:
                            main.asyncio = asyncio_proxy
                    cleanup_asyncio_ok = (installed_asyncio and installed_asyncio_verified and asyncio_proxy is not None and (getattr(main, "asyncio", direct_sentinel) is asyncio_proxy))
                except BaseException:
                    runner_failures.append("CLEANUP_ASYNCIO_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "datetime", direct_sentinel) is not dt_proxy or dt_proxy is None:
                        runner_failures.append("CLEANUP_DATETIME_SEAM_LOST")
                        if installed_dt and dt_proxy is not None:
                            main.datetime = dt_proxy
                    cleanup_datetime_ok = (installed_dt and installed_dt_verified and dt_proxy is not None and (getattr(main, "datetime", direct_sentinel) is dt_proxy))
                except BaseException:
                    runner_failures.append("CLEANUP_DATETIME_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "secrets", direct_sentinel) is not secrets_proxy or secrets_proxy is None:
                        runner_failures.append("CLEANUP_SECRETS_SEAM_LOST")
                        if installed_secrets and secrets_proxy is not None:
                            main.secrets = secrets_proxy
                    cleanup_secrets_ok = (installed_secrets and installed_secrets_verified and secrets_proxy is not None and (getattr(main, "secrets", direct_sentinel) is secrets_proxy))
                except BaseException:
                    runner_failures.append("CLEANUP_SECRETS_SEAM_CHECK_FAILED")

                try:
                    if getattr(main, "JSONResponse", direct_sentinel) is not json_response_recorder or json_response_recorder is None:
                        runner_failures.append("CLEANUP_JSON_SEAM_LOST")
                        if installed_json and json_response_recorder is not None:
                            main.JSONResponse = json_response_recorder
                    cleanup_json_ok = (installed_json and installed_json_verified and json_response_recorder is not None and (getattr(main, "JSONResponse", direct_sentinel) is json_response_recorder))
                except BaseException:
                    runner_failures.append("CLEANUP_JSON_SEAM_CHECK_FAILED")

                all_cleanup_seams_ok = (
                    cleanup_factory_ok
                    and cleanup_asyncio_ok
                    and cleanup_datetime_ok
                    and cleanup_secrets_ok
                    and cleanup_json_ok
                )
                if not all_cleanup_seams_ok:
                    runner_failures.append("CLEANUP_DRIVE_UNSAFE_SEAMS")

                # Unified cancellation, settlement, and scans (Repair L6, L7)
                if runner_timed_out and runner_task is not None:
                    try:
                        is_runner_done = False
                        try:
                            is_runner_done = runner_task.done()
                        except BaseException:
                            runner_failures.append("TIMEOUT_RUNNER_DONE_CHECK_FAILED")
                        if not is_runner_done:
                            harness_cancel_once(runner_task)
                            drive_ok = safe_bounded_drive(owned_loop, 1.0, "TIMEOUT_TIMER_HANDLE_FAILED", "TIMEOUT_RUN_FOREVER_FAILED", "TIMEOUT_TIMER_CANCEL_FAILED")
                            if not drive_ok:
                                runner_failures.append("TIMEOUT_DRIVE_FAILED")
                    except BaseException:
                        runner_failures.append("TIMEOUT_SETTLEMENT_FAILED")

                pending_init = []
                try:
                    for t in all_known_tasks:
                        try:
                            if not t.done():
                                pending_init.append(t)
                        except BaseException:
                            runner_failures.append("INITIAL_TASK_DONE_CHECK_FAILED")
                except BaseException:
                    runner_failures.append("INITIAL_PENDING_INVENTORY_FAILED")

                for t in pending_init:
                    try:
                        harness_cancel_once(t)
                    except BaseException:
                        runner_failures.append("INITIAL_TASK_CANCEL_FAILED")

                drive_ok = safe_bounded_drive(owned_loop, 0.5, "CANCEL_PROGRESS_TIMER_HANDLE_FAILED", "CANCEL_PROGRESS_RUN_FOREVER_FAILED", "CANCEL_PROGRESS_TIMER_CANCEL_FAILED")
                if not drive_ok:
                    runner_failures.append("CANCEL_PROGRESS_DRIVE_FAILED")

                for cp_idx in range(3):
                    try:
                        current_all = list(real_asyncio.all_tasks(owned_loop))
                        for t in current_all:
                            if not any(k is t for k in all_known_tasks):
                                if not any(d is t for d in cleanup_discovered_tasks):
                                    cleanup_discovered_tasks.append(t)
                                runner_failures.append("CHECKPOINT_UNRETAINED_TASK")

                        all_active_tasks = list(all_known_tasks) + [d for d in cleanup_discovered_tasks if not any(k is d for k in all_known_tasks)]
                        pending_to_cancel = []
                        for t in all_active_tasks:
                            try:
                                if not t.done():
                                    pending_to_cancel.append(t)
                            except BaseException:
                                runner_failures.append("CHECKPOINT_TASK_DONE_CHECK_FAILED")

                        for t in pending_to_cancel:
                            try:
                                harness_cancel_once(t)
                            except BaseException:
                                runner_failures.append("CHECKPOINT_TASK_CANCEL_FAILED")

                        pending_now = []
                        for t in all_active_tasks:
                            try:
                                if not t.done():
                                    pending_now.append(t)
                            except BaseException:
                                runner_failures.append("CHECKPOINT_TASK_DONE_CHECK_FAILED")

                        if not pending_now:
                            break
                        drive_ok = safe_bounded_drive(owned_loop, 0.5, "CHECKPOINT_TIMER_HANDLE_FAILED", "CHECKPOINT_RUN_FOREVER_FAILED", "CHECKPOINT_TIMER_CANCEL_FAILED")
                        if not drive_ok:
                            runner_failures.append("CHECKPOINT_DRIVE_FAILED")
                    except BaseException:
                        runner_failures.append("CHECKPOINT_INVENTORY_FAILED")

                if late_control_coro_obj is not None and all_cleanup_seams_ok:
                    try:
                        def schedule_late_control_cb():
                            nonlocal late_control_proxy
                            try:
                                late_control_proxy = asyncio_proxy.Task(late_control_coro_obj, loop=owned_loop)
                            except BaseException:
                                runner_failures.append("LATE_CONTROL_TASK_FAILED")

                        owned_loop.call_soon(schedule_late_control_cb)
                        late_control_scheduled = True
                    except BaseException:
                        runner_failures.append("LATE_CONTROL_SCHEDULE_FAILED")

                    drive_ok = safe_bounded_drive(owned_loop, 0.1, "LATE_WINDOW_TIMER_HANDLE_FAILED", "LATE_WINDOW_RUN_FOREVER_FAILED", "LATE_WINDOW_TIMER_CANCEL_FAILED")
                    if not drive_ok:
                        runner_failures.append("LATE_WINDOW_DRIVE_FAILED")

                    if not late_control_scheduled or late_control_proxy is None:
                        runner_failures.append("LATE_CONTROL_NOT_SCHEDULED")

                final_inventory_authorized_tasks_cutoff.extend(all_known_tasks)

                # Pass 1
                try:
                    tasks_pass1 = list(real_asyncio.all_tasks(owned_loop))
                    final_inventory_snapshots.append(tasks_pass1)
                    final_inventory_pass_tags.append("PASS_1_INITIAL")
                    for t in tasks_pass1:
                        if not any(c is t for c in final_inventory_authorized_tasks_cutoff):
                            runner_failures.append("FINAL_SCAN_FOUND_UNRETAINED_TASK")
                            if not any(d is t for d in cleanup_discovered_tasks):
                                cleanup_discovered_tasks.append(t)
                        try:
                            if not t.done():
                                harness_cancel_once(t)
                        except BaseException:
                            runner_failures.append("FINAL_PASS_1_CANCEL_FAILED")
                    drive_ok = safe_bounded_drive(owned_loop, 0.1, "FINAL_SETTLE_TIMER_HANDLE_FAILED", "FINAL_SETTLE_RUN_FOREVER_FAILED", "FINAL_SETTLE_TIMER_CANCEL_FAILED")
                    if not drive_ok:
                        runner_failures.append("FINAL_SETTLE_DRIVE_FAILED")
                except BaseException:
                    runner_failures.append("FINAL_INVENTORY_PASS_1_FAILED")

                # Pass 2
                try:
                    tasks_pass2 = list(real_asyncio.all_tasks(owned_loop))
                    final_inventory_snapshots.append(tasks_pass2)
                    final_inventory_pass_tags.append("PASS_2_POST_SETTLE")
                    for t in tasks_pass2:
                        if not any(c is t for c in final_inventory_authorized_tasks_cutoff):
                            runner_failures.append("FINAL_SCAN_FOUND_UNRETAINED_TASK")
                            if not any(d is t for d in cleanup_discovered_tasks):
                                cleanup_discovered_tasks.append(t)
                        try:
                            if not t.done():
                                harness_cancel_once(t)
                        except BaseException:
                            runner_failures.append("FINAL_PASS_2_CANCEL_FAILED")
                    needs_second_settle = False
                    for t in tasks_pass2:
                        try:
                            if not t.done():
                                needs_second_settle = True
                                break
                        except BaseException:
                            runner_failures.append("FINAL_PASS_2_DONE_CHECK_FAILED")
                    if needs_second_settle:
                        drive_ok = safe_bounded_drive(owned_loop, 0.1, "SECOND_SETTLE_TIMER_HANDLE_FAILED", "SECOND_SETTLE_RUN_FOREVER_FAILED", "SECOND_SETTLE_TIMER_CANCEL_FAILED")
                        if not drive_ok:
                            runner_failures.append("SECOND_SETTLE_DRIVE_FAILED")
                except BaseException:
                    runner_failures.append("FINAL_INVENTORY_PASS_2_FAILED")

                # Pass 3
                try:
                    tasks_pass3 = list(real_asyncio.all_tasks(owned_loop))
                    final_inventory_snapshots.append(tasks_pass3)
                    final_inventory_pass_tags.append("PASS_3_FINAL_VERIFY")
                    for t in tasks_pass3:
                        if not any(c is t for c in final_inventory_authorized_tasks_cutoff):
                            runner_failures.append("FINAL_SCAN_FOUND_UNRETAINED_TASK")
                            if not any(d is t for d in cleanup_discovered_tasks):
                                cleanup_discovered_tasks.append(t)
                        try:
                            if not t.done():
                                harness_cancel_once(t)
                                runner_failures.append("LOOP_TASKS_STILL_PENDING")
                        except BaseException:
                            runner_failures.append("FINAL_PASS_3_CANCEL_FAILED")
                    needs_third_settle = False
                    for t in tasks_pass3:
                        try:
                            if not t.done():
                                needs_third_settle = True
                                break
                        except BaseException:
                            runner_failures.append("FINAL_PASS_3_DONE_CHECK_FAILED")
                    if needs_third_settle:
                        drive_ok = safe_bounded_drive(owned_loop, 0.1, "THIRD_SETTLE_TIMER_HANDLE_FAILED", "THIRD_SETTLE_RUN_FOREVER_FAILED", "THIRD_SETTLE_TIMER_CANCEL_FAILED")
                        if not drive_ok:
                            runner_failures.append("THIRD_SETTLE_DRIVE_FAILED")
                except BaseException:
                    runner_failures.append("FINAL_INVENTORY_PASS_3_FAILED")

                # Terminal outcome retrieval for all known & discovered tasks
                all_tasks_for_retrieval = list(all_known_tasks) + [d for d in cleanup_discovered_tasks if not any(k is d for k in all_known_tasks)]
                for t in all_tasks_for_retrieval:
                    try:
                        if t.done():
                            retrieve_terminal_outcome(t)
                        else:
                            runner_failures.append("TASK_NOT_DONE_BEFORE_RETRIEVAL")
                    except BaseException:
                        runner_failures.append("TERMINAL_RETRIEVAL_SCAN_FAILED")

            # Unified cleanup source inventory & sweep (Repair L5)
            cleanup_source_candidates = [
                tracked_expiry_coro_obj,
                cancel_control_coro_obj,
                late_control_coro_obj,
                create_task_control_coro_obj,
                ensure_future_control_coro_obj,
                resolver_miss_source_coro,
                positional_probe_source_coro,
                task_unsupported_args_source_coro,
                task_wrong_loop_source_coro,
                create_task_unsupported_args_source_coro,
                create_task_wrong_loop_source_coro,
                ensure_future_unsupported_args_source_coro,
                ensure_future_wrong_loop_source_coro,
            ]
            for src in (
                interception_close_attempts
                + interception_close_successes
                + positional_close_attempts
                + positional_close_successes
                + other_rejection_close_attempts
                + other_rejection_close_successes
                + rescue_close_attempts
                + rescue_close_successes
                + [r.source_coro for r in intercepted_task_records if r.source_coro is not None]
            ):
                cleanup_source_candidates.append(src)

            cleanup_source_inventory = []
            for src in cleanup_source_candidates:
                if src is not None and not any(s is src for s in cleanup_source_inventory):
                    cleanup_source_inventory.append(src)

            for src in cleanup_source_inventory:
                is_proven_closed = any(
                    s is src for s in (
                        interception_close_successes
                        + positional_close_successes
                        + other_rejection_close_successes
                        + rescue_close_successes
                    )
                )
                if not is_proven_closed:
                    state_read_ok = False
                    is_open = False
                    try:
                        frame = src.cr_frame
                        running = src.cr_running
                        state_read_ok = True
                        if frame is not None or running is not False:
                            is_open = True
                    except BaseException:
                        runner_failures.append("CLEANUP_SOURCE_STATE_VERIFY_FAILED")

                    if state_read_ok and not is_open:
                        runner_failures.append("CLEANUP_SOURCE_MISSING_CLOSE_PROOF")
                    elif state_read_ok and is_open:
                        ok = close_source_rescue(src)
                        if not ok:
                            runner_failures.append("CLEANUP_SOURCE_RESCUE_PROOF_FAILED")
        finally:
            # Phase 2: Capture pre-restore identities
            direct_factory_ok = False
            direct_asyncio_ok = False
            direct_datetime_ok = False
            direct_secrets_ok = False
            direct_json_ok = False

            if owned_loop is not None:
                try:
                    direct_factory_ok = (owned_loop.get_task_factory() is custom_task_factory)
                except BaseException:
                    runner_failures.append("PRE_RESTORE_FACTORY_CHECK_FAILED")

            try:
                direct_asyncio_ok = (getattr(main, "asyncio", direct_sentinel) is asyncio_proxy)
            except BaseException:
                runner_failures.append("PRE_RESTORE_ASYNCIO_CHECK_FAILED")
            try:
                direct_datetime_ok = (getattr(main, "datetime", direct_sentinel) is dt_proxy)
            except BaseException:
                runner_failures.append("PRE_RESTORE_DATETIME_CHECK_FAILED")
            try:
                direct_secrets_ok = (getattr(main, "secrets", direct_sentinel) is secrets_proxy)
            except BaseException:
                runner_failures.append("PRE_RESTORE_SECRETS_CHECK_FAILED")
            try:
                direct_json_ok = (getattr(main, "JSONResponse", direct_sentinel) is json_response_recorder)
            except BaseException:
                runner_failures.append("PRE_RESTORE_JSON_CHECK_FAILED")

            # Phase 3: Five independent baseline restores + task factory restore + loop close
            try:
                main.datetime = real_datetime
                restored_dt = True
            except BaseException:
                runner_failures.append("DATETIME_BASELINE_RESTORE_FAILED")
            try:
                main.secrets = real_secrets
                restored_secrets = True
            except BaseException:
                runner_failures.append("SECRETS_BASELINE_RESTORE_FAILED")
            try:
                main.asyncio = real_asyncio
                restored_asyncio = True
            except BaseException:
                runner_failures.append("ASYNCIO_BASELINE_RESTORE_FAILED")
            try:
                main.JSONResponse = real_json_response
                restored_json = True
            except BaseException:
                runner_failures.append("JSONRESPONSE_BASELINE_RESTORE_FAILED")

            if owned_loop is not None:
                if orig_task_factory is not ORIG_FACTORY_UNSET:
                    try:
                        owned_loop.set_task_factory(orig_task_factory)
                        restored_factory = True
                    except BaseException:
                        runner_failures.append("TASK_FACTORY_RESTORE_FAILED")

                try:
                    owned_loop.close()
                    loop_closed_flag = owned_loop.is_closed()
                except BaseException:
                    runner_failures.append("LOOP_CLOSE_FAILED")

    try:
        if owned_loop is None or not loop_closed_flag:
            raise AssertionError("OWNED_LOOP_NOT_CLOSED")

        # Reverse and bidirectional assertions for 4 source-close ledgers (Repair L5 Item 6 / M5)
        non_rescue_attempts = [
            ("interception", interception_close_attempts),
            ("positional", positional_close_attempts),
            ("other", other_rejection_close_attempts),
        ]
        for tag1, l1 in non_rescue_attempts:
            for idx1, a1 in enumerate(l1):
                for idx2, a2 in enumerate(l1):
                    if idx1 != idx2 and a1 is a2:
                        raise AssertionError("DUPLICATE_IN_NON_RESCUE_ATTEMPT_LEDGER")
            for tag2, l2 in non_rescue_attempts:
                if tag1 != tag2:
                    for a1 in l1:
                        if any(a2 is a1 for a2 in l2):
                            raise AssertionError("NON_RESCUE_ATTEMPT_LEDGERS_NOT_DISJOINT")

        # 1. Every success maps by identity to exactly one attempt in its matching ledger
        for s in interception_close_successes:
            if sum(1 for a in interception_close_attempts if a is s) != 1:
                raise AssertionError("INTERCEPTION_SUCCESS_NOT_IN_ATTEMPTS")
            if sum(1 for r in intercepted_task_records if r.source_coro is s) != 1:
                raise AssertionError("INTERCEPTION_SUCCESS_NOT_IN_RECORDS")

        # 2 & 3. Each interception attempt has zero or one matching success; record count equals success count
        for a in interception_close_attempts:
            success_count = sum(1 for s in interception_close_successes if s is a)
            if success_count > 1:
                raise AssertionError("INTERCEPTION_ATTEMPT_MULTIPLE_SUCCESSES")
            if sum(1 for r in intercepted_task_records if r.source_coro is a) != success_count:
                raise AssertionError("INTERCEPTION_ATTEMPT_RECORD_COUNT_MISMATCH")

        # Positional successes and attempts
        for s in positional_close_successes:
            if sum(1 for a in positional_close_attempts if a is s) != 1:
                raise AssertionError("POSITIONAL_SUCCESS_NOT_IN_ATTEMPTS")
            if s is not positional_probe_source_coro:
                raise AssertionError("POSITIONAL_SUCCESS_WRONG_IDENTITY")
        for a in positional_close_attempts:
            if sum(1 for s in positional_close_successes if s is a) > 1:
                raise AssertionError("POSITIONAL_ATTEMPT_MULTIPLE_SUCCESSES")
            if a is not positional_probe_source_coro:
                raise AssertionError("POSITIONAL_ATTEMPT_WRONG_IDENTITY")

        # Other rejection successes and attempts
        expected_other_sources = (
            task_unsupported_args_source_coro,
            task_wrong_loop_source_coro,
            create_task_unsupported_args_source_coro,
            create_task_wrong_loop_source_coro,
            ensure_future_unsupported_args_source_coro,
            ensure_future_wrong_loop_source_coro,
        )
        for s in other_rejection_close_successes:
            if sum(1 for a in other_rejection_close_attempts if a is s) != 1:
                raise AssertionError("OTHER_REJECTION_SUCCESS_NOT_IN_ATTEMPTS")
            if not any(s is exp for exp in expected_other_sources):
                raise AssertionError("OTHER_REJECTION_SUCCESS_UNKNOWN_IDENTITY")
        for a in other_rejection_close_attempts:
            if sum(1 for s in other_rejection_close_successes if s is a) > 1:
                raise AssertionError("OTHER_REJECTION_ATTEMPT_MULTIPLE_SUCCESSES")
            if not any(a is exp for exp in expected_other_sources):
                raise AssertionError("OTHER_REJECTION_ATTEMPT_UNKNOWN_IDENTITY")

        all_non_rescue_successes = interception_close_successes + positional_close_successes + other_rejection_close_successes
        all_non_rescue_attempts = interception_close_attempts + positional_close_attempts + other_rejection_close_attempts

        for s in all_non_rescue_successes:
            if sum(1 for ra in rescue_close_attempts if ra is s) != 0:
                raise AssertionError("RESCUE_ATTEMPT_FOR_PROVEN_SUCCESSFUL_SOURCE")
            if sum(1 for rs in rescue_close_successes if rs is s) != 0:
                raise AssertionError("RESCUE_SUCCESS_FOR_PROVEN_SUCCESSFUL_SOURCE")

        for idx1, r1 in enumerate(rescue_close_attempts):
            for idx2, r2 in enumerate(rescue_close_attempts):
                if idx1 != idx2 and r1 is r2:
                    raise AssertionError("DUPLICATE_RESCUE_ATTEMPT")

            nr_attempt_count = sum(1 for a in all_non_rescue_attempts if a is r1)
            nr_success_count = sum(1 for s in all_non_rescue_successes if s is r1)
            if nr_attempt_count == 0:
                if nr_success_count != 0:
                    raise AssertionError("RESCUE_ATTEMPT_WITH_NON_RESCUE_SUCCESS")
            elif nr_attempt_count == 1:
                if nr_success_count != 0:
                    raise AssertionError("RESCUE_ATTEMPT_WITH_MATCHING_NON_RESCUE_SUCCESS")
            else:
                raise AssertionError("RESCUE_ATTEMPT_WITH_MULTIPLE_NON_RESCUE_ATTEMPTS")

        for idx1, s1 in enumerate(rescue_close_successes):
            for idx2, s2 in enumerate(rescue_close_successes):
                if idx1 != idx2 and s1 is s2:
                    raise AssertionError("DUPLICATE_RESCUE_SUCCESS")
            if sum(1 for a in rescue_close_attempts if a is s1) != 1:
                raise AssertionError("RESCUE_SUCCESS_WITHOUT_EXACTLY_ONE_ATTEMPT")

        if runner_failures:
            raise AssertionError("RUNNER_FAILURES_NOT_EMPTY")
        if not (installed_factory and installed_factory_verified and installed_dt and installed_dt_verified and installed_secrets and installed_secrets_verified and installed_asyncio and installed_asyncio_verified and installed_json and installed_json_verified):
            raise AssertionError("INSTALLATION_AUTHORITY_NOT_VERIFIED")
        if not (direct_factory_ok and direct_asyncio_ok and direct_datetime_ok and direct_secrets_ok and direct_json_ok):
            raise AssertionError("DIRECT_SEAMS_NOT_VERIFIED")
        if not (restored_asyncio and restored_dt and restored_secrets and restored_json and restored_factory):
            raise AssertionError("RESTORES_NOT_VERIFIED")
        if main.asyncio is not real_asyncio or main.datetime is not real_datetime or main.secrets is not real_secrets or main.JSONResponse is not real_json_response or owned_loop.get_task_factory() is not orig_task_factory:
            raise AssertionError("BASELINES_NOT_RESTORED")

        if len(rescue_close_attempts) != 0 or len(rescue_close_successes) != 0:
            raise AssertionError("RESCUE_CLOSE_LEDGER_NOT_EMPTY")

        # Intercepted records assertions
        for r in intercepted_task_records:
            if r.real_task.get_loop() is not owned_loop:
                raise AssertionError("INTERCEPTED_TASK_WRONG_LOOP")
            if r.source_coro is None or r.controlled_coro is None or r.proxy is None:
                raise AssertionError("INTERCEPTED_RECORD_INCOMPLETE")
            if sum(1 for s in interception_close_attempts if s is r.source_coro) != 1:
                raise AssertionError("RECORD_SOURCE_NOT_IN_INTERCEPTION_ATTEMPTS")
            if sum(1 for s in interception_close_successes if s is r.source_coro) != 1:
                raise AssertionError("RECORD_SOURCE_NOT_IN_INTERCEPTION_SUCCESSES")
            if sum(1 for s in positional_close_attempts if s is r.source_coro) != 0:
                raise AssertionError("RECORD_SOURCE_IN_POSITIONAL_ATTEMPTS")
            if sum(1 for s in other_rejection_close_attempts if s is r.source_coro) != 0:
                raise AssertionError("RECORD_SOURCE_IN_OTHER_REJECTION_ATTEMPTS")
            if sum(1 for s in rescue_close_attempts if s is r.source_coro) != 0:
                raise AssertionError("RECORD_SOURCE_IN_RESCUE_ATTEMPTS")
            if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is r.controlled_coro) != 0:
                raise AssertionError("CONTROLLED_CORO_IN_SOURCE_CLOSE")
            if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is runner_coro_obj) != 0:
                raise AssertionError("RUNNER_CORO_IN_SOURCE_CLOSE")
            if sum(1 for t in all_known_tasks if t is r.real_task) != 1:
                raise AssertionError("TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for t in test_owned_tasks if t is r.real_task) != 1:
                raise AssertionError("TASK_NOT_IN_TEST_OWNED")
            if sum(1 for c in test_owned_coroutines if c is r.controlled_coro) != 1:
                raise AssertionError("CONTROLLED_CORO_NOT_IN_TEST_OWNED")
            if r.origin_record.get("coro") is not r.source_coro:
                raise AssertionError("ORIGIN_RECORD_CORO_MISMATCH")

            if r.category == "browser_expiry":
                cat_calls = effects["browser_expiry_task_create_calls"]
            elif r.category == "native_stall":
                cat_calls = effects["native_stall_task_create_calls"]
            elif r.category == "unexpected":
                cat_calls = effects["unexpected_task_create_calls"]
            elif r.category == "cancel_control":
                cat_calls = cancel_control_origin_calls
            elif r.category == "late_control":
                cat_calls = late_control_origin_calls
            elif r.category == "create_task_control":
                cat_calls = create_task_control_origin_calls
            elif r.category == "ensure_future_control":
                cat_calls = ensure_future_control_origin_calls
            elif r.category == "create_task_resolver_miss_control":
                cat_calls = resolver_miss_origin_calls
            else:
                raise AssertionError("INVALID_INTERCEPTED_CATEGORY")

            if sum(1 for rec in cat_calls if rec is r.origin_record) != 1:
                raise AssertionError("ORIGIN_RECORD_NOT_IN_CATEGORY_LIST")
            if sum(1 for p in classified_task_proxies if p is r.proxy) != 1:
                raise AssertionError("PROXY_NOT_IN_CLASSIFIED_LIST")
            if r.category in ("cancel_control", "late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control"):
                if sum(1 for p in control_proxy_tracker["proxies"] if p is r.proxy) != 1:
                    raise AssertionError("PROXY_NOT_IN_CONTROL_TRACKER_PROXIES")
            if sum(1 for other in intercepted_task_records if other.proxy is r.proxy) != 1:
                raise AssertionError("PROXY_NOT_ONE_TO_ONE_WITH_RECORD")
            if r.proxy._task is not r.real_task or r.proxy._source_coro is not r.source_coro or r.proxy._controlled_coro is not r.controlled_coro:
                raise AssertionError("PROXY_FIELDS_MISMATCH")
            if hasattr(r.real_task, "get_coro") and r.real_task.get_coro() is not r.controlled_coro:
                raise AssertionError("REAL_TASK_CORO_MISMATCH")

        # Binding controls by identity, category, origin, task, proxy
        cancel_recs = [r for r in intercepted_task_records if r.category == "cancel_control"]
        if len(cancel_recs) != 1:
            raise AssertionError("CANCEL_CONTROL_RECORD_COUNT_MISMATCH")
        cancel_rec = cancel_recs[0]
        if (
            cancel_rec.source_coro is not cancel_control_coro_obj
            or cancel_rec.proxy is not cancel_control_proxy
            or cancel_rec.real_task is not cancel_control_proxy._task
            or len(cancel_control_origin_calls) != 1
            or cancel_rec.origin_record is not cancel_control_origin_calls[0]
        ):
            raise AssertionError("CANCEL_CONTROL_BINDING_MISMATCH")

        create_task_recs = [r for r in intercepted_task_records if r.category == "create_task_control"]
        if len(create_task_recs) != 1:
            raise AssertionError("CREATE_TASK_CONTROL_RECORD_COUNT_MISMATCH")
        create_task_rec = create_task_recs[0]
        if (
            create_task_rec.source_coro is not create_task_control_coro_obj
            or create_task_rec.proxy is not create_task_control_proxy
            or create_task_rec.real_task is not create_task_control_proxy._task
            or len(create_task_control_origin_calls) != 1
            or create_task_rec.origin_record is not create_task_control_origin_calls[0]
        ):
            raise AssertionError("CREATE_TASK_CONTROL_BINDING_MISMATCH")

        ensure_future_recs = [r for r in intercepted_task_records if r.category == "ensure_future_control"]
        if len(ensure_future_recs) != 1:
            raise AssertionError("ENSURE_FUTURE_CONTROL_RECORD_COUNT_MISMATCH")
        ensure_future_rec = ensure_future_recs[0]
        if (
            ensure_future_rec.source_coro is not ensure_future_control_coro_obj
            or ensure_future_rec.proxy is not ensure_future_control_proxy
            or ensure_future_rec.real_task is not ensure_future_control_proxy._task
            or len(ensure_future_control_origin_calls) != 1
            or ensure_future_rec.origin_record is not ensure_future_control_origin_calls[0]
        ):
            raise AssertionError("ENSURE_FUTURE_CONTROL_BINDING_MISMATCH")

        resolver_miss_recs = [r for r in intercepted_task_records if r.category == "create_task_resolver_miss_control"]
        if len(resolver_miss_recs) != 1:
            raise AssertionError("RESOLVER_MISS_RECORD_COUNT_MISMATCH")
        resolver_miss_rec = resolver_miss_recs[0]
        if (
            resolver_miss_rec.source_coro is not resolver_miss_source_coro
            or resolver_miss_rec is not resolver_miss_matched_records[0]
            or len(resolver_miss_origin_calls) != 1
            or resolver_miss_rec.origin_record is not resolver_miss_origin_calls[0]
        ):
            raise AssertionError("RESOLVER_MISS_BINDING_MISMATCH")

        late_recs = [r for r in intercepted_task_records if r.category == "late_control"]
        if len(late_recs) != 1:
            raise AssertionError("LATE_CONTROL_RECORD_COUNT_MISMATCH")
        late_rec = late_recs[0]
        if (
            late_rec.source_coro is not late_control_coro_obj
            or late_rec.proxy is not late_control_proxy
            or late_rec.real_task is not late_control_proxy._task
            or len(late_control_origin_calls) != 1
            or late_rec.origin_record is not late_control_origin_calls[0]
        ):
            raise AssertionError("LATE_CONTROL_BINDING_MISMATCH")

        # Pairwise distinct controls
        distinct_control_proxies = [cancel_control_proxy, create_task_control_proxy, ensure_future_control_proxy, resolver_miss_rec.proxy, late_control_proxy]
        distinct_control_tasks = [cancel_control_proxy._task, create_task_control_proxy._task, ensure_future_control_proxy._task, resolver_miss_rec.real_task, late_control_proxy._task]
        distinct_control_coros = [cancel_control_coro_obj, create_task_control_coro_obj, ensure_future_control_coro_obj, resolver_miss_source_coro, late_control_coro_obj]
        distinct_control_recs = [cancel_rec, create_task_rec, ensure_future_rec, resolver_miss_rec, late_rec]
        distinct_control_origins = [cancel_control_origin_calls[0], create_task_control_origin_calls[0], ensure_future_control_origin_calls[0], resolver_miss_origin_calls[0], late_control_origin_calls[0]]

        for idx1 in range(5):
            for idx2 in range(idx1 + 1, 5):
                if distinct_control_proxies[idx1] is distinct_control_proxies[idx2]:
                    raise AssertionError("CONTROL_PROXIES_NOT_PAIRWISE_DISTINCT")
                if distinct_control_tasks[idx1] is distinct_control_tasks[idx2]:
                    raise AssertionError("CONTROL_TASKS_NOT_PAIRWISE_DISTINCT")
                if distinct_control_coros[idx1] is distinct_control_coros[idx2]:
                    raise AssertionError("CONTROL_COROS_NOT_PAIRWISE_DISTINCT")
                if distinct_control_recs[idx1] is distinct_control_recs[idx2]:
                    raise AssertionError("CONTROL_RECORDS_NOT_PAIRWISE_DISTINCT")
                if distinct_control_origins[idx1] is distinct_control_origins[idx2]:
                    raise AssertionError("CONTROL_ORIGINS_NOT_PAIRWISE_DISTINCT")

        # Bidirectional proxy mappings
        for p in classified_task_proxies:
            if sum(1 for r in intercepted_task_records if r.proxy is p) != 1:
                raise AssertionError("CLASSIFIED_PROXY_NOT_IN_RECORDS")
        for p in control_proxy_tracker["proxies"]:
            if sum(1 for r in intercepted_task_records if r.proxy is p and r.category in ("cancel_control", "late_control", "create_task_control", "ensure_future_control", "create_task_resolver_miss_control")) != 1:
                raise AssertionError("CONTROL_TRACKER_PROXY_NOT_IN_RECORDS")

        # Bidirectional category origin mappings
        all_origin_lists = [
            effects["browser_expiry_task_create_calls"],
            effects["native_stall_task_create_calls"],
            effects["unexpected_task_create_calls"],
            cancel_control_origin_calls,
            late_control_origin_calls,
            create_task_control_origin_calls,
            ensure_future_control_origin_calls,
            resolver_miss_origin_calls,
        ]
        for olist in all_origin_lists:
            for orec in olist:
                if sum(1 for r in intercepted_task_records if r.origin_record is orec) != 1:
                    raise AssertionError("ORIGIN_RECORD_NOT_IN_INTERCEPTED_RECORDS")

        # Forward & reverse task ledgers
        for t in test_owned_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("TEST_OWNED_TASK_NOT_IN_ALL_KNOWN")
        for t in all_known_tasks:
            if sum(1 for k in test_owned_tasks if k is t) != 1:
                raise AssertionError("ALL_KNOWN_TASK_NOT_IN_TEST_OWNED")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("TASK_OUTCOME_RETRIEVAL_MISMATCH")
            if not t.done():
                raise AssertionError("TASK_NOT_DONE")
            if hasattr(t, "get_coro") and t.get_coro() is not None:
                if sum(1 for c in test_owned_coroutines if c is t.get_coro()) != 1:
                    raise AssertionError("TASK_CORO_NOT_IN_TEST_OWNED_COROS")

        for o in outcome_retrieved_tasks:
            if sum(1 for k in all_known_tasks if k is o) != 1:
                raise AssertionError("RETRIEVED_TASK_NOT_IN_ALL_KNOWN")

        for t in all_known_tasks:
            in_normal = sum(1 for n in normal_terminal_tasks if n is t)
            in_cancelled = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exception = sum(1 for e in exception_terminal_tasks if e is t)
            if in_normal + in_cancelled + in_exception != 1:
                raise AssertionError("TASK_TERMINAL_CLASSIFICATION_NOT_EXACT_ONE")

        # Reverse terminal ledger assertions
        for t in normal_terminal_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("NORMAL_TERMINAL_TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("NORMAL_TERMINAL_TASK_NOT_IN_OUTCOME_RETRIEVED")
            in_norm = sum(1 for n in normal_terminal_tasks if n is t)
            in_canc = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exc = sum(1 for e in exception_terminal_tasks if e is t)
            if in_norm != 1 or in_canc != 0 or in_exc != 0:
                raise AssertionError("NORMAL_TERMINAL_TASK_MEMBERSHIP_INVALID")

        for t in cancelled_terminal_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("CANCELLED_TERMINAL_TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("CANCELLED_TERMINAL_TASK_NOT_IN_OUTCOME_RETRIEVED")
            in_norm = sum(1 for n in normal_terminal_tasks if n is t)
            in_canc = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exc = sum(1 for e in exception_terminal_tasks if e is t)
            if in_norm != 0 or in_canc != 1 or in_exc != 0:
                raise AssertionError("CANCELLED_TERMINAL_TASK_MEMBERSHIP_INVALID")

        for t in exception_terminal_tasks:
            if sum(1 for k in all_known_tasks if k is t) != 1:
                raise AssertionError("EXCEPTION_TERMINAL_TASK_NOT_IN_ALL_KNOWN")
            if sum(1 for o in outcome_retrieved_tasks if o is t) != 1:
                raise AssertionError("EXCEPTION_TERMINAL_TASK_NOT_IN_OUTCOME_RETRIEVED")
            in_norm = sum(1 for n in normal_terminal_tasks if n is t)
            in_canc = sum(1 for c in cancelled_terminal_tasks if c is t)
            in_exc = sum(1 for e in exception_terminal_tasks if e is t)
            if in_norm != 0 or in_canc != 0 or in_exc != 1:
                raise AssertionError("EXCEPTION_TERMINAL_TASK_MEMBERSHIP_INVALID")

        if len(exception_terminal_tasks) != 0:
            raise AssertionError("EXCEPTION_TERMINAL_TASKS_NOT_EMPTY")

        for c in test_owned_coroutines:
            if not (c is runner_coro_obj or c is construct_fail_coro_obj or c is non_owning_control_coro_obj or any(r.controlled_coro is c for r in intercepted_task_records)):
                raise AssertionError("UNACCOUNTED_TEST_OWNED_CORO")

        # Runner task assertions
        if runner_task is None or runner_coro_obj is None:
            raise AssertionError("RUNNER_TASK_OR_CORO_NONE")
        if sum(1 for t in outcome_retrieved_tasks if t is runner_task) != 1:
            raise AssertionError("RUNNER_TASK_OUTCOME_RETRIEVAL_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is runner_task) != 1:
            raise AssertionError("RUNNER_TASK_NOT_IN_NORMAL_TERMINAL")
        if not runner_task.done() or runner_task.cancelled() or runner_task.exception() is not None:
            raise AssertionError("RUNNER_TASK_STATE_INVALID")
        if hasattr(runner_task, "get_coro") and runner_task.get_coro() is not runner_coro_obj:
            raise AssertionError("RUNNER_TASK_CORO_MISMATCH")
        if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is runner_coro_obj) != 0:
            raise AssertionError("RUNNER_CORO_IN_SOURCE_CLOSE_OUTSIDE")

        # Construction-control assertions
        if len(construction_control_classification_records) != 1:
            raise AssertionError("CONSTRUCTION_CONTROL_RECORD_COUNT_MISMATCH")
        if construction_control_classification_records[0][0] is not construct_fail_coro_obj or construction_control_classification_records[0][1] is not injected_construction_exception_instance:
            raise AssertionError("CONSTRUCTION_CONTROL_RECORD_IDENTITY_MISMATCH")
        if sum(1 for a in test_close_attempts if a is construct_fail_coro_obj) != 1:
            raise AssertionError("CONSTRUCTION_CONTROL_CLOSE_ATTEMPT_MISMATCH")
        if sum(1 for s in test_close_successes if s is construct_fail_coro_obj) != 1:
            raise AssertionError("CONSTRUCTION_CONTROL_CLOSE_SUCCESS_MISMATCH")
        if construct_fail_coro_obj.cr_frame is not None or construct_fail_coro_obj.cr_running is not False:
            raise AssertionError("CONSTRUCTION_CONTROL_NOT_CLOSED")
        if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is construct_fail_coro_obj) != 0:
            raise AssertionError("CONSTRUCTION_CONTROL_IN_SOURCE_CLOSE")
        if sum(1 for t in all_known_tasks if getattr(t, "get_coro", lambda: None)() is construct_fail_coro_obj) != 0:
            raise AssertionError("CONSTRUCTION_CONTROL_TASK_CREATED")

        # Explicit non-owning constructor control assertions
        if len(explicit_non_owning_classification_records) != 1:
            raise AssertionError("NON_OWNING_CONTROL_RECORD_COUNT_MISMATCH")
        if explicit_non_owning_classification_records[0][0] is not non_owning_control_coro_obj or explicit_non_owning_classification_records[0][1] is not non_owning_constructor_sentinel:
            raise AssertionError("NON_OWNING_CONTROL_RECORD_IDENTITY_MISMATCH")
        if sum(1 for a in test_close_attempts if a is non_owning_control_coro_obj) != 1:
            raise AssertionError("NON_OWNING_CONTROL_CLOSE_ATTEMPT_MISMATCH")
        if sum(1 for s in test_close_successes if s is non_owning_control_coro_obj) != 1:
            raise AssertionError("NON_OWNING_CONTROL_CLOSE_SUCCESS_MISMATCH")
        if non_owning_control_coro_obj.cr_frame is not None or non_owning_control_coro_obj.cr_running is not False:
            raise AssertionError("NON_OWNING_CONTROL_NOT_CLOSED")
        if sum(1 for s in (all_non_rescue_attempts + rescue_close_attempts) if s is non_owning_control_coro_obj) != 0:
            raise AssertionError("NON_OWNING_CONTROL_IN_SOURCE_CLOSE")
        if sum(1 for t in all_known_tasks if getattr(t, "get_coro", lambda: None)() is non_owning_control_coro_obj) != 0:
            raise AssertionError("NON_OWNING_CONTROL_TASK_CREATED")

        if len(test_close_attempts) != 2 or len(test_close_successes) != 2:
            raise AssertionError("TEST_CLOSE_LEDGER_SURPLUS")
        if len(test_owned_unauthorized_rejection_close_attempts) != 0 or len(test_owned_unauthorized_rejection_close_successes) != 0:
            raise AssertionError("UNAUTHORIZED_REJECTION_CLOSE_NOT_EMPTY")
        if len(constructor_side_effect_tasks) != 0:
            raise AssertionError("CONSTRUCTOR_SIDE_EFFECT_TASKS_NOT_EMPTY")

        # Cancellation control assertions
        if cancel_control_proxy is None:
            raise AssertionError("CANCEL_CONTROL_PROXY_NONE")
        if len(control_proxy_tracker["native_stall_task_cancel_calls"]) != 1:
            raise AssertionError("CANCEL_CONTROL_TRACKER_COUNT_MISMATCH")
        if len(cancel_control_proxy.cancel_calls) != 1:
            raise AssertionError("CANCEL_CONTROL_PROXY_CANCEL_COUNT_MISMATCH")
        if control_proxy_tracker["native_stall_task_cancel_calls"][0] is not cancel_control_proxy.cancel_calls[0]:
            raise AssertionError("CANCEL_CONTROL_RECORD_IDENTITY_MISMATCH")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is cancel_control_proxy) != 1:
            raise AssertionError("CANCEL_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in harness_cancelled_tasks if t is cancel_control_proxy._task) != 1:
            raise AssertionError("CANCEL_CONTROL_HARNESS_CANCEL_MISMATCH")
        if sum(1 for t in cancelled_terminal_tasks if t is cancel_control_proxy._task) != 1:
            raise AssertionError("CANCEL_CONTROL_CANCELLED_TERMINAL_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is cancel_control_proxy._task) != 0:
            raise AssertionError("CANCEL_CONTROL_IN_NORMAL_TERMINAL")
        if not cancel_control_proxy._task.cancelled():
            raise AssertionError("CANCEL_CONTROL_TASK_NOT_CANCELLED")

        # Surface witnesses assertions (Clause 3A)
        if create_task_control_proxy is None:
            raise AssertionError("CREATE_TASK_CONTROL_PROXY_NONE")
        if len(create_task_control_proxy.cancel_calls) != 0:
            raise AssertionError("CREATE_TASK_CONTROL_PROXY_CANCELLED")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is create_task_control_proxy) != 1:
            raise AssertionError("CREATE_TASK_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in harness_cancelled_tasks if t is create_task_control_proxy._task) != 0:
            raise AssertionError("CREATE_TASK_CONTROL_HARNESS_CANCELLED")
        if sum(1 for t in normal_terminal_tasks if t is create_task_control_proxy._task) != 1:
            raise AssertionError("CREATE_TASK_CONTROL_NORMAL_TERMINAL_MISMATCH")
        if create_task_control_proxy._task.cancelled():
            raise AssertionError("CREATE_TASK_CONTROL_TASK_CANCELLED")

        if ensure_future_control_proxy is None:
            raise AssertionError("ENSURE_FUTURE_CONTROL_PROXY_NONE")
        if len(ensure_future_control_proxy.cancel_calls) != 0:
            raise AssertionError("ENSURE_FUTURE_CONTROL_PROXY_CANCELLED")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is ensure_future_control_proxy) != 1:
            raise AssertionError("ENSURE_FUTURE_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in harness_cancelled_tasks if t is ensure_future_control_proxy._task) != 0:
            raise AssertionError("ENSURE_FUTURE_CONTROL_HARNESS_CANCELLED")
        if sum(1 for t in normal_terminal_tasks if t is ensure_future_control_proxy._task) != 1:
            raise AssertionError("ENSURE_FUTURE_CONTROL_NORMAL_TERMINAL_MISMATCH")
        if ensure_future_control_proxy._task.cancelled():
            raise AssertionError("ENSURE_FUTURE_CONTROL_TASK_CANCELLED")

        # Hostile surface probes assertions (Clause 3B)
        if len(ensure_future_repass_proxy_returns) != 1 or ensure_future_repass_proxy_returns[0] is not cancel_control_proxy:
            raise AssertionError("ENSURE_FUTURE_REPASS_PROXY_MISMATCH")
        if len(ensure_future_repass_task_returns) != 1 or ensure_future_repass_task_returns[0] is not cancel_control_proxy:
            raise AssertionError("ENSURE_FUTURE_REPASS_TASK_MISMATCH")

        if len(expected_unclassified_future_rejections) != 1 or expected_unclassified_future_rejections[0] is not expected_unclassified_future_rejection_instance:
            raise AssertionError("UNCLASSIFIED_FUTURE_REJECTION_MISMATCH")
        if len(unclassified_future_cancels) != 1 or unclassified_future_cancels[0] is not True:
            raise AssertionError("UNCLASSIFIED_FUTURE_CANCEL_MISMATCH")
        if len(unclassified_future_caught_cancellations) != 1 or not isinstance(unclassified_future_caught_cancellations[0], real_asyncio.CancelledError):
            raise AssertionError("UNCLASSIFIED_FUTURE_CAUGHT_CANCEL_MISMATCH")
        if len(unclassified_future_retrievals) != 1 or unclassified_future_retrievals[0] is not unclassified_future_obj:
            raise AssertionError("UNCLASSIFIED_FUTURE_RETRIEVAL_MISMATCH")
        if not unclassified_future_obj.cancelled():
            raise AssertionError("UNCLASSIFIED_FUTURE_NOT_CANCELLED")
        if sum(1 for t in all_known_tasks if t is unclassified_future_obj) != 0:
            raise AssertionError("UNCLASSIFIED_FUTURE_IN_ALL_KNOWN_TASKS")

        if len(expected_resolver_miss_rejections) != 1 or expected_resolver_miss_rejections[0] is not expected_resolver_miss_rejection_instance:
            raise AssertionError("RESOLVER_MISS_REJECTION_MISMATCH")
        if len(resolver_miss_matched_records) != 1 or resolver_miss_matched_records[0].source_coro is not resolver_miss_source_coro:
            raise AssertionError("RESOLVER_MISS_MATCHED_RECORD_MISMATCH")
        if len(resolver_miss_calls) != 1 or resolver_miss_calls[0] is not resolver_miss_matched_records[0].real_task:
            raise AssertionError("RESOLVER_MISS_CALL_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is resolver_miss_matched_records[0].real_task) != 1:
            raise AssertionError("RESOLVER_MISS_TASK_NORMAL_TERMINAL_MISMATCH")

        if len(expected_positional_rejections) != 1 or expected_positional_rejections[0] is not expected_positional_rejection_instance:
            raise AssertionError("POSITIONAL_REJECTION_MISMATCH")
        if sum(1 for s in positional_close_attempts if s is positional_probe_source_coro) != 1:
            raise AssertionError("POSITIONAL_PROBE_CLOSE_ATTEMPT_MISMATCH")
        if sum(1 for s in positional_close_successes if s is positional_probe_source_coro) != 1:
            raise AssertionError("POSITIONAL_PROBE_CLOSE_SUCCESS_MISMATCH")
        if sum(1 for s in interception_close_attempts if s is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_INTERCEPTION_CLOSE")
        if sum(1 for s in other_rejection_close_attempts if s is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_OTHER_REJECTION_CLOSE")
        if sum(1 for s in rescue_close_attempts if s is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_RESCUE_CLOSE")
        if sum(1 for r in intercepted_task_records if r.source_coro is positional_probe_source_coro) != 0:
            raise AssertionError("POSITIONAL_PROBE_IN_INTERCEPTED_RECORDS")

        # Six other-rejection probes assertions
        expected_other_exceptions = [
            expected_task_unsupported_args_exc,
            expected_task_wrong_loop_exc,
            expected_create_task_unsupported_args_exc,
            expected_create_task_wrong_loop_exc,
            expected_ensure_future_unsupported_args_exc,
            expected_ensure_future_wrong_loop_exc,
        ]
        if len(recorded_other_rejection_exceptions) != 6:
            raise AssertionError("RECORDED_OTHER_REJECTIONS_COUNT_MISMATCH")
        for exp_exc in expected_other_exceptions:
            if sum(1 for r in recorded_other_rejection_exceptions if r is exp_exc) != 1:
                raise AssertionError("RECORDED_OTHER_REJECTION_IDENTITY_MISMATCH")

        expected_other_sources = [
            task_unsupported_args_source_coro,
            task_wrong_loop_source_coro,
            create_task_unsupported_args_source_coro,
            create_task_wrong_loop_source_coro,
            ensure_future_unsupported_args_source_coro,
            ensure_future_wrong_loop_source_coro,
        ]
        if len(other_rejection_close_attempts) != 6 or len(other_rejection_close_successes) != 6:
            raise AssertionError("OTHER_REJECTION_CLOSE_COUNT_MISMATCH")
        for exp_src in expected_other_sources:
            if sum(1 for a in other_rejection_close_attempts if a is exp_src) != 1:
                raise AssertionError("OTHER_REJECTION_ATTEMPT_MISMATCH")
            if sum(1 for s in other_rejection_close_successes if s is exp_src) != 1:
                raise AssertionError("OTHER_REJECTION_SUCCESS_MISMATCH")
            if sum(1 for r in intercepted_task_records if r.source_coro is exp_src) != 0:
                raise AssertionError("OTHER_REJECTION_SOURCE_IN_RECORDS")

        # Late-window control assertions
        if late_control_proxy is None:
            raise AssertionError("LATE_CONTROL_PROXY_NONE")
        if len(late_control_proxy.cancel_calls) != 0:
            raise AssertionError("LATE_CONTROL_PROXY_CANCELLED")
        if sum(1 for p in control_proxy_tracker["proxies"] if p is late_control_proxy) != 1:
            raise AssertionError("LATE_CONTROL_PROXY_MEMBERSHIP_MISMATCH")
        if sum(1 for t in normal_terminal_tasks if t is late_control_proxy._task) != 1:
            raise AssertionError("LATE_CONTROL_NORMAL_TERMINAL_MISMATCH")
        if sum(1 for t in cancelled_terminal_tasks if t is late_control_proxy._task) != 0:
            raise AssertionError("LATE_CONTROL_IN_CANCELLED_TERMINAL")
        if late_control_proxy._task.cancelled():
            raise AssertionError("LATE_CONTROL_TASK_CANCELLED")

        # Final inventory assertions
        if final_inventory_pass_tags != ["PASS_1_INITIAL", "PASS_2_POST_SETTLE", "PASS_3_FINAL_VERIFY"]:
            raise AssertionError("FINAL_INVENTORY_PASS_TAGS_MISMATCH")
        if len(final_inventory_snapshots) != 3:
            raise AssertionError("FINAL_INVENTORY_SNAPSHOTS_COUNT_MISMATCH")
        if len(cleanup_discovered_tasks) != 0:
            raise AssertionError("CLEANUP_DISCOVERED_TASKS_NOT_EMPTY")
        for snap in final_inventory_snapshots:
            for t in snap:
                if sum(1 for c in final_inventory_authorized_tasks_cutoff if c is t) != 1:
                    raise AssertionError("SNAPSHOT_TASK_NOT_IN_CUTOFF")
        for c in final_inventory_authorized_tasks_cutoff:
            if sum(1 for k in all_known_tasks if k is c) != 1:
                raise AssertionError("CUTOFF_TASK_NOT_IN_ALL_KNOWN")

        # Unhanded expiry check if created
        if tracked_expiry_coro_obj is not None:
            if not any(r.source_coro is tracked_expiry_coro_obj for r in intercepted_task_records):
                if sum(1 for s in interception_close_attempts if s is tracked_expiry_coro_obj) != 1:
                    raise AssertionError("UNHANDED_EXPIRY_ATTEMPT_MISMATCH")
                if sum(1 for s in interception_close_successes if s is tracked_expiry_coro_obj) != 1:
                    raise AssertionError("UNHANDED_EXPIRY_SUCCESS_MISMATCH")
    except AssertionError:
        raise
    except BaseException:
        raise AssertionError("POST_LOOP_ASSERTION_EXECUTION_FAILED")

    # 3. Exact schema assertion for fallback
    expected_effects = {
        "http_request_headers_property_reads": 0,
        "http_request_headers_reads": [],
        "http_request_state_property_reads": 0,
        "http_request_app_property_reads": 0,
        "settings_reads": [],
        "verify_bearer_calls": [],
        "set_current_auth_calls": [],
        "reset_current_auth_calls": [],
        "set_auth_enforced_calls": [],
        "reset_auth_enforced_calls": [],
        "state_auth_user_writes": 0,
        "call_next_calls": [],
        "json_response_calls": [],
        "http_session_mgr_reads": [],
        "storage_reads": [],
        "deserialize_session_calls": [],
        "model_dump_calls": [],
        "ws_headers_property_reads": 0,
        "ws_headers_reads": [],
        "ws_query_params_property_reads": 0,
        "ws_query_params_reads": [],
        "ws_app_property_reads": 0,
        "ticket_entry_index_reads": [],
        "browser_read_session_calls": [],
        "clock_reads": [],
        "browser_connect_calls": [],
        "browser_disconnect_calls": [],
        "browser_receive_json_count": 0,
        "browser_expiry_coro_create_count": 0,
        "browser_expiry_task_create_calls": [],
        "browser_expiry_task_cancel_calls": [],
        "browser_expiry_task_await_calls": [],
        "native_session_mgr_reads": [],
        "native_compare_digest_calls": [],
        "native_receive_count": 0,
        "native_sm_lock_calls": [],
        "fake_sm_construct_calls": [],
        "fake_sm_start_calls": [],
        "fake_sm_send_audio_calls": [],
        "fake_wsm_next_seq_calls": [],
        "fake_wsm_broadcast_calls": [],
        "native_stall_task_create_calls": [],
        "native_stall_task_cancel_calls": [],
        "native_stall_task_await_calls": [],
        "unexpected_task_create_calls": [],
        "ws_denial_responses": [],
        "ws_close_calls": [],
        "ws_accept_calls": [],
        "ws_send_json_calls": [],
        "ws_send_bytes_calls": [],
        "logger_events": [],
        "registry_mutations": [],
        "registry_reads": [],
        "app_state_property_reads": 0,
        "http_gate_read_count": 0,
        "browser_gate_read_count": 0,
        "native_gate_read_count": 0,
        "event_trace": [],
    }
    expected_effects["browser_gate_read_count"] = 1
    expected_effects["native_gate_read_count"] = 1
    expected_effects["ws_app_property_reads"] = 2
    expected_effects["app_state_property_reads"] = 2
    expected_effects["ws_close_calls"] = [
        ("browser_gate_read_count", {"code": 1008}),
        ("native_gate_read_count", {"code": 1008}),
    ]
    expected_effects["event_trace"] = [
        "browser:ws_app",
        "browser:app_state",
        "browser:ready_read",
        "browser:close:1008",
        "native:ws_app",
        "native:app_state",
        "native:ready_read",
        "native:close:1008",
    ]

    if effects != expected_effects:
        raise AssertionError("FALLBACK_EFFECTS_SCHEMA_MISMATCH")

    # 4. Identity & content snapshots
    assert main.settings is (settings_spy if has_settings else None)
    assert main.logger is logger_spy
    assert main.ws_manager is fake_wsm
    assert main.session_mgr is fake_native_sm_inst
    assert main.firestore_storage is fake_fs
    assert main.verify_bearer_token is counting_verify
    assert main._read_session is counting_read_session
    assert main.deserialize_session is counting_deserialize
    assert main.set_current_auth is counting_set_auth
    assert main.reset_current_auth is counting_reset_auth
    assert main.set_auth_enforced is counting_set_enforced
    assert main.reset_auth_enforced is counting_reset_enforced
    assert main.StreamManager is InstrumentedStreamManager
    assert main._close_ws_at_expiry is counting_close_ws_factory
    assert main.native_sm_lock is fresh_lock

    assert main.ws_tickets is tickets_mapping
    assert main.stream_keys is stream_keys_mapping
    assert main.stop_capabilities is stop_cap_mapping
    assert main.context_windows is fresh_context_windows
    assert main.pipeline_tasks is fresh_pipeline_tasks
    assert main.native_session_health is fresh_native_health
    assert main.native_frame_last_seq is fresh_native_frame_last_seq
    assert main.native_stream_managers is fresh_native_sm
    assert main.stream_managers is fresh_stream_managers
    assert main.deleted_sessions is fresh_deleted

    assert tickets_mapping.raw_dict() == {ticket: ticket_entry}
    assert stream_keys_mapping.raw_dict() == {"s1": "test-stream-key"}
    assert stop_cap_mapping.raw_dict() == {"cap-1": stop_cap_entry}
    assert fresh_context_windows.raw_dict() == {}
    assert fresh_pipeline_tasks.raw_dict() == {}
    assert fresh_native_health.raw_dict() == {}
    assert fresh_native_frame_last_seq.raw_dict() == {}
    assert fresh_native_sm.raw_dict() == {}
    assert fresh_stream_managers.raw_dict() == {}
    assert fresh_deleted.raw_set() == set()

    assert fake_wsm.connect_calls == []
    assert fake_wsm.disconnect_calls == []
    assert fake_wsm.broadcast_calls == []
    assert fake_wsm.seq_calls == []
    assert current_auth() is None
    assert auth_is_enforced() is False


def test_positive_http_controls_when_ready(monkeypatch):
    """Positive controls for HTTP with ready=True proving every HTTP seam runs and increments."""
    from starlette.datastructures import State
    from starlette.testclient import TestClient

    s = auth_settings()
    effects = make_wired_effect_schema()
    settings_spy = SettingsAttributeSpy(s, effects)
    monkeypatch.setattr(main, "settings", settings_spy)
    main.app.state.ready = True

    user = AuthContext("uid-a", "recruiter@example.com", "ella-internal")

    stop_cap_mapping = RecordingDict("stop_capabilities", effects)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=60)
    stop_cap_mapping.raw_set("cap-1", (user, "s1", expiry))
    monkeypatch.setattr(main, "stop_capabilities", stop_cap_mapping)

    state_auth_user_writes = 0
    orig_state_setattr = State.__setattr__

    def counting_state_setattr(self, name, value):
        nonlocal state_auth_user_writes
        if name == "auth_user":
            state_auth_user_writes += 1
        return orig_state_setattr(self, name, value)

    monkeypatch.setattr(State, "__setattr__", counting_state_setattr)

    verify_calls = []

    def counting_verify(authorization=None, *args, **kwargs):
        verify_calls.append(authorization)
        if authorization:
            return user
        raise AuthenticationError("Missing bearer token")

    monkeypatch.setattr(main, "verify_bearer_token", counting_verify)

    set_auth_calls = []
    orig_set_auth = main.set_current_auth

    def tracking_set_auth(ctx):
        set_auth_calls.append(ctx)
        return orig_set_auth(ctx)

    monkeypatch.setattr(main, "set_current_auth", tracking_set_auth)

    reset_auth_tokens = []
    orig_reset_auth = main.reset_current_auth

    def tracking_reset_auth(token):
        reset_auth_tokens.append(token)
        return orig_reset_auth(token)

    monkeypatch.setattr(main, "reset_current_auth", tracking_reset_auth)

    set_enforced_calls = []
    orig_set_enforced = main.set_auth_enforced

    def tracking_set_enforced(value=True):
        set_enforced_calls.append(value)
        return orig_set_enforced(value)

    monkeypatch.setattr(main, "set_auth_enforced", tracking_set_enforced)

    reset_enforced_tokens = []
    orig_reset_enforced = main.reset_auth_enforced

    def tracking_reset_enforced(token):
        reset_enforced_tokens.append(token)
        return orig_reset_enforced(token)

    monkeypatch.setattr(main, "reset_auth_enforced", tracking_reset_enforced)

    # Distinct stop and persisted-read session objects
    active_stop_session = SimpleNamespace(
        id="s1",
        owner_id="uid-a",
        org_id="ella-internal",
        status=SessionStatus.ACTIVE,
        mode="meeting",
    )
    completed_stop_session = SimpleNamespace(
        id="s1",
        owner_id="uid-a",
        org_id="ella-internal",
        status=SessionStatus.COMPLETED,
        mode="meeting",
    )

    session_read_order = []
    model_dump_calls = []

    def fake_model_dump():
        model_dump_calls.append("model_dump")
        session_read_order.append(("session.model_dump",))
        return {
            "session_id": "s1",
            "owner_id": "uid-a",
            "org_id": "ella-internal",
            "status": "active",
        }

    fake_session_model = SimpleNamespace(
        owner_id="uid-a",
        org_id="ella-internal",
        status=SessionStatus.ACTIVE,
        model_dump=fake_model_dump,
    )

    class FakeSessionManager:
        def __init__(self):
            self.get_session_calls: list[str] = []
            self.stop_session_calls: list[tuple[str, bool]] = []

        def get_session(self, session_id):
            session_read_order.append(("session_mgr.get_session", session_id))
            self.get_session_calls.append(session_id)
            if len(self.get_session_calls) == 1:
                return active_stop_session
            # Return None to exercise firestore fallback + deserialization on second call
            return None

        async def stop_session(self, session_id, *, transcription_complete):
            self.stop_session_calls.append((session_id, transcription_complete))
            return completed_stop_session

    fake_sm = FakeSessionManager()
    monkeypatch.setattr(main, "session_mgr", fake_sm)

    persisted_record = {
        "session_id": "s1",
        "ownerId": "uid-a",
        "orgId": "ella-internal",
        "status": "active",
    }

    class FakeFirestore:
        def __init__(self):
            self.get_record_calls: list[str] = []
            self.save_session_calls: list[Any] = []

        async def get_session_record(self, session_id):
            self.get_record_calls.append(session_id)
            session_read_order.append(("firestore.get_session_record", session_id))
            return persisted_record

        async def save_session(self, session):
            self.save_session_calls.append(session)

    fake_fs = FakeFirestore()
    monkeypatch.setattr(main, "firestore_storage", fake_fs)

    stop_pipeline_calls = []

    async def fake_stop_pipeline(session_id):
        stop_pipeline_calls.append(session_id)
        return True

    monkeypatch.setattr(main, "_stop_pipeline", fake_stop_pipeline)

    schedule_summary_calls = []

    def fake_schedule_summary(session_id):
        schedule_summary_calls.append(session_id)

    monkeypatch.setattr(main, "_schedule_final_summary_once", fake_schedule_summary)

    deserialize_calls = []

    def fake_deserialize(session_id, record):
        deserialize_calls.append((session_id, record))
        session_read_order.append(("deserialize_session", session_id, record))
        return fake_session_model

    monkeypatch.setattr(main, "deserialize_session", fake_deserialize)

    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        # 1. Bearer /api/me
        res_me = client.get("/api/me", headers={"Authorization": "Bearer valid-token"})
        assert res_me.status_code == 200
        assert res_me.json() == {"uid": "uid-a", "email": "recruiter@example.com", "org_id": "ella-internal"}
        assert len(verify_calls) == 1
        assert verify_calls[0] == "Bearer valid-token"
        assert len(set_auth_calls) >= 1
        assert set_auth_calls[-1] == user
        assert len(reset_auth_tokens) == len(set_auth_calls)
        assert len(set_enforced_calls) >= 1
        assert len(reset_enforced_tokens) == len(set_enforced_calls)
        assert state_auth_user_writes >= 1
        assert len(effects["settings_reads"]) >= 1

        # 2. Matching stop capability route
        pre_stop_reads = sum(1 for r in effects["registry_reads"] if r == ("stop_capabilities", "get", "cap-1"))
        res_stop = client.post(
            "/api/sessions/s1/stop",
            headers={"X-TARS-Stop-Capability": "cap-1"},
        )
        post_stop_reads = sum(1 for r in effects["registry_reads"] if r == ("stop_capabilities", "get", "cap-1"))
        assert post_stop_reads == pre_stop_reads + 1
        assert res_stop.status_code == 200
        assert res_stop.json() == {
            "session_id": "s1",
            "status": "completed",
            "transcription_complete": True,
        }
        assert res_stop.json()["transcription_complete"] is True
        assert stop_pipeline_calls == ["s1"]
        assert fake_sm.get_session_calls == ["s1"]
        assert fake_sm.stop_session_calls == [("s1", True)]
        assert fake_sm.stop_session_calls[0][1] is True
        assert len(fake_fs.save_session_calls) == 1
        assert fake_fs.save_session_calls[0] is completed_stop_session
        assert schedule_summary_calls == ["s1"]
        assert session_read_order == [("session_mgr.get_session", "s1")]
        session_read_order.clear()

        # 3. Protected session get route (exercises session_mgr, firestore, deserialize, model_dump)
        res_sess = client.get("/api/sessions/s1", headers={"Authorization": "Bearer valid-token"})
        assert res_sess.status_code == 200
        assert res_sess.json() == {
            "session_id": "s1",
            "owner_id": "uid-a",
            "org_id": "ella-internal",
            "status": "active",
        }
        assert fake_sm.get_session_calls == ["s1", "s1"]
        assert fake_fs.get_record_calls == ["s1"]
        assert deserialize_calls == [("s1", persisted_record)]
        assert deserialize_calls[0][1] is persisted_record
        assert session_read_order == [
            ("session_mgr.get_session", "s1"),
            ("firestore.get_session_record", "s1"),
            ("deserialize_session", "s1", persisted_record),
            ("session.model_dump",),
        ]
        assert model_dump_calls == ["model_dump"]
    finally:
        client.close()


def test_positive_browser_ws_controls_when_ready(monkeypatch):
    """Positive control for browser WebSocket when ready=True with fresh fake ws_manager."""
    from starlette.testclient import TestClient
    from backend.auth import current_auth

    s = auth_settings()
    effects = make_wired_effect_schema()
    settings_spy = SettingsAttributeSpy(s, effects)
    monkeypatch.setattr(main, "settings", settings_spy)
    main.app.state.ready = True

    user = AuthContext("uid-a", "recruiter@example.com", "ella-internal")
    ticket = "test-ticket-browser-pos"
    exp_time = datetime.now(timezone.utc) + timedelta(seconds=60)
    tickets_mapping = RecordingDict("ws_tickets", effects)
    tickets_mapping.raw_set(ticket, (user, "s1", exp_time))
    monkeypatch.setattr(main, "ws_tickets", tickets_mapping)

    fake_wsm = FakeWSConnectionManager(effects)
    monkeypatch.setattr(main, "ws_manager", fake_wsm)

    read_session_calls = []

    async def fake_read_session(session_id):
        read_session_calls.append({"session_id": session_id, "auth": current_auth()})
        return SimpleNamespace(
            owner_id="uid-a",
            org_id="ella-internal",
            status=SessionStatus.ACTIVE,
        )

    monkeypatch.setattr(main, "_read_session", fake_read_session)

    expiry_coro_calls = []

    async def tracking_close_ws(ws, exp):
        expiry_coro_calls.append({
            "ws": ws,
            "exp": exp,
            "auth": current_auth(),
        })
        try:
            while True:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise

    monkeypatch.setattr(main, "_close_ws_at_expiry", tracking_close_ws)

    created_tasks: list[TrackedTaskProxy] = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro, *args, **kwargs):
        real_task = real_create_task(coro, *args, **kwargs)
        proxy = TrackedTaskProxy(real_task, effects, is_browser=True)
        created_tasks.append(proxy)
        return proxy

    monkeypatch.setattr(asyncio, "create_task", tracking_create_task)

    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        with client.websocket_connect(
            "/ws/s1?last_seq=7",
            subprotocols=["tars-ticket", ticket],
        ) as ws:
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong == {"type": "pong"}

        # Assert exact post-connect and post-disconnect effects
        assert effects["registry_mutations"].count(("ws_tickets", "pop", ticket, None)) == 1
        assert ticket not in tickets_mapping
        assert len(read_session_calls) == 1
        assert read_session_calls[0] == {"session_id": "s1", "auth": user}
        assert len(fake_wsm.connect_calls) == 1
        assert fake_wsm.connect_calls[0]["session_id"] == "s1"
        assert fake_wsm.connect_calls[0]["last_seq"] == 7
        assert fake_wsm.connect_calls[0]["subprotocol"] == "tars-ticket"
        assert fake_wsm.connect_calls[0]["auth"] is user
        assert len(fake_wsm.disconnect_calls) == 1
        assert fake_wsm.disconnect_calls[0]["session_id"] == "s1"
        assert fake_wsm.disconnect_calls[0]["auth"] is user
        assert len(expiry_coro_calls) == 1
        assert expiry_coro_calls[0]["exp"] is exp_time
        assert expiry_coro_calls[0]["auth"] is user
        assert len(created_tasks) == 1
        assert created_tasks[0].done() is True
        assert created_tasks[0].cancelled() is True
        assert len(created_tasks[0].cancel_calls) >= 1
        assert created_tasks[0].await_count == 1
        assert len(fake_wsm.active_connections.get("s1", [])) == 0
    finally:
        client.close()


def test_positive_native_ws_controls_when_ready(monkeypatch):
    """Positive control for native WebSocket when ready=True with fresh fake StreamManager & ws_manager."""
    from starlette.testclient import TestClient
    import json
    import struct

    s = auth_settings()
    effects = make_wired_effect_schema()
    settings_spy = SettingsAttributeSpy(s, effects)
    monkeypatch.setattr(main, "settings", settings_spy)
    main.app.state.ready = True

    stream_keys_mapping = RecordingDict("stream_keys", effects)
    stream_keys_mapping.raw_set("s1", "test-native-stream-key")
    monkeypatch.setattr(main, "stream_keys", stream_keys_mapping)

    fake_wsm = FakeWSConnectionManager(effects)
    monkeypatch.setattr(main, "ws_manager", fake_wsm)

    class FakeNativeSessionManager:
        def __init__(self):
            self.calls: list[str] = []

        def get_session(self, session_id):
            self.calls.append(session_id)
            return SimpleNamespace(
                owner_id="uid-a",
                org_id="ella-internal",
                status=SessionStatus.ACTIVE,
            )

    fake_sm = FakeNativeSessionManager()
    monkeypatch.setattr(main, "session_mgr", fake_sm)

    fresh_health = {}
    monkeypatch.setattr(main, "native_session_health", fresh_health)
    fresh_frame_last_seq = {}
    monkeypatch.setattr(main, "native_frame_last_seq", fresh_frame_last_seq)
    fresh_native_sm = {}
    monkeypatch.setattr(main, "native_stream_managers", fresh_native_sm)
    fresh_stream_managers = {}
    monkeypatch.setattr(main, "stream_managers", fresh_stream_managers)
    fresh_lock = asyncio.Lock()
    monkeypatch.setattr(main, "native_sm_lock", fresh_lock)

    created_sm_instances: list[FakeStreamManager] = []

    class TrackingStreamManager(FakeStreamManager):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, tracker=effects, **kwargs)
            created_sm_instances.append(self)

    monkeypatch.setattr(main, "StreamManager", TrackingStreamManager)

    created_stall_tasks: list[TrackedTaskProxy] = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro, *args, **kwargs):
        real_task = real_create_task(coro, *args, **kwargs)
        proxy = TrackedTaskProxy(real_task, effects, is_browser=False)
        created_stall_tasks.append(proxy)
        return proxy

    monkeypatch.setattr(asyncio, "create_task", tracking_create_task)

    client = TestClient(main.app, raise_server_exceptions=False)
    try:
        with client.websocket_connect(
            "/api/stream/native/s1",
            subprotocols=["tars-stream", "test-native-stream-key"],
        ) as ws:
            # Prepare a valid binary audio frame
            header_bytes = json.dumps(
                {"session_id": "s1", "source": "microphone", "sequence": 1}
            ).encode("utf-8")
            header_len = len(header_bytes)
            pcm_payload = b"\x01\x02\x03\x04"
            raw_frame = struct.pack(">I", header_len) + header_bytes + pcm_payload

            ws.send_bytes(raw_frame)
            ws.send_json({"type": "ping"})
            pong = ws.receive_json()
            assert pong == {"type": "pong"}

        # Assert exact native effects
        assert effects["registry_reads"].count(("stream_keys", "get", "s1")) == 1
        assert len(fake_sm.calls) >= 1
        assert "s1" in fresh_health
        assert fresh_frame_last_seq["s1"]["microphone"] == 1
        assert len(created_sm_instances) == 1
        assert created_sm_instances[0].started is True
        assert created_sm_instances[0].audio_sends == [pcm_payload]
        assert len(fake_wsm.broadcast_calls) >= 2  # connect health + disconnect health
        assert len(created_stall_tasks) == 1
        assert created_stall_tasks[0].done() is True
        assert created_stall_tasks[0].cancelled() is True
        assert len(created_stall_tasks[0].cancel_calls) >= 1
        assert created_stall_tasks[0].await_count == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    "bad_subprotocol",
    [
        ",tars-ticket,valid-ticket",  # leading comma
        "tars-ticket,valid-ticket,",  # trailing comma
        "tars-ticket,,valid-ticket",  # doubled comma
        "tars-ticket,",               # empty second token
        ",tars-ticket",               # empty first token
        "tars-ticket",                # single token
        "tars-ticket,t1,t2",          # three tokens
        "wrong-protocol,valid-ticket",
        "",
    ],
)
def test_websocket_rejects_malformed_subprotocols_without_ticket_consumption(bad_subprotocol, monkeypatch):
    ticket = "valid-ticket"
    user = AuthContext("uid-a", "a@example.com", "ella-internal")
    main.ws_tickets.clear()
    main.ws_tickets[ticket] = (
        user,
        "s1",
        datetime.now(timezone.utc) + timedelta(seconds=60),
    )

    s = auth_settings()
    monkeypatch.setattr(main, "settings", s)
    main.app.state.ready = True

    class FakeWS:
        def __init__(self, raw_header: str):
            self.headers = {"sec-websocket-protocol": raw_header}
            self.query_params = {}
            self.app = main.app
            self.closed: list[dict] = []

        async def close(self, **kwargs):
            self.closed.append(kwargs)

    try:
        ws = FakeWS(bad_subprotocol)
        asyncio.run(main.websocket_endpoint(ws, "s1"))
        assert ws.closed == [{"code": 1008}]
        # Assert ticket was NOT consumed
        assert ticket in main.ws_tickets
    finally:
        main.ws_tickets.clear()


class CustomBaseException(BaseException):
    """Custom BaseException subclass not caught by except Exception."""
    pass


def test_browser_ws_post_auth_failure_paths_and_lifecycle(monkeypatch):
    """Browser post-auth failure-path and lifecycle causality executed inside running coroutines."""
    real_create_task = asyncio.create_task
    real_shield = asyncio.shield
    real_gather = asyncio.gather

    ticket_user = AuthContext("uid-ticket", "ticket@example.com", "ella-internal")
    baseline_user = AuthContext("uid-baseline", "baseline@example.com", "ella-internal")
    TIMEOUT = 2.0

    async def wait_ev(ev: asyncio.Event, desc: str):
        try:
            await asyncio.wait_for(ev.wait(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            raise AssertionError(f"TIMEOUT_WAITING_FOR_BARRIER: {desc}")

    async def wait_endpoint_done(task: asyncio.Task, desc: str):
        done, pending = await asyncio.wait({task}, timeout=TIMEOUT)
        if pending:
            raise AssertionError(f"ENDPOINT_TASK_NOT_DONE_WITHIN_BOUND: {desc}")

    class TaskProxy:
        def __init__(self, real_task, tracker):
            self._real_task = real_task
            self._tracker = tracker
            self.cancel_count = 0
            self.await_count = 0

        def cancel(self, *args, **kwargs):
            self.cancel_count += 1
            self._tracker["events"].append("proxy_cancel")
            self._tracker["cancel_calls"].append({
                "args": args,
                "kwargs": kwargs,
                "auth": main.current_auth(),
            })
            self._tracker["cancel_auth"].append(main.current_auth())
            return self._real_task.cancel(*args, **kwargs)

        def done(self):
            return self._real_task.done()

        def cancelled(self):
            return self._real_task.cancelled()

        def result(self):
            return self._real_task.result()

        def exception(self):
            return self._real_task.exception()

        def __await__(self):
            self.await_count += 1
            self._tracker["events"].append("proxy_await")
            self._tracker["await_auth"].append(main.current_auth())
            return self._real_task.__await__()

    class StatefulWSManager:
        def __init__(self, tracker, connect_side_effect=None, disconnect_side_effect=None):
            self._tracker = tracker
            self.active_sockets = set()
            self.connect_calls: list[dict] = []
            self.disconnect_calls: list[dict] = []
            self.connect_side_effect = connect_side_effect
            self.disconnect_side_effect = disconnect_side_effect

        async def connect(self, websocket, session_id, last_seq=0, subprotocol="tars-ticket"):
            self._tracker["events"].append("ws_connect")
            self.connect_calls.append({
                "ws": websocket,
                "session_id": session_id,
                "last_seq": last_seq,
                "subprotocol": subprotocol,
                "auth": main.current_auth(),
            })
            self.active_sockets.add(websocket)
            if self.connect_side_effect is not None:
                if isinstance(self.connect_side_effect, BaseException):
                    raise self.connect_side_effect
                elif callable(self.connect_side_effect):
                    return await self.connect_side_effect(websocket, session_id)

        def disconnect(self, websocket, session_id):
            self._tracker["events"].append("ws_disconnect")
            self.disconnect_calls.append({
                "ws": websocket,
                "session_id": session_id,
                "auth": main.current_auth(),
            })
            self.active_sockets.discard(websocket)
            if self.disconnect_side_effect is not None:
                if isinstance(self.disconnect_side_effect, BaseException):
                    raise self.disconnect_side_effect
                elif callable(self.disconnect_side_effect):
                    self.disconnect_side_effect(websocket, session_id)

    class MockWS:
        def __init__(self, session_id, ticket, tracker, receive_fn=None):
            self.headers = {"sec-websocket-protocol": f"tars-ticket,{ticket}"}
            self.query_params = {}
            self.app = main.app
            self.closed: list[dict] = []
            self.tracker = tracker
            self.receive_fn = receive_fn

        async def close(self, **kwargs):
            self.tracker["events"].append("ws_close")
            self.tracker["close_calls"].append({"kwargs": kwargs, "auth": main.current_auth()})
            self.closed.append(kwargs)

        async def receive_json(self):
            self.tracker["events"].append("ws_receive")
            self.tracker["receive_auth"].append(main.current_auth())
            if self.receive_fn is not None:
                return await self.receive_fn()
            raise WebSocketDisconnect(1000)

    def assert_connected_ws(mgr, ws, ticket_user):
        assert len(mgr.connect_calls) == 1
        assert mgr.connect_calls[0]["ws"] is ws
        assert mgr.connect_calls[0]["session_id"] == "s1"
        assert mgr.connect_calls[0]["last_seq"] == 0
        assert mgr.connect_calls[0]["subprotocol"] == "tars-ticket"
        assert mgr.connect_calls[0]["auth"] is ticket_user
        assert len(mgr.disconnect_calls) == 1
        assert mgr.disconnect_calls[0]["ws"] is ws
        assert mgr.disconnect_calls[0]["session_id"] == "s1"
        assert mgr.disconnect_calls[0]["auth"] is ticket_user
        assert len(mgr.active_sockets) == 0

    async def run_scenario(
        name,
        *,
        read_session_fn,
        connect_side_effect=None,
        disconnect_side_effect=None,
        create_task_side_effect=None,
        child_coro_behavior="block",
        child_injected_error=None,
        receive_fn_builder=None,
        expect_exc=None,
        expect_close_code=None,
        cancellation_mode=None,
    ):
        orig_has_ready = hasattr(main.app.state, "ready")
        orig_ready = getattr(main.app.state, "ready", None) if orig_has_ready else None
        orig_settings = main.settings
        orig_ws_manager = main.ws_manager
        orig_ws_tickets = main.ws_tickets
        orig_ws_tickets_snapshot = dict(main.ws_tickets)
        orig_current_auth = main.current_auth()

        scenario_result = None

        with monkeypatch.context() as m:
            baseline_token = main.set_current_auth(baseline_user)
            assert main.current_auth() is baseline_user

            baseline_tasks = set(asyncio.all_tasks())

            tracker = {
                "events": [],
                "set_auth_calls": [],
                "reset_auth_calls": [],
                "read_session_calls": [],
                "close_calls": [],
                "cancel_calls": [],
                "cancel_auth": [],
                "await_auth": [],
                "child_coro_calls": [],
                "child_start_auth": [],
                "child_finally_auth": [],
                "receive_auth": [],
                "captured_coroutine": None,
            }

            s = auth_settings()
            m.setattr(main, "settings", s)
            main.app.state.ready = True

            gather_calls = 0
            gather_futures: list[asyncio.Future] = []
            shield_entries = 0
            shield_futures: list[asyncio.Future] = []
            shield_caught_cancels: list[asyncio.CancelledError] = []
            shield_entry_events = {2: asyncio.Event(), 3: asyncio.Event()}
            logger_exceptions = []
            logger_warnings = []

            def tracking_gather(*args, **kwargs):
                nonlocal gather_calls
                gather_calls += 1
                tracker["events"].append("gather_called")
                fut = real_gather(*args, **kwargs)
                gather_futures.append(fut)
                return fut

            async def tracking_shield(arg):
                nonlocal shield_entries
                shield_entries += 1
                tracker["events"].append(f"shield_enter_{shield_entries}")
                if shield_entries in shield_entry_events:
                    shield_entry_events[shield_entries].set()
                fut = real_shield(arg)
                shield_futures.append(fut)
                try:
                    return await fut
                except asyncio.CancelledError as exc:
                    shield_caught_cancels.append(exc)
                    raise

            def spy_exception(event, *args, **kwargs):
                logger_exceptions.append({"event": event, "args": args, "kwargs": kwargs})

            def spy_warning(event, *args, **kwargs):
                logger_warnings.append({"event": event, "args": args, "kwargs": kwargs})

            m.setattr(asyncio, "gather", tracking_gather)
            m.setattr(asyncio, "shield", tracking_shield)
            m.setattr(main.logger, "exception", spy_exception)
            m.setattr(main.logger, "warning", spy_warning)

            orig_set_auth = main.set_current_auth
            orig_reset_auth = main.reset_current_auth

            def tracking_set_auth(ctx):
                tok = orig_set_auth(ctx)
                tracker["events"].append("set_current_auth")
                tracker["set_auth_calls"].append({
                    "token": tok,
                    "ctx": ctx,
                    "auth": main.current_auth(),
                })
                return tok

            def tracking_reset_auth(tok):
                tracker["events"].append("reset_current_auth")
                tracker["reset_auth_calls"].append({
                    "token": tok,
                    "auth_before": main.current_auth(),
                })
                orig_reset_auth(tok)

            m.setattr(main, "set_current_auth", tracking_set_auth)
            m.setattr(main, "reset_current_auth", tracking_reset_auth)

            async def tracking_read_session(sid):
                tracker["events"].append("read_session")
                tracker["read_session_calls"].append({
                    "session_id": sid,
                    "auth": main.current_auth(),
                })
                return await read_session_fn(sid)

            m.setattr(main, "_read_session", tracking_read_session)

            manager = StatefulWSManager(
                tracker,
                connect_side_effect=connect_side_effect,
                disconnect_side_effect=disconnect_side_effect,
            )
            m.setattr(main, "ws_manager", manager)

            child_started_event = asyncio.Event()
            receive_cancel_ready_event = asyncio.Event()
            child_finalizer_entered_event = asyncio.Event()
            child_release_event = asyncio.Event()
            child_error_entered_event = asyncio.Event()
            child_error_release_event = asyncio.Event()
            child_done_event = asyncio.Event()

            ticket_expiry = datetime.now(timezone.utc) + timedelta(seconds=60)

            async def tracking_expiry_coro(ws, exp):
                tracker["events"].append("child_started")
                tracker["child_coro_calls"].append({
                    "ws": ws,
                    "exp": exp,
                    "auth": main.current_auth(),
                })
                tracker["child_start_auth"].append(main.current_auth())
                child_started_event.set()
                try:
                    if child_coro_behavior == "block":
                        while True:
                            await asyncio.sleep(0.01)
                    elif child_coro_behavior == "done_success":
                        return "child_ok"
                    elif child_coro_behavior == "done_cancel":
                        raise asyncio.CancelledError()
                    elif child_coro_behavior == "done_error":
                        raise child_injected_error
                    elif child_coro_behavior == "pause_finalizer":
                        while True:
                            await asyncio.sleep(0.01)
                    elif child_coro_behavior == "catch_cancel_and_error":
                        try:
                            while True:
                                await asyncio.sleep(0.01)
                        except asyncio.CancelledError:
                            tracker["events"].append("child_cancel_caught")
                            if child_injected_error is not None:
                                raise child_injected_error
                    elif child_coro_behavior == "pause_then_error_on_cancel":
                        try:
                            while True:
                                await asyncio.sleep(0.01)
                        except asyncio.CancelledError:
                            tracker["events"].append("child_cancel_caught")
                            child_error_entered_event.set()
                            await wait_ev(child_error_release_event, "child_error_release")
                            if child_injected_error is not None:
                                raise child_injected_error
                finally:
                    tracker["events"].append("child_finally_entered")
                    tracker["child_finally_auth"].append(main.current_auth())
                    if child_coro_behavior == "pause_finalizer":
                        tracker["events"].append("child_finalizer_paused")
                        child_finalizer_entered_event.set()
                        await wait_ev(child_release_event, "child_release")
                    tracker["events"].append("child_finally_done")
                    child_done_event.set()

            m.setattr(main, "_close_ws_at_expiry", tracking_expiry_coro)

            created_proxies: list[TaskProxy] = []

            def tracking_create_task(coro):
                tracker["events"].append("create_task")
                if create_task_side_effect is not None:
                    tracker["captured_coroutine"] = coro
                    if isinstance(create_task_side_effect, BaseException):
                        raise create_task_side_effect
                    elif callable(create_task_side_effect):
                        return create_task_side_effect(coro)
                real_t = real_create_task(coro)
                proxy = TaskProxy(real_t, tracker)
                created_proxies.append(proxy)
                return proxy

            m.setattr(asyncio, "create_task", tracking_create_task)

            ticket = f"ticket-{name}"
            tickets_dict = {ticket: (ticket_user, "s1", ticket_expiry)}
            m.setattr(main, "ws_tickets", tickets_dict)

            recv_cancel_exc: list[asyncio.CancelledError] = []

            receive_fn = None
            if receive_fn_builder:
                receive_fn = receive_fn_builder(
                    child_started_event,
                    child_done_event,
                    child_release_event,
                    recv_cancel_exc,
                    receive_cancel_ready_event,
                )

            ws = MockWS("s1", ticket, tracker, receive_fn=receive_fn)

            endpoint_completed_auth = []
            endpoint_task_ref: list[asyncio.Task] = []

            async def run_endpoint_task():
                try:
                    return await main.websocket_endpoint(ws, "s1")
                finally:
                    endpoint_completed_auth.append(main.current_auth())

            try:
                endpoint_task = real_create_task(run_endpoint_task())
                endpoint_task_ref.append(endpoint_task)

                if cancellation_mode == "endpoint_cancel":
                    await wait_ev(child_started_event, "child_started")
                    await wait_ev(receive_cancel_ready_event, "receive_cancel_ready")
                    endpoint_task.cancel("ENDPOINT_CANCEL_UNIQUE")
                    await wait_endpoint_done(endpoint_task, "endpoint_cancel")
                    with pytest.raises(asyncio.CancelledError) as exc_info:
                        await endpoint_task
                    tracker["events"].append("endpoint_cancel_observed")
                    assert len(recv_cancel_exc) == 1
                    assert exc_info.value is recv_cancel_exc[0]
                    assert exc_info.value.args == ("ENDPOINT_CANCEL_UNIQUE",)
                elif cancellation_mode == "paused_finalizer_cancel":
                    await wait_ev(child_finalizer_entered_event, "child_finalizer_entered")
                    endpoint_task.cancel("PARENT_CANCEL_1")
                    await wait_ev(shield_entry_events[2], "shield_entry_2")
                    endpoint_task.cancel("PARENT_CANCEL_2")
                    await wait_ev(shield_entry_events[3], "shield_entry_3")
                    child_release_event.set()
                    await wait_endpoint_done(endpoint_task, "paused_finalizer_cancel")
                    with pytest.raises(asyncio.CancelledError) as exc_info:
                        await endpoint_task
                    tracker["events"].append("endpoint_cancel_observed")
                    assert len(shield_caught_cancels) == 2
                    assert exc_info.value is shield_caught_cancels[1]
                    assert shield_caught_cancels[0] is not shield_caught_cancels[1]
                    assert shield_caught_cancels[0].args == ("PARENT_CANCEL_1",)
                    assert shield_caught_cancels[1].args == ("PARENT_CANCEL_2",)
                elif cancellation_mode == "child_error_behind_cancel":
                    await wait_ev(child_error_entered_event, "child_error_entered")
                    endpoint_task.cancel("PARENT_CANCEL_BEHIND_CHILD_ERR")
                    await wait_ev(shield_entry_events[2], "shield_entry_2")
                    child_error_release_event.set()
                    await wait_endpoint_done(endpoint_task, "child_error_behind_cancel")
                    with pytest.raises(asyncio.CancelledError) as exc_info:
                        await endpoint_task
                    tracker["events"].append("endpoint_cancel_observed")
                    assert len(shield_caught_cancels) == 1
                    assert exc_info.value is shield_caught_cancels[0]
                    assert exc_info.value.args == ("PARENT_CANCEL_BEHIND_CHILD_ERR",)
                elif expect_exc is not None:
                    await wait_endpoint_done(endpoint_task, f"expect_exc_{name}")
                    with pytest.raises(BaseException) as exc_info:
                        await endpoint_task
                    assert exc_info.value is expect_exc, f"Expected exact exception object identity, got {exc_info.value!r}"
                else:
                    await wait_endpoint_done(endpoint_task, f"normal_success_{name}")
                    await endpoint_task

                assert endpoint_completed_auth == [baseline_user], f"Endpoint task did not restore baseline auth in {name}"
                assert main.current_auth() is baseline_user, f"Baseline auth not restored in scenario {name}"

                assert len(tracker["set_auth_calls"]) == 1, f"set_current_auth calls != 1 in {name}"
                set_record = tracker["set_auth_calls"][0]
                endpoint_token = set_record["token"]
                assert set_record["ctx"] is ticket_user
                assert len(tracker["reset_auth_calls"]) == 1, f"reset_current_auth calls != 1 in {name}: {tracker['reset_auth_calls']}"
                assert tracker["reset_auth_calls"][0]["token"] == endpoint_token
                assert tracker["reset_auth_calls"][0]["auth_before"] is ticket_user

                if expect_close_code is not None:
                    assert ws.closed == [{"code": expect_close_code}]

                pending_delta = {task for task in asyncio.all_tasks() - baseline_tasks if not task.done()}

                scenario_result = (
                    tracker,
                    manager,
                    ws,
                    created_proxies,
                    gather_calls,
                    gather_futures,
                    shield_entries,
                    shield_futures,
                    shield_caught_cancels,
                    logger_exceptions,
                    logger_warnings,
                    pending_delta,
                    ticket_expiry,
                )
            finally:
                child_release_event.set()
                child_error_release_event.set()
                try:
                    for p in created_proxies:
                        if not p.done():
                            p.cancel()
                    for ep in endpoint_task_ref:
                        if not ep.done():
                            ep.cancel()
                    current_pending = [task for task in asyncio.all_tasks() - baseline_tasks if not task.done()]
                    for t in current_pending:
                        t.cancel()
                    if current_pending:
                        done, pending = await asyncio.wait(set(current_pending), timeout=TIMEOUT)
                        for t in done:
                            if not t.cancelled():
                                try:
                                    t.exception()
                                except BaseException:
                                    pass
                        if pending:
                            raise AssertionError("EMERGENCY_CLEANUP_PENDING_TASKS_SURVIVED")
                    assert len([task for task in asyncio.all_tasks() - baseline_tasks if not task.done()]) == 0
                finally:
                    if orig_has_ready:
                        main.app.state.ready = orig_ready
                    elif hasattr(main.app.state, "ready"):
                        delattr(main.app.state, "ready")

                    main.reset_current_auth(baseline_token)

        assert hasattr(main.app.state, "ready") == orig_has_ready
        if orig_has_ready:
            assert getattr(main.app.state, "ready", None) == orig_ready
        assert main.settings is orig_settings
        assert main.ws_manager is orig_ws_manager
        assert main.ws_tickets is orig_ws_tickets
        assert dict(main.ws_tickets) == orig_ws_tickets_snapshot
        assert main.current_auth() is orig_current_auth
        assert scenario_result is not None, f"Scenario {name} did not assign result"

        return scenario_result

    # 1. Owner mismatch after set_current_auth
    async def read_mismatch(sid):
        return SimpleNamespace(owner_id="uid-other", org_id="ella-internal", status=SessionStatus.ACTIVE)

    t1, m1, ws1, p1, g1, gf1, s1, sf1, sc1, le1, lw1, pd1, exp1 = asyncio.run(
        run_scenario(
            "owner_mismatch",
            read_session_fn=read_mismatch,
            expect_close_code=1008,
        )
    )
    assert len(t1["read_session_calls"]) == 1
    assert t1["read_session_calls"][0]["session_id"] == "s1"
    assert t1["read_session_calls"][0]["auth"] is ticket_user
    assert len(t1["close_calls"]) == 1
    assert t1["close_calls"][0]["auth"] is ticket_user
    assert len(m1.connect_calls) == 0
    assert len(m1.disconnect_calls) == 0
    assert len(p1) == 0
    assert len(pd1) == 0
    assert g1 == 0 and s1 == 0 and len(gf1) == 0 and len(sf1) == 0
    assert le1 == [] and lw1 == []

    # 2. _read_session raises HTTPException
    async def read_http_err(sid):
        raise HTTPException(status_code=404, detail="Session not found")

    t2, m2, ws2, p2, g2, gf2, s2, sf2, sc2, le2, lw2, pd2, exp2 = asyncio.run(
        run_scenario(
            "read_session_http_error",
            read_session_fn=read_http_err,
            expect_close_code=1008,
        )
    )
    assert len(t2["read_session_calls"]) == 1
    assert t2["read_session_calls"][0]["session_id"] == "s1"
    assert t2["read_session_calls"][0]["auth"] is ticket_user
    assert len(t2["close_calls"]) == 1
    assert t2["close_calls"][0]["auth"] is ticket_user
    assert len(m2.connect_calls) == 0
    assert len(m2.disconnect_calls) == 0
    assert len(p2) == 0
    assert len(pd2) == 0
    assert g2 == 0 and s2 == 0 and len(gf2) == 0 and len(sf2) == 0
    assert le2 == [] and lw2 == []

    # 3. Partial ws_manager.connect BaseException failure
    async def read_ok(sid):
        return SimpleNamespace(owner_id="uid-ticket", org_id="ella-internal", status=SessionStatus.ACTIVE)

    injected_connect_err = CustomBaseException("CONNECT_BASE_EXCEPTION_SENTINEL")
    t3, m3, ws3, p3, g3, gf3, s3, sf3, sc3, le3, lw3, pd3, exp3 = asyncio.run(
        run_scenario(
            "connect_failure",
            read_session_fn=read_ok,
            connect_side_effect=injected_connect_err,
            expect_exc=injected_connect_err,
        )
    )
    assert len(t3["read_session_calls"]) == 1
    assert t3["read_session_calls"][0]["session_id"] == "s1"
    assert t3["read_session_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m3, ws3, ticket_user)
    assert len(p3) == 0
    assert len(pd3) == 0
    assert g3 == 0 and s3 == 0 and len(gf3) == 0 and len(sf3) == 0
    assert le3 == [] and lw3 == []

    # 4. asyncio.create_task BaseException failure
    injected_create_err = CustomBaseException("CREATE_TASK_BASE_EXCEPTION_SENTINEL")
    t4, m4, ws4, p4, g4, gf4, s4, sf4, sc4, le4, lw4, pd4, exp4 = asyncio.run(
        run_scenario(
            "create_task_failure",
            read_session_fn=read_ok,
            create_task_side_effect=injected_create_err,
            expect_exc=injected_create_err,
        )
    )
    assert len(t4["read_session_calls"]) == 1
    assert t4["read_session_calls"][0]["session_id"] == "s1"
    assert t4["read_session_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m4, ws4, ticket_user)
    assert t4["captured_coroutine"].cr_frame is None, "Production did not close unsubmitted expiry coroutine"
    assert len(p4) == 0
    assert len(pd4) == 0
    assert g4 == 0 and s4 == 0 and len(gf4) == 0 and len(sf4) == 0
    assert le4 == [] and lw4 == []

    # 5. Normal WebSocketDisconnect
    def normal_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(start_ev, "normal_recv_start")
            raise WebSocketDisconnect(1000)
        return _recv

    t5, m5, ws5, p5, g5, gf5, s5, sf5, sc5, le5, lw5, pd5, exp5 = asyncio.run(
        run_scenario(
            "normal_disconnect",
            read_session_fn=read_ok,
            child_coro_behavior="block",
            receive_fn_builder=normal_recv,
        )
    )
    assert len(t5["read_session_calls"]) == 1
    assert t5["read_session_calls"][0]["session_id"] == "s1"
    assert t5["read_session_calls"][0]["auth"] is ticket_user
    assert len(t5["child_coro_calls"]) == 1
    assert t5["child_coro_calls"][0]["ws"] is ws5
    assert t5["child_coro_calls"][0]["exp"] is exp5
    assert t5["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m5, ws5, ticket_user)
    assert len(p5) == 1
    assert p5[0].cancel_count == 1
    assert len(t5["cancel_calls"]) == 1
    assert t5["cancel_calls"][0]["args"] == () and t5["cancel_calls"][0]["kwargs"] == {}
    assert t5["cancel_calls"][0]["auth"] is ticket_user
    assert p5[0].await_count == 1
    assert p5[0].done() is True
    assert p5[0].cancelled() is True
    assert g5 == 1 and gf5[0].done() is True
    assert s5 == 1 and all(f.done() for f in sf5)
    assert len(pd5) == 0
    assert t5["child_start_auth"] == [ticket_user]
    assert t5["cancel_auth"] == [ticket_user]
    assert t5["await_auth"] == [ticket_user]
    assert t5["child_finally_auth"] == [ticket_user]
    assert le5 == [] and lw5 == []
    events = t5["events"]
    assert (
        events.index("create_task")
        < events.index("proxy_cancel")
        < events.index("gather_called")
    )
    assert (
        events.index("child_finally_entered")
        < events.index("child_finally_done")
        < events.index("ws_disconnect")
        < events.index("reset_current_auth")
    )

    # 6. Unexpected receive-loop Exception
    def error_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(start_ev, "error_recv_start")
            raise RuntimeError("UNEXPECTED_RECV_ERR")
        return _recv

    t6, m6, ws6, p6, g6, gf6, s6, sf6, sc6, le6, lw6, pd6, exp6 = asyncio.run(
        run_scenario(
            "unexpected_receive_error",
            read_session_fn=read_ok,
            child_coro_behavior="block",
            receive_fn_builder=error_recv,
        )
    )
    assert len(t6["read_session_calls"]) == 1
    assert t6["read_session_calls"][0]["session_id"] == "s1"
    assert t6["read_session_calls"][0]["auth"] is ticket_user
    assert len(t6["child_coro_calls"]) == 1
    assert t6["child_coro_calls"][0]["ws"] is ws6
    assert t6["child_coro_calls"][0]["exp"] is exp6
    assert t6["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m6, ws6, ticket_user)
    assert len(p6) == 1
    assert p6[0].cancel_count == 1
    assert len(t6["cancel_calls"]) == 1
    assert t6["cancel_calls"][0]["args"] == () and t6["cancel_calls"][0]["kwargs"] == {}
    assert t6["cancel_calls"][0]["auth"] is ticket_user
    assert p6[0].await_count == 1
    assert p6[0].done() is True
    assert p6[0].cancelled() is True
    assert g6 == 1 and gf6[0].done() is True
    assert s6 == 1 and all(f.done() for f in sf6)
    assert len(pd6) == 0
    assert le6 == [{"event": "ws_error", "args": (), "kwargs": {"session_id": "s1"}}]
    assert lw6 == []

    # 7. Endpoint task cancellation
    def blocking_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(start_ev, "blocking_recv_start")
            try:
                receive_cancel_ready_event.set()
                while True:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError as exc:
                cancel_holder.append(exc)
                raise
        return _recv

    t7, m7, ws7, p7, g7, gf7, s7, sf7, sc7, le7, lw7, pd7, exp7 = asyncio.run(
        run_scenario(
            "cancellation",
            read_session_fn=read_ok,
            child_coro_behavior="block",
            receive_fn_builder=blocking_recv,
            cancellation_mode="endpoint_cancel",
        )
    )
    assert len(t7["read_session_calls"]) == 1
    assert t7["read_session_calls"][0]["session_id"] == "s1"
    assert t7["read_session_calls"][0]["auth"] is ticket_user
    assert len(t7["child_coro_calls"]) == 1
    assert t7["child_coro_calls"][0]["ws"] is ws7
    assert t7["child_coro_calls"][0]["exp"] is exp7
    assert t7["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m7, ws7, ticket_user)
    assert len(p7) == 1
    assert p7[0].cancel_count == 1
    assert len(t7["cancel_calls"]) == 1
    assert t7["cancel_calls"][0]["args"] == () and t7["cancel_calls"][0]["kwargs"] == {}
    assert t7["cancel_calls"][0]["auth"] is ticket_user
    assert p7[0].await_count == 1
    assert p7[0].done() is True
    assert p7[0].cancelled() is True
    assert g7 == 1 and gf7[0].done() is True
    assert s7 == 1 and all(f.done() for f in sf7)
    assert len(pd7) == 0
    assert t7["child_start_auth"] == [ticket_user]
    assert t7["cancel_auth"] == [ticket_user]
    assert t7["await_auth"] == [ticket_user]
    assert t7["child_finally_auth"] == [ticket_user]
    assert le7 == [] and lw7 == []
    events = t7["events"]
    assert (
        events.index("child_finally_done")
        < events.index("ws_disconnect")
        < events.index("reset_current_auth")
        < events.index("endpoint_cancel_observed")
    )

    # 8. Cancellation delivered twice during paused child finalizer
    def pause_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(start_ev, "pause_recv_start")
            raise WebSocketDisconnect(1000)
        return _recv

    t8, m8, ws8, p8, g8, gf8, s8, sf8, sc8, le8, lw8, pd8, exp8 = asyncio.run(
        run_scenario(
            "paused_finalizer_cancellation",
            read_session_fn=read_ok,
            child_coro_behavior="pause_finalizer",
            receive_fn_builder=pause_recv,
            cancellation_mode="paused_finalizer_cancel",
        )
    )
    assert len(t8["read_session_calls"]) == 1
    assert t8["read_session_calls"][0]["session_id"] == "s1"
    assert t8["read_session_calls"][0]["auth"] is ticket_user
    assert len(t8["child_coro_calls"]) == 1
    assert t8["child_coro_calls"][0]["ws"] is ws8
    assert t8["child_coro_calls"][0]["exp"] is exp8
    assert t8["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m8, ws8, ticket_user)
    assert len(p8) == 1
    assert p8[0].done() is True
    assert p8[0].cancelled() is True
    assert p8[0].cancel_count == 1
    assert len(t8["cancel_calls"]) == 1
    assert t8["cancel_calls"][0]["args"] == () and t8["cancel_calls"][0]["kwargs"] == {}
    assert t8["cancel_calls"][0]["auth"] is ticket_user
    assert p8[0].await_count == 1
    assert g8 == 1 and gf8[0].done() is True
    assert s8 == 3 and all(f.done() for f in sf8)
    assert len(pd8) == 0
    assert t8["child_start_auth"] == [ticket_user]
    assert t8["cancel_auth"] == [ticket_user]
    assert t8["await_auth"] == [ticket_user]
    assert t8["child_finally_auth"] == [ticket_user]
    assert le8 == [] and lw8 == []
    events = t8["events"]
    assert (
        events.index("child_finally_entered")
        < events.index("child_finally_done")
        < events.index("ws_disconnect")
        < events.index("reset_current_auth")
        < events.index("endpoint_cancel_observed")
    )

    # 9. Already-done child success
    def done_success_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(done_ev, "done_success_recv")
            raise WebSocketDisconnect(1000)
        return _recv

    t9, m9, ws9, p9, g9, gf9, s9, sf9, sc9, le9, lw9, pd9, exp9 = asyncio.run(
        run_scenario(
            "already_done_success",
            read_session_fn=read_ok,
            child_coro_behavior="done_success",
            receive_fn_builder=done_success_recv,
        )
    )
    assert len(t9["read_session_calls"]) == 1
    assert t9["read_session_calls"][0]["session_id"] == "s1"
    assert t9["read_session_calls"][0]["auth"] is ticket_user
    assert len(t9["child_coro_calls"]) == 1
    assert t9["child_coro_calls"][0]["ws"] is ws9
    assert t9["child_coro_calls"][0]["exp"] is exp9
    assert t9["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m9, ws9, ticket_user)
    assert len(p9) == 1
    assert p9[0].done() is True
    assert p9[0].cancelled() is False
    assert p9[0].cancel_count == 1
    assert len(t9["cancel_calls"]) == 1
    assert t9["cancel_calls"][0]["args"] == () and t9["cancel_calls"][0]["kwargs"] == {}
    assert t9["cancel_calls"][0]["auth"] is ticket_user
    assert p9[0].await_count == 1
    assert g9 == 1 and gf9[0].done() is True
    assert s9 == 1 and all(f.done() for f in sf9)
    assert len(pd9) == 0
    assert le9 == [] and lw9 == []

    # 10. Already-done child cancellation
    def done_cancel_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(done_ev, "done_cancel_recv")
            raise WebSocketDisconnect(1000)
        return _recv

    t10, m10, ws10, p10, g10, gf10, s10, sf10, sc10, le10, lw10, pd10, exp10 = asyncio.run(
        run_scenario(
            "already_done_cancelled",
            read_session_fn=read_ok,
            child_coro_behavior="done_cancel",
            receive_fn_builder=done_cancel_recv,
        )
    )
    assert len(t10["read_session_calls"]) == 1
    assert t10["read_session_calls"][0]["session_id"] == "s1"
    assert t10["read_session_calls"][0]["auth"] is ticket_user
    assert len(t10["child_coro_calls"]) == 1
    assert t10["child_coro_calls"][0]["ws"] is ws10
    assert t10["child_coro_calls"][0]["exp"] is exp10
    assert t10["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m10, ws10, ticket_user)
    assert len(p10) == 1
    assert p10[0].done() is True
    assert p10[0].cancelled() is True
    assert p10[0].cancel_count == 1
    assert len(t10["cancel_calls"]) == 1
    assert t10["cancel_calls"][0]["args"] == () and t10["cancel_calls"][0]["kwargs"] == {}
    assert t10["cancel_calls"][0]["auth"] is ticket_user
    assert p10[0].await_count == 1
    assert g10 == 1 and gf10[0].done() is True
    assert s10 == 1 and all(f.done() for f in sf10)
    assert len(pd10) == 0
    assert le10 == [] and lw10 == []

    # 11. Already-done child non-cancellation exception
    injected_child_err = RuntimeError("CHILD_ERR_SENTINEL_EXACT")

    def done_err_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(done_ev, "done_err_recv")
            raise WebSocketDisconnect(1000)
        return _recv

    t11, m11, ws11, p11, g11, gf11, s11, sf11, sc11, le11, lw11, pd11, exp11 = asyncio.run(
        run_scenario(
            "already_done_child_err",
            read_session_fn=read_ok,
            child_coro_behavior="done_error",
            child_injected_error=injected_child_err,
            receive_fn_builder=done_err_recv,
            expect_exc=injected_child_err,
        )
    )
    assert len(t11["read_session_calls"]) == 1
    assert t11["read_session_calls"][0]["session_id"] == "s1"
    assert t11["read_session_calls"][0]["auth"] is ticket_user
    assert len(t11["child_coro_calls"]) == 1
    assert t11["child_coro_calls"][0]["ws"] is ws11
    assert t11["child_coro_calls"][0]["exp"] is exp11
    assert t11["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m11, ws11, ticket_user)
    assert len(p11) == 1
    assert p11[0].done() is True
    assert p11[0].cancelled() is False
    assert p11[0].cancel_count == 1
    assert len(t11["cancel_calls"]) == 1
    assert t11["cancel_calls"][0]["args"] == () and t11["cancel_calls"][0]["kwargs"] == {}
    assert t11["cancel_calls"][0]["auth"] is ticket_user
    assert p11[0].await_count == 1
    assert g11 == 1 and gf11[0].done() is True
    assert s11 == 1 and all(f.done() for f in sf11)
    assert len(pd11) == 0
    assert le11 == [] and lw11 == []

    # 12. Disconnect Exception does not mask active primary
    injected_primary_err = CustomBaseException("PRIMARY_CONNECT_BASE_EXC")
    injected_disconnect_err = RuntimeError("DISCONNECT_CLEANUP_ERR")
    t12, m12, ws12, p12, g12, gf12, s12, sf12, sc12, le12, lw12, pd12, exp12 = asyncio.run(
        run_scenario(
            "disconnect_error_masking",
            read_session_fn=read_ok,
            connect_side_effect=injected_primary_err,
            disconnect_side_effect=injected_disconnect_err,
            expect_exc=injected_primary_err,
        )
    )
    assert len(t12["read_session_calls"]) == 1
    assert t12["read_session_calls"][0]["session_id"] == "s1"
    assert t12["read_session_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m12, ws12, ticket_user)
    assert len(p12) == 0
    assert len(pd12) == 0
    assert lw12 == [{"event": "ws_cleanup_disconnect_error", "args": (), "kwargs": {}}]
    assert le12 == []
    assert g12 == 0 and s12 == 0 and len(gf12) == 0 and len(sf12) == 0

    # 13. Active primary plus child non-cancellation error
    injected_primary_13 = CustomBaseException("PRIMARY_FAIL_13")
    injected_child_13 = CustomBaseException("CHILD_FAIL_13")

    def error_primary_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(start_ev, "primary_error_recv_start")
            raise injected_primary_13
        return _recv

    t13, m13, ws13, p13, g13, gf13, s13, sf13, sc13, le13, lw13, pd13, exp13 = asyncio.run(
        run_scenario(
            "active_primary_plus_child_error",
            read_session_fn=read_ok,
            child_coro_behavior="catch_cancel_and_error",
            child_injected_error=injected_child_13,
            receive_fn_builder=error_primary_recv,
            expect_exc=injected_primary_13,
        )
    )
    assert len(t13["read_session_calls"]) == 1
    assert t13["read_session_calls"][0]["session_id"] == "s1"
    assert t13["read_session_calls"][0]["auth"] is ticket_user
    assert len(t13["child_coro_calls"]) == 1
    assert t13["child_coro_calls"][0]["ws"] is ws13
    assert t13["child_coro_calls"][0]["exp"] is exp13
    assert t13["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m13, ws13, ticket_user)
    assert len(p13) == 1
    assert p13[0].done() is True
    assert p13[0].cancelled() is False
    assert p13[0].exception() is injected_child_13
    assert p13[0].cancel_count == 1
    assert len(t13["cancel_calls"]) == 1
    assert t13["cancel_calls"][0]["args"] == () and t13["cancel_calls"][0]["kwargs"] == {}
    assert t13["cancel_calls"][0]["auth"] is ticket_user
    assert p13[0].await_count == 1
    assert g13 == 1 and gf13[0].done() is True
    assert s13 == 1 and all(f.done() for f in sf13)
    assert len(pd13) == 0
    assert t13["child_start_auth"] == [ticket_user]
    assert t13["cancel_auth"] == [ticket_user]
    assert t13["await_auth"] == [ticket_user]
    assert t13["child_finally_auth"] == [ticket_user]
    assert lw13 == [{"event": "ws_cleanup_task_error", "args": (), "kwargs": {}}]
    assert le13 == []
    events = t13["events"]
    assert (
        events.index("child_finally_done")
        < events.index("ws_disconnect")
        < events.index("reset_current_auth")
    )

    # 14. Child error behind parent cancellation
    injected_child_14 = CustomBaseException("CHILD_FAIL_14")

    def pause_then_error_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(start_ev, "pause_then_error_recv_start")
            raise WebSocketDisconnect(1000)
        return _recv

    t14, m14, ws14, p14, g14, gf14, s14, sf14, sc14, le14, lw14, pd14, exp14 = asyncio.run(
        run_scenario(
            "child_error_behind_parent_cancellation",
            read_session_fn=read_ok,
            child_coro_behavior="pause_then_error_on_cancel",
            child_injected_error=injected_child_14,
            receive_fn_builder=pause_then_error_recv,
            cancellation_mode="child_error_behind_cancel",
        )
    )
    assert len(t14["read_session_calls"]) == 1
    assert t14["read_session_calls"][0]["session_id"] == "s1"
    assert t14["read_session_calls"][0]["auth"] is ticket_user
    assert len(t14["child_coro_calls"]) == 1
    assert t14["child_coro_calls"][0]["ws"] is ws14
    assert t14["child_coro_calls"][0]["exp"] is exp14
    assert t14["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m14, ws14, ticket_user)
    assert len(p14) == 1
    assert p14[0].done() is True
    assert p14[0].cancelled() is False
    assert p14[0].exception() is injected_child_14
    assert p14[0].cancel_count == 1
    assert len(t14["cancel_calls"]) == 1
    assert t14["cancel_calls"][0]["args"] == () and t14["cancel_calls"][0]["kwargs"] == {}
    assert t14["cancel_calls"][0]["auth"] is ticket_user
    assert p14[0].await_count == 1
    assert g14 == 1 and gf14[0].done() is True
    assert s14 == 2 and all(f.done() for f in sf14)
    assert len(pd14) == 0
    assert t14["child_start_auth"] == [ticket_user]
    assert t14["cancel_auth"] == [ticket_user]
    assert t14["await_auth"] == [ticket_user]
    assert t14["child_finally_auth"] == [ticket_user]
    assert lw14 == [{"event": "ws_cleanup_task_error", "args": (), "kwargs": {}}]
    assert le14 == []
    events = t14["events"]
    assert (
        events.index("child_finally_entered")
        < events.index("child_finally_done")
        < events.index("ws_disconnect")
        < events.index("reset_current_auth")
        < events.index("endpoint_cancel_observed")
    )

    # 15. Active endpoint cancellation plus child non-cancellation error
    injected_child_15 = CustomBaseException("CHILD_FAIL_15")

    def blocking_then_child_error_recv(start_ev, done_ev, rel_ev, cancel_holder, receive_cancel_ready_event):
        async def _recv():
            await wait_ev(start_ev, "blocking_recv_15_start")
            try:
                receive_cancel_ready_event.set()
                while True:
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError as exc:
                cancel_holder.append(exc)
                raise
        return _recv

    t15, m15, ws15, p15, g15, gf15, s15, sf15, sc15, le15, lw15, pd15, exp15 = asyncio.run(
        run_scenario(
            "active_endpoint_cancellation_plus_child_error",
            read_session_fn=read_ok,
            child_coro_behavior="catch_cancel_and_error",
            child_injected_error=injected_child_15,
            receive_fn_builder=blocking_then_child_error_recv,
            cancellation_mode="endpoint_cancel",
        )
    )
    assert len(t15["read_session_calls"]) == 1
    assert t15["read_session_calls"][0]["session_id"] == "s1"
    assert t15["read_session_calls"][0]["auth"] is ticket_user
    assert len(t15["child_coro_calls"]) == 1
    assert t15["child_coro_calls"][0]["ws"] is ws15
    assert t15["child_coro_calls"][0]["exp"] is exp15
    assert t15["child_coro_calls"][0]["auth"] is ticket_user
    assert_connected_ws(m15, ws15, ticket_user)
    assert len(p15) == 1
    assert p15[0].done() is True
    assert p15[0].cancelled() is False
    assert p15[0].exception() is injected_child_15
    assert p15[0].cancel_count == 1
    assert len(t15["cancel_calls"]) == 1
    assert t15["cancel_calls"][0]["args"] == () and t15["cancel_calls"][0]["kwargs"] == {}
    assert t15["cancel_calls"][0]["auth"] is ticket_user
    assert p15[0].await_count == 1
    assert g15 == 1 and gf15[0].done() is True
    assert s15 == 1 and all(f.done() for f in sf15)
    assert len(pd15) == 0
    assert t15["child_start_auth"] == [ticket_user]
    assert t15["cancel_auth"] == [ticket_user]
    assert t15["await_auth"] == [ticket_user]
    assert t15["child_finally_auth"] == [ticket_user]
    assert lw15 == [{"event": "ws_cleanup_task_error", "args": (), "kwargs": {}}]
    assert le15 == []
    events = t15["events"]
    assert (
        events.index("child_finally_done")
        < events.index("ws_disconnect")
        < events.index("reset_current_auth")
        < events.index("endpoint_cancel_observed")
    )
