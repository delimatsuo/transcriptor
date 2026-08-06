from backend.scripts.inventory_legacy_scope import (
    INVENTORY_VERSION,
    build_scope_inventory,
)


def test_scope_inventory_is_versioned_stable_and_content_free():
    inventory = build_scope_inventory(
        [
            {
                "id": "owned",
                "ownerId": "uid-a",
                "orgId": "ella-internal",
                "status": "completed",
                "startedAt": "2026-08-06T10:00:00Z",
                "transcript": [{"text": "candidate content must not be copied"}],
                "title": "candidate name must not be copied",
            },
            {
                "id": "missing-org",
                "ownerId": "uid-a",
                "orgId": "",
                "status": "active",
            },
            {"id": "missing-both", "status": "completed"},
        ]
    )

    assert inventory["version"] == INVENTORY_VERSION
    assert inventory["mutation"] == "none"
    assert inventory["total"] == 3
    assert inventory["ownedCount"] == 1
    assert inventory["unownedCount"] == 2
    assert [item["id"] for item in inventory["unowned"]] == [
        "missing-both",
        "missing-org",
    ]
    assert "candidate content" not in repr(inventory)
    assert "candidate name" not in repr(inventory)


def test_scope_inventory_treats_whitespace_scope_as_unowned():
    inventory = build_scope_inventory(
        [{"id": "spaces", "ownerId": "  ", "orgId": "ella-internal"}]
    )

    assert inventory["ownedCount"] == 0
    assert inventory["unowned"][0]["ownerId"] is None
