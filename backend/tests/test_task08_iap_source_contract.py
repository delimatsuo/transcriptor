from __future__ import annotations

import json
from pathlib import Path

import pytest

CONTRACT = Path(__file__).parents[2] / "docs" / "launch" / "task-08-iap-source-contract.json"


def test_task08_contract_binds_private_topology_and_exact_origins():
    contract = json.loads(CONTRACT.read_text())
    assert contract["status"] == "SOURCE_ONLY_NON_AUTHORIZING"
    assert contract["provider_state"] is False
    assert contract["topology"]["frontend"]["origin"] == "https://tars.ellaexecutivesearch.com"
    assert contract["topology"]["api"]["origin"] == "https://api.tars.ellaexecutivesearch.com"
    assert contract["origins"]["api_wss"] == "wss://api.tars.ellaexecutivesearch.com"
    assert contract["origins"]["ws_path"] == "/ws"
    assert contract["origins"]["native_stream_path"] == "/api/stream/native"
    assert contract["cloud_run_iam"]["public_principal"] is False
    assert contract["cloud_run_iam"]["allowed_invoker_count"] == 1
    assert len(contract["cloud_run_iam"]["allowed_invoker_principals"]) == 1
    assert contract["cloud_run_iam"]["invoker_role"] == "roles/run.invoker"
    assert contract["cloud_run_iam"]["allowed_invoker_principals"] == [
        "service-PROJECT_NUMBER@gcp-sa-iap.iam.gserviceaccount.com"
    ]
    assert contract["admission"] == {
        "exact_unique_address_count": 5,
        "required_domain": "ellaexecutivesearch.com",
        "server_derived_org_id": "ella-internal",
        "operator_set_nonempty_subset": True,
    }
    assert contract["cors"]["application_allowed_origins"] == [
        "https://tars.ellaexecutivesearch.com"
    ]
    assert contract["cors"]["provider_iap_access_settings"] == {
        "allow_http_options": True,
        "configured": False,
        "proven": False,
        "authorized_by_source_task": False,
    }


def test_task08_contract_binds_signed_gcip_and_socket_lifecycle():
    contract = json.loads(CONTRACT.read_text())
    iap = contract["iap"]
    assert iap["issuer"] == "https://cloud.google.com/iap"
    assert iap["audience_template"] == "/projects/PROJECT_NUMBER/locations/REGION/services/SERVICE_NAME"
    assert iap["gcip"]["encoding"] == "bounded JSON string"
    assert iap["gcip"]["duplicate_keys"] == "reject"
    assert iap["gcip"]["firebase"] == {"sign_in_provider": "google.com"}
    assert iap["gcip"]["top_level_provider_accepted"] is False
    socket = contract["websocket"]
    assert socket["absolute_max_lifetime_seconds"] == 3300
    assert socket["fresh_http_ticket_on_reconnect"] is True
    assert socket["fresh_iap_assertion_on_reconnect"] is True
    assert socket["ping_extends_deadline"] is False


def test_task08_contract_binds_frontend_terminal_lifecycle_exactly():
    contract = json.loads(CONTRACT.read_text())
    assert contract["frontend_terminal_lifecycle"] == {
        "terminal_http_statuses": [401, 403],
        "authenticated_data_unmount_required": True,
        "logout_max_wait_ms": 5000,
        "stale_attempt_fence_required": True,
        "late_media_disposal_required": True,
        "retryable_failures": ["5xx", "network"],
    }


def test_task08_provider_single_instance_gates_remain_unconfigured_unproven():
    contract = json.loads(CONTRACT.read_text())
    gates = contract["provider_gates"]
    assert gates["cloud_run_max_instances"]["required_value"] == 1
    assert gates["serving_revision_count"]["required_value"] == 1
    assert gates["traffic"]["required_unsplit_percent"] == 100
    assert gates["session_affinity"]["required"] is True
    for gate in gates.values():
        assert gate["configured"] is False
        assert gate["proven"] is False
        assert gate["source_authorized"] is False
        assert gate["authorized_by_source_task"] is False


def test_task08_contract_requires_all_zero_categories_and_rollback_categories():
    contract = json.loads(CONTRACT.read_text())
    assert contract["zero_active_categories"] == [
        "active_business_sessions",
        "registered_browser_sockets",
        "outstanding_browser_tickets",
        "active_stream_keys",
        "active_provider_operations",
    ]
    required = {
        "frontend_release", "cloud_run_artifact", "cloud_run_revision", "cloud_run_traffic",
        "iam", "iap", "identity_platform", "origins_dns_tls", "api_key_restrictions",
        "runtime_configuration", "budgets", "quotas", "kill_switch", "evidence",
    }
    assert required.issubset(contract["rollback_categories"])
    assert contract["rollback_manifest_required"] is True
    assert contract["cost_proposal"] == {
        "currency": "BRL",
        "monthly_amount": 250,
        "kind": "alert_only_proposal",
        "hard_spending_limit": False,
        "provider_state": False,
        "thresholds_percent": [50, 90, 100],
        "recipient": "deli@ellaexecutivesearch.com",
    }
    assert contract["evidence_ceiling"]["native_desktop"] == "incompatible with this IAP candidate and remains unqualified"


def _validate_rollback_manifest(contract: dict) -> None:
    required = {
        "frontend_release", "cloud_run_artifact", "cloud_run_revision", "cloud_run_traffic",
        "iam", "iap", "identity_platform", "origins_dns_tls", "api_key_restrictions",
        "runtime_configuration", "budgets", "quotas", "kill_switch", "evidence",
    }
    assert required.issubset(contract["rollback_categories"])
    assert contract["rollback_manifest_required"] is True


def test_each_required_rollback_category_is_individually_required():
    contract = json.loads(CONTRACT.read_text())
    required = {
        "frontend_release", "cloud_run_artifact", "cloud_run_revision", "cloud_run_traffic",
        "iam", "iap", "identity_platform", "origins_dns_tls", "api_key_restrictions",
        "runtime_configuration", "budgets", "quotas", "kill_switch", "evidence",
    }
    _validate_rollback_manifest(contract)
    for omitted in sorted(required):
        candidate = {
            **contract,
            "rollback_categories": [
                item for item in contract["rollback_categories"] if item != omitted
            ],
        }
        with pytest.raises(AssertionError):
            _validate_rollback_manifest(candidate)
