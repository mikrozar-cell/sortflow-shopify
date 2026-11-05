"""
Automated Verification Suite for SortFlow Pro (Shopify App).
Tests:
  1. Pinned + In-Stock + Auto-Demoted Out-of-Stock sorting engine
  2. Restock Memory™ (product sold out -> demoted to bottom -> restocked -> restored to exact rank)
  3. New Arrival placement (placed right after Pinned items, never below Out-of-Stock)
  4. Automatic switch of collection sortOrder from BEST_SELLING to MANUAL
  5. 1-Click Snapshot Rollback / Undo
  6. Shopify Webhook HMAC-SHA256 verification (GDPR & Uninstall webhooks)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
from pathlib import Path

# Point SQLite DB to a clean temporary file for isolated tests
TEMP_DB = Path(tempfile.gettempdir()) / "test_sortflow_shopify.sqlite"
if TEMP_DB.exists():
    TEMP_DB.unlink()
os.environ["SORTFLOW_DB_PATH"] = str(TEMP_DB)

from fastapi.testclient import TestClient
from app.database import get_collection_rule, init_db
from app.server import SHOPIFY_API_SECRET, app, reset_demo_state


def compute_hmac(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def run_all_tests() -> None:
    init_db()
    reset_demo_state()
    client = TestClient(app)

    # 1. Verify UI & Collections List
    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "SortFlow Pro" in r_index.text

    r_cols = client.get("/api/collections?shop=demo-store.myshopify.com")
    assert r_cols.status_code == 200
    cols = r_cols.json()["collections"]
    assert len(cols) == 2
    assert cols[0]["id"] == "col_summer_2026"
    assert cols[0]["sortOrder"] == "BEST_SELLING"

    # 2. Fetch initial products for col_summer_2026
    r_prods = client.get("/api/collections/col_summer_2026/products?shop=demo-store.myshopify.com")
    assert r_prods.status_code == 200
    prods = r_prods.json()["products"]
    assert len(prods) == 8
    # Out-of-stock items (prod_102 and prod_105) must be automatically demoted to the bottom (#7 and #8)
    assert prods[-2]["product_id"] == "prod_102"
    assert prods[-1]["product_id"] == "prod_105"
    assert not prods[-1]["is_in_stock"]

    # 3. Pin 'prod_106' to #1 and Save Order -> verify sortOrder switches from BEST_SELLING to MANUAL
    save_items = []
    # Put prod_106 first and pinned
    reordered = [p for p in prods if p["product_id"] == "prod_106"] + [
        p for p in prods if p["product_id"] != "prod_106"
    ]
    for idx, p in enumerate(reordered):
        save_items.append(
            {
                "product_id": p["product_id"],
                "position": (idx + 1) * 10,
                "is_pinned": p["product_id"] == "prod_106",
            }
        )

    r_save = client.post(
        "/api/collections/col_summer_2026/save?shop=demo-store.myshopify.com",
        json={
            "auto_demote_oos": True,
            "restore_on_restock": True,
            "new_products_position": "after_pinned",
            "items": save_items,
        },
    )
    assert r_save.status_code == 200
    save_data = r_save.json()
    assert save_data["sort_order_switched_to_manual"] is True
    assert save_data["products"][0]["product_id"] == "prod_106"
    assert save_data["products"][0]["is_pinned"] is True

    # 4. Test Restock Memory™:
    # Currently prod_101 is at Rank #2 (right after pinned prod_106).
    assert save_data["products"][1]["product_id"] == "prod_101"
    # Simulate prod_101 selling out (0 inventory)
    r_sellout = client.post(
        "/api/collections/col_summer_2026/simulate-stock?shop=demo-store.myshopify.com",
        json={"product_id": "prod_101", "new_inventory": 0},
    )
    assert r_sellout.status_code == 200
    after_sellout = r_sellout.json()["products"]
    # prod_101 should now be demoted into the OOS section at the bottom (last 3 items)
    oos_ids = [p["product_id"] for p in after_sellout[-3:]]
    assert "prod_101" in oos_ids

    # Now simulate restocking prod_101 (+30 units) -> Restock Memory™ must return it to Rank #2!
    r_restock = client.post(
        "/api/collections/col_summer_2026/simulate-stock?shop=demo-store.myshopify.com",
        json={"product_id": "prod_101", "new_inventory": 30},
    )
    assert r_restock.status_code == 200
    after_restock = r_restock.json()["products"]
    assert after_restock[0]["product_id"] == "prod_106"  # Pinned #1
    assert after_restock[1]["product_id"] == "prod_101"  # Restored to #2!

    # 5. Test New Product Placement ('after_pinned'):
    # Adding a new product should place it right after pinned prod_106 (i.e. above OOS items!)
    r_new = client.post(
        "/api/collections/col_summer_2026/simulate-new-product?shop=demo-store.myshopify.com",
        json={
            "title": "Test Capsule Jacket",
            "sku": "TC-JKT-01",
            "price": "199.00",
            "inventory": 20,
        },
    )
    assert r_new.status_code == 200
    new_id = r_new.json()["new_product_id"]
    after_new = r_new.json()["products"]
    # Index 0 is pinned prod_106, Index 1 should be the newly added product!
    assert after_new[0]["product_id"] == "prod_106"
    assert after_new[1]["product_id"] == new_id

    # 6. Test 1-Click Snapshot Undo / Restore
    r_undo = client.post(
        "/api/collections/col_summer_2026/restore-snapshot?shop=demo-store.myshopify.com"
    )
    assert r_undo.status_code == 200
    undo_data = r_undo.json()
    restored = undo_data["products"]
    assert restored[0]["product_id"] == "prod_101"
    assert restored[1]["product_id"] == "prod_102"
    assert undo_data["rule"]["auto_demote_oos"] is True

    rule_after_undo = get_collection_rule("demo-store.myshopify.com", "col_summer_2026")
    assert rule_after_undo["auto_demote_oos"] == 1

    # 7. Test Mandatory GDPR & Inventory Webhooks with HMAC verification
    webhook_body = json.dumps({"id": 99999, "shop_domain": "demo-store.myshopify.com"}).encode("utf-8")
    # Invalid HMAC must be rejected with 401
    r_bad_hmac = client.post(
        "/webhooks/gdpr",
        content=webhook_body,
        headers={
            "X-Shopify-Hmac-Sha256": "invalid_signature",
            "X-Shopify-Topic": "customers/data_request",
        },
    )
    assert r_bad_hmac.status_code == 401

    # Valid HMAC must return 200
    valid_sig = compute_hmac(webhook_body, SHOPIFY_API_SECRET)
    r_good_hmac = client.post(
        "/webhooks/gdpr",
        content=webhook_body,
        headers={
            "X-Shopify-Hmac-Sha256": valid_sig,
            "X-Shopify-Topic": "customers/data_request",
            "X-Shopify-Shop-Domain": "demo-store.myshopify.com",
        },
    )
    assert r_good_hmac.status_code == 200
    assert r_good_hmac.json()["status"] == "gdpr_acknowledged"

    # 8. Test Live Store OAuth Redirection & 401 Guard (No demo fallback for live stores!)
    r_live_root = client.get("/?shop=reviewer-test-store.myshopify.com")
    assert r_live_root.status_code == 200
    assert "Authenticating SortFlow with your Shopify store" in r_live_root.text
    assert "/auth?shop=reviewer-test-store.myshopify.com" in r_live_root.text

    r_live_api = client.get("/api/collections?shop=reviewer-test-store.myshopify.com")
    assert r_live_api.status_code == 401
    assert "unauthorized" in r_live_api.json()["detail"]["error"]

    print("ALL 8 TEST SUITES PASSED 100%!")


if __name__ == "__main__":
    run_all_tests()

