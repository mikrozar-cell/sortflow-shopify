"""
SortFlow Pro for Shopify — FastAPI Backend Server & Local Demo Sandbox.
Provides:
  - Shopify OAuth 2.0 flow (/auth, /auth/callback)
  - Mandatory GDPR & Inventory/Product Webhooks (/webhooks/*) with HMAC verification
  - Merchandiser REST API (/api/collections, /api/collections/{id}/products, /save, /restore-snapshot)
  - Shopify GraphQL Billing API (/api/billing/subscribe)
  - Interactive Local Demo Sandbox (demo-store.myshopify.com) so you can test all features
    locally in your browser at http://127.0.0.1:8000 without needing Docker.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.database import (
    cleanup_shop_data,
    get_collection_rule,
    get_latest_snapshot,
    get_product_positions,
    get_shop_session,
    init_db,
    save_collection_snapshot,
    save_product_positions_batch,
    upsert_collection_rule,
    upsert_shop_session,
)
from app.shopify_service import (
    ShopifyGraphQLClient,
    compute_final_order,
    verify_shopify_webhook_hmac,
)

SHOPIFY_API_KEY = os.environ.get("SHOPIFY_API_KEY", "demo_api_key")
SHOPIFY_API_SECRET = os.environ.get("SHOPIFY_API_SECRET", "demo_api_secret")
SHOPIFY_APP_URL = os.environ.get("SHOPIFY_APP_URL", "http://127.0.0.1:8000")
SHOPIFY_SCOPES = "read_products,write_products"
DEMO_SHOP = "demo-store.myshopify.com"

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="SortFlow Pro — Shopify Collection Merchandiser",
    version="1.0.0",
    description="Visual Drag & Drop Collection Merchandiser + Out-of-Stock Demoter with Restock Memory",
)

# Initialize SQLite tables on module load
init_db()

# ---------------------------------------------------------------------------
# Built-in Demo Sandbox Data (for instant local testing & App Store video)
# ---------------------------------------------------------------------------

DEMO_COLLECTIONS: Dict[str, Dict[str, Any]] = {
    "col_summer_2026": {
        "id": "col_summer_2026",
        "gid": "gid://shopify/Collection/1001",
        "title": "Summer Essentials 2026",
        "handle": "summer-essentials-2026",
        "sortOrder": "BEST_SELLING",  # Intentionally non-MANUAL initially to demonstrate auto-fix!
        "productsCount": 8,
    },
    "col_footwear": {
        "id": "col_footwear",
        "gid": "gid://shopify/Collection/1002",
        "title": "Performance Footwear",
        "handle": "performance-footwear",
        "sortOrder": "MANUAL",
        "productsCount": 6,
    },
}

INITIAL_DEMO_PRODUCTS: Dict[str, List[Dict[str, Any]]] = {
    "col_summer_2026": [
        {
            "product_id": "prod_101",
            "gid": "gid://shopify/Product/101",
            "title": "AeroWeave Linen Overshirt — Sand",
            "handle": "aeroweave-linen-overshirt",
            "status": "ACTIVE",
            "total_inventory": 42,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "89.00",
            "currency": "EUR",
            "sku": "AW-LIN-01",
            "image_url": "",
            "badge_color": "#008060",
            "shopify_rank": 1,
        },
        {
            "product_id": "prod_102",
            "gid": "gid://shopify/Product/102",
            "title": "Solstice Polarized Sunglasses — Tortoise",
            "handle": "solstice-polarized-sunglasses",
            "status": "ACTIVE",
            "total_inventory": 0,
            "tracks_inventory": True,
            "is_in_stock": False,
            "price": "125.00",
            "currency": "EUR",
            "sku": "SL-SUN-02",
            "image_url": "",
            "badge_color": "#d82c0d",
            "shopify_rank": 2,
        },
        {
            "product_id": "prod_103",
            "gid": "gid://shopify/Product/103",
            "title": "Coastline Organic Cotton Tee — Navy",
            "handle": "coastline-organic-tee",
            "status": "ACTIVE",
            "total_inventory": 118,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "38.00",
            "currency": "EUR",
            "sku": "CL-TEE-03",
            "image_url": "",
            "badge_color": "#2c6ecb",
            "shopify_rank": 3,
        },
        {
            "product_id": "prod_104",
            "gid": "gid://shopify/Product/104",
            "title": "Riviera Chino Shorts — Olive",
            "handle": "riviera-chino-shorts",
            "status": "ACTIVE",
            "total_inventory": 19,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "64.00",
            "currency": "EUR",
            "sku": "RV-SHT-04",
            "image_url": "",
            "badge_color": "#5c6ac4",
            "shopify_rank": 4,
        },
        {
            "product_id": "prod_105",
            "gid": "gid://shopify/Product/105",
            "title": "Mariner Canvas Weekender Bag",
            "handle": "mariner-canvas-weekender",
            "status": "ACTIVE",
            "total_inventory": 0,
            "tracks_inventory": True,
            "is_in_stock": False,
            "price": "165.00",
            "currency": "EUR",
            "sku": "MR-BAG-05",
            "image_url": "",
            "badge_color": "#9c6ade",
            "shopify_rank": 5,
        },
        {
            "product_id": "prod_106",
            "gid": "gid://shopify/Product/106",
            "title": "Breeze Ultralight Cap — Stone",
            "handle": "breeze-ultralight-cap",
            "status": "ACTIVE",
            "total_inventory": 75,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "32.00",
            "currency": "EUR",
            "sku": "BZ-CAP-06",
            "image_url": "",
            "badge_color": "#47c1bf",
            "shopify_rank": 6,
        },
        {
            "product_id": "prod_107",
            "gid": "gid://shopify/Product/107",
            "title": "Mediterranean Espadrilles — Suede",
            "handle": "mediterranean-espadrilles",
            "status": "ACTIVE",
            "total_inventory": 14,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "95.00",
            "currency": "EUR",
            "sku": "MD-ESP-07",
            "image_url": "",
            "badge_color": "#f49342",
            "shopify_rank": 7,
        },
        {
            "product_id": "prod_108",
            "gid": "gid://shopify/Product/108",
            "title": "Thermal Insulated Beach Bottle 750ml",
            "handle": "thermal-beach-bottle",
            "status": "ACTIVE",
            "total_inventory": 54,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "29.00",
            "currency": "EUR",
            "sku": "TH-BTL-08",
            "image_url": "",
            "badge_color": "#00a0ac",
            "shopify_rank": 8,
        },
    ],
    "col_footwear": [
        {
            "product_id": "prod_201",
            "gid": "gid://shopify/Product/201",
            "title": "CloudStride Carbon Runner v3",
            "handle": "cloudstride-carbon-runner",
            "status": "ACTIVE",
            "total_inventory": 27,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "195.00",
            "currency": "EUR",
            "sku": "CS-RUN-01",
            "image_url": "",
            "badge_color": "#008060",
            "shopify_rank": 1,
        },
        {
            "product_id": "prod_202",
            "gid": "gid://shopify/Product/202",
            "title": "Apex Trail Grip GTX — Obsidian",
            "handle": "apex-trail-grip-gtx",
            "status": "ACTIVE",
            "total_inventory": 0,
            "tracks_inventory": True,
            "is_in_stock": False,
            "price": "175.00",
            "currency": "EUR",
            "sku": "AP-TRL-02",
            "image_url": "",
            "badge_color": "#d82c0d",
            "shopify_rank": 2,
        },
        {
            "product_id": "prod_203",
            "gid": "gid://shopify/Product/203",
            "title": "Velocity Court Low — White/Green",
            "handle": "velocity-court-low",
            "status": "ACTIVE",
            "total_inventory": 63,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "135.00",
            "currency": "EUR",
            "sku": "VL-CRT-03",
            "image_url": "",
            "badge_color": "#2c6ecb",
            "shopify_rank": 3,
        },
        {
            "product_id": "prod_204",
            "gid": "gid://shopify/Product/204",
            "title": "KnitFlex Recovery Slides",
            "handle": "knitflex-recovery-slides",
            "status": "ACTIVE",
            "total_inventory": 91,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "55.00",
            "currency": "EUR",
            "sku": "KF-SLD-04",
            "image_url": "",
            "badge_color": "#5c6ac4",
            "shopify_rank": 4,
        },
        {
            "product_id": "prod_205",
            "gid": "gid://shopify/Product/205",
            "title": "Merino Pro Running Crew Socks (3-Pack)",
            "handle": "merino-pro-running-socks",
            "status": "ACTIVE",
            "total_inventory": 140,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "28.00",
            "currency": "EUR",
            "sku": "MR-SCK-05",
            "image_url": "",
            "badge_color": "#9c6ade",
            "shopify_rank": 5,
        },
        {
            "product_id": "prod_206",
            "gid": "gid://shopify/Product/206",
            "title": "Tempo Track Spike — Neon Lime",
            "handle": "tempo-track-spike",
            "status": "ACTIVE",
            "total_inventory": 11,
            "tracks_inventory": True,
            "is_in_stock": True,
            "price": "160.00",
            "currency": "EUR",
            "sku": "TM-SPK-06",
            "image_url": "",
            "badge_color": "#f49342",
            "shopify_rank": 6,
        },
    ],
}

# Per-session sandbox storage: sandbox_sessions[session_id] = { col_id: [products] }
DEMO_SESSIONS_STATE: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}


def get_demo_products_state(session_id: str = "default") -> Dict[str, List[Dict[str, Any]]]:
    """Returns isolated demo products state per browser session."""
    if session_id not in DEMO_SESSIONS_STATE:
        DEMO_SESSIONS_STATE[session_id] = copy.deepcopy(INITIAL_DEMO_PRODUCTS)
    return DEMO_SESSIONS_STATE[session_id]


def reset_demo_state() -> None:
    """Resets in-memory demo products & collections for clean tests."""
    global DEMO_SESSIONS_STATE
    DEMO_SESSIONS_STATE.clear()
    DEMO_SESSIONS_STATE["default"] = copy.deepcopy(INITIAL_DEMO_PRODUCTS)
    DEMO_COLLECTIONS["col_summer_2026"]["sortOrder"] = "BEST_SELLING"


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class ProductRankInput(BaseModel):
    product_id: str
    position: int
    is_pinned: bool = False


class SaveCollectionOrderRequest(BaseModel):
    auto_demote_oos: bool = True
    restore_on_restock: bool = True
    new_products_position: str = Field(default="after_pinned", pattern="^(top|after_pinned|bottom)$")
    items: List[ProductRankInput]


class SimulateStockRequest(BaseModel):
    product_id: str
    new_inventory: int


class AddDemoProductRequest(BaseModel):
    title: str = "New Arrival Capsule Jacket"
    sku: str = "NEW-ARR-99"
    price: str = "149.00"
    inventory: int = 30


# ---------------------------------------------------------------------------
# UI & Static Route
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index_page() -> FileResponse:
    """Serves the Shopify Polaris Visual Collection Merchandiser UI."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page() -> FileResponse:
    """Public Privacy Policy for Shopify App Store Listing."""
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/support", response_class=HTMLResponse)
async def support_page() -> FileResponse:
    """Public Support page for Shopify App Store Listing."""
    return FileResponse(STATIC_DIR / "support.html")


# ---------------------------------------------------------------------------
# Shopify OAuth 2.0 Flow
# ---------------------------------------------------------------------------

@app.get("/auth")
async def shopify_auth_start(shop: str = Query(..., description="myshopify.com domain")) -> RedirectResponse:
    """Initiates Shopify OAuth installation redirect."""
    if not shop.endswith(".myshopify.com"):
        raise HTTPException(status_code=400, detail="Invalid shop domain. Must end with .myshopify.com")
    redirect_uri = f"{SHOPIFY_APP_URL.rstrip('/')}/auth/callback"
    params = urllib.parse.urlencode(
        {
            "client_id": SHOPIFY_API_KEY,
            "scope": SHOPIFY_SCOPES,
            "redirect_uri": redirect_uri,
        }
    )
    return RedirectResponse(url=f"https://{shop}/admin/oauth/authorize?{params}")


@app.get("/auth/callback")
async def shopify_auth_callback(
    shop: str = Query(...),
    code: str = Query(...),
) -> RedirectResponse:
    """Exchanges temporary authorization code for a permanent offline access token."""
    import httpx

    token_url = f"https://{shop}/admin/oauth/access_token"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            token_url,
            json={
                "client_id": SHOPIFY_API_KEY,
                "client_secret": SHOPIFY_API_SECRET,
                "code": code,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    access_token = payload.get("access_token", "")
    scope = payload.get("scope", SHOPIFY_SCOPES)
    upsert_shop_session(shop_domain=shop, access_token=access_token, scope=scope)
    return RedirectResponse(url=f"/?shop={urllib.parse.quote(shop)}")


# ---------------------------------------------------------------------------
# Merchandiser REST API
# ---------------------------------------------------------------------------

@app.get("/api/collections")
async def api_list_collections(
    shop: str = Query(default=DEMO_SHOP),
    session_id: str = Query(default="default"),
) -> Dict[str, Any]:
    """Lists collections for the connected Shopify store or Demo Sandbox."""
    session = get_shop_session(shop)
    if shop == DEMO_SHOP or not session:
        collections = list(DEMO_COLLECTIONS.values())
        demo_state = get_demo_products_state(session_id)
        for col in collections:
            col_id = col["id"]
            if col_id in demo_state:
                col["productsCount"] = len(demo_state[col_id])
            rule = get_collection_rule(shop, col_id)
            col["auto_demote_oos"] = bool(rule["auto_demote_oos"])
            col["restore_on_restock"] = bool(rule["restore_on_restock"])
            col["last_sorted_at"] = rule.get("last_sorted_at")
        return {
            "shop": shop,
            "mode": "demo_sandbox",
            "session_id": session_id,
            "plan": session["plan_name"] if session else "pro",
            "collections": collections,
        }

    client = ShopifyGraphQLClient(shop, session["access_token"])
    collections = await client.list_collections(limit=50)
    for col in collections:
        rule = get_collection_rule(shop, col["id"])
        col["auto_demote_oos"] = bool(rule["auto_demote_oos"])
        col["restore_on_restock"] = bool(rule["restore_on_restock"])
        col["last_sorted_at"] = rule.get("last_sorted_at")
    return {
        "shop": shop,
        "mode": "live_shopify",
        "plan": session["plan_name"],
        "collections": collections,
    }


@app.get("/api/collections/{collection_id}/products")
async def api_get_collection_products(
    collection_id: str,
    shop: str = Query(default=DEMO_SHOP),
    session_id: str = Query(default="default"),
) -> Dict[str, Any]:
    """
    Returns products for a collection with their current stored positions,
    pin flags, Out-of-Stock status, Restock Memory state, and snapshot availability.
    """
    session = get_shop_session(shop)
    rule = get_collection_rule(shop, collection_id)
    stored_positions = get_product_positions(shop, collection_id)
    snapshot = get_latest_snapshot(shop, collection_id)

    if shop == DEMO_SHOP or not session:
        if collection_id not in DEMO_COLLECTIONS:
            raise HTTPException(status_code=404, detail="Collection not found in demo store")
        collection_meta = DEMO_COLLECTIONS[collection_id]
        demo_state = get_demo_products_state(session_id)
        raw_products = copy.deepcopy(demo_state.get(collection_id, []))
    else:
        client = ShopifyGraphQLClient(shop, session["access_token"])
        raw_products = await client.fetch_collection_products(collection_id)
        collection_meta = {
            "id": collection_id,
            "title": f"Collection #{collection_id}",
            "sortOrder": "MANUAL",
        }

    # Compute effective deterministic order
    ordered_products, updated_positions = compute_final_order(
        shopify_products=raw_products,
        stored_positions=stored_positions,
        auto_demote_oos=bool(rule["auto_demote_oos"]),
        restore_on_restock=bool(rule["restore_on_restock"]),
        new_products_position=rule.get("new_products_position", "after_pinned"),
    )

    return {
        "shop": shop,
        "collection": collection_meta,
        "rule": {
            "auto_demote_oos": bool(rule["auto_demote_oos"]),
            "restore_on_restock": bool(rule["restore_on_restock"]),
            "new_products_position": rule.get("new_products_position", "after_pinned"),
            "last_sorted_at": rule.get("last_sorted_at"),
        },
        "has_snapshot": snapshot is not None,
        "snapshot": snapshot,
        "products": ordered_products,
        "position_metadata": updated_positions,
    }


@app.post("/api/collections/{collection_id}/save")
async def api_save_collection_order(
    collection_id: str,
    payload: SaveCollectionOrderRequest,
    shop: str = Query(default=DEMO_SHOP),
    session_id: str = Query(default="default"),
) -> Dict[str, Any]:
    """
    Saves visual merchandising positions & rules:
    1. Automatically records a pre-sort snapshot if none exists yet (for 1-click Undo).
    2. Ensures the Shopify collection is set to MANUAL sortOrder (solving the #1 competitor complaint!).
    3. Computes deterministic positions (Pinned -> In-Stock -> Auto-Demoted OOS with Restock Memory).
    4. Pushes the final order to Shopify via GraphQL collectionReorderProducts.
    """
    session = get_shop_session(shop)
    existing_positions = get_product_positions(shop, collection_id)
    existing_snapshot = get_latest_snapshot(shop, collection_id)

    sort_order_fixed = False
    if shop == DEMO_SHOP or not session:
        if collection_id not in DEMO_COLLECTIONS:
            raise HTTPException(status_code=404, detail="Collection not found")
        demo_state = get_demo_products_state(session_id)
        raw_products = demo_state[collection_id]
        if DEMO_COLLECTIONS[collection_id]["sortOrder"] != "MANUAL":
            DEMO_COLLECTIONS[collection_id]["sortOrder"] = "MANUAL"
            sort_order_fixed = True
    else:
        client = ShopifyGraphQLClient(shop, session["access_token"])
        raw_products = await client.fetch_collection_products(collection_id)
        sort_order_fixed = await client.ensure_manual_sort_order(collection_id)

    # Always save a snapshot of the current state right before applying new user order,
    # so clicking "Undo" always restores the state from just before the changes!
    current_snapshot_ids = [p["product_id"] for p in raw_products]
    save_collection_snapshot(
        shop_domain=shop,
        collection_id=collection_id,
        snapshot_ids=current_snapshot_ids,
        note="Pre-change snapshot",
    )

    # Merge user-submitted visual ranks with existing Restock Memory metadata
    user_positions_map: Dict[str, Dict[str, Any]] = {}
    for idx, item in enumerate(payload.items):
        prev = existing_positions.get(item.product_id, {})
        base_pos = (idx + 1) * 10
        user_positions_map[item.product_id] = {
            "product_id": item.product_id,
            "manual_position": base_pos,
            "saved_in_stock_position": prev.get("saved_in_stock_position", base_pos),
            "is_pinned": item.is_pinned,
            "is_out_of_stock": prev.get("is_out_of_stock", False),
        }

    ordered_products, updated_positions = compute_final_order(
        shopify_products=raw_products,
        stored_positions=user_positions_map,
        auto_demote_oos=payload.auto_demote_oos,
        restore_on_restock=payload.restore_on_restock,
        new_products_position=payload.new_products_position,
    )

    # Persist rules & positions in SQLite
    upsert_collection_rule(
        shop_domain=shop,
        collection_id=collection_id,
        auto_demote_oos=payload.auto_demote_oos,
        restore_on_restock=payload.restore_on_restock,
        new_products_position=payload.new_products_position,
    )
    save_product_positions_batch(shop, collection_id, list(updated_positions.values()))

    # Push to demo state or live Shopify GraphQL API
    if shop == DEMO_SHOP or not session:
        demo_state = get_demo_products_state(session_id)
        demo_state[collection_id] = ordered_products
        sync_info = {"synced": True, "moved_count": len(ordered_products), "job_id": "demo_job_1"}
    else:
        client = ShopifyGraphQLClient(shop, session["access_token"])
        sync_info = await client.push_collection_order(
            collection_id,
            [p["product_id"] for p in ordered_products],
        )

    return {
        "status": "ok",
        "sort_order_switched_to_manual": sort_order_fixed,
        "sync": sync_info,
        "products": ordered_products,
        "has_snapshot": True,
    }


@app.post("/api/collections/{collection_id}/restore-snapshot")
async def api_restore_snapshot(
    collection_id: str,
    shop: str = Query(default=DEMO_SHOP),
    session_id: str = Query(default="default"),
) -> Dict[str, Any]:
    """
    1-Click Undo / Rollback: Restores the collection's product order to the saved snapshot.
    Directly solves the competitor review complaint: 'Scrambled my collection with no Undo!'
    """
    snapshot = get_latest_snapshot(shop, collection_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot found for this collection")

    snapshot_ids: List[str] = snapshot["snapshot"]
    session = get_shop_session(shop)

    if shop == DEMO_SHOP or not session:
        demo_state = get_demo_products_state(session_id)
        current_items = {p["product_id"]: p for p in demo_state.get(collection_id, [])}
        restored_list: List[Dict[str, Any]] = []
        for rank_idx, pid in enumerate(snapshot_ids):
            if pid in current_items:
                item = copy.deepcopy(current_items[pid])
                item["effective_rank"] = rank_idx + 1
                item["is_pinned"] = False
                restored_list.append(item)
        # Include any newly added products not in the original snapshot at the end
        for pid, item in current_items.items():
            if pid not in snapshot_ids:
                item_copy = copy.deepcopy(item)
                item_copy["effective_rank"] = len(restored_list) + 1
                item_copy["is_pinned"] = False
                restored_list.append(item_copy)
        demo_state[collection_id] = restored_list
    else:
        client = ShopifyGraphQLClient(shop, session["access_token"])
        await client.push_collection_order(collection_id, snapshot_ids)
        restored_list = await client.fetch_collection_products(collection_id)

    # Reset stored positions to match snapshot order
    records = [
        {
            "product_id": pid,
            "manual_position": (idx + 1) * 10,
            "saved_in_stock_position": (idx + 1) * 10,
            "is_pinned": False,
            "is_out_of_stock": False,
        }
        for idx, pid in enumerate(snapshot_ids)
    ]
    save_product_positions_batch(shop, collection_id, records)
    upsert_collection_rule(
        shop_domain=shop,
        collection_id=collection_id,
        auto_demote_oos=False,
        restore_on_restock=True,
        new_products_position="after_pinned",
    )

    return {
        "status": "restored",
        "restored_from": snapshot["created_at"],
        "products": restored_list,
    }


@app.post("/api/collections/{collection_id}/simulate-stock")
async def api_simulate_stock_change(
    collection_id: str,
    payload: SimulateStockRequest,
    shop: str = Query(default=DEMO_SHOP),
    session_id: str = Query(default="default"),
) -> Dict[str, Any]:
    """
    Sandbox helper: Simulates a real-time Shopify `inventory_levels/update` webhook
    for a product (e.g., selling out to 0 or restocking to +25 units).
    Demonstrates automatic OOS demotion AND Restock Memory restoration!
    """
    demo_state = get_demo_products_state(session_id)
    if collection_id not in demo_state:
        raise HTTPException(status_code=404, detail="Demo collection not found")

    raw_products = demo_state[collection_id]
    target_found = False
    for p in raw_products:
        if p["product_id"] == payload.product_id:
            p["total_inventory"] = payload.new_inventory
            p["is_in_stock"] = payload.new_inventory > 0
            target_found = True
            break

    if not target_found:
        raise HTTPException(status_code=404, detail="Product not found in collection")

    rule = get_collection_rule(shop, collection_id)
    stored_positions = get_product_positions(shop, collection_id)

    ordered_products, updated_positions = compute_final_order(
        shopify_products=raw_products,
        stored_positions=stored_positions,
        auto_demote_oos=bool(rule["auto_demote_oos"]),
        restore_on_restock=bool(rule["restore_on_restock"]),
        new_products_position=rule.get("new_products_position", "after_pinned"),
    )

    save_product_positions_batch(shop, collection_id, list(updated_positions.values()))
    demo_state[collection_id] = ordered_products

    return {
        "status": "webhook_simulated",
        "product_id": payload.product_id,
        "new_inventory": payload.new_inventory,
        "products": ordered_products,
    }


@app.post("/api/collections/{collection_id}/simulate-new-product")
async def api_simulate_new_product(
    collection_id: str,
    payload: AddDemoProductRequest,
    shop: str = Query(default=DEMO_SHOP),
    session_id: str = Query(default="default"),
) -> Dict[str, Any]:
    """
    Sandbox helper: Simulates a `products/create` webhook when a merchant adds a new product.
    Demonstrates that new arrivals are placed according to `new_products_position` (e.g. right after
    pinned items, NEVER buried below out-of-stock products!).
    """
    demo_state = get_demo_products_state(session_id)
    if collection_id not in demo_state:
        raise HTTPException(status_code=404, detail="Demo collection not found")

    raw_products = demo_state[collection_id]
    new_id = f"prod_new_{len(raw_products) + 101}"
    new_prod = {
        "product_id": new_id,
        "gid": f"gid://shopify/Product/{new_id}",
        "title": payload.title,
        "handle": payload.title.lower().replace(" ", "-"),
        "status": "ACTIVE",
        "total_inventory": payload.inventory,
        "tracks_inventory": True,
        "is_in_stock": payload.inventory > 0,
        "price": payload.price,
        "currency": "EUR",
        "sku": payload.sku,
        "image_url": "",
        "badge_color": "#008060",
        "shopify_rank": len(raw_products) + 1,
    }
    raw_products.append(new_prod)

    rule = get_collection_rule(shop, collection_id)
    stored_positions = get_product_positions(shop, collection_id)

    ordered_products, updated_positions = compute_final_order(
        shopify_products=raw_products,
        stored_positions=stored_positions,
        auto_demote_oos=bool(rule["auto_demote_oos"]),
        restore_on_restock=bool(rule["restore_on_restock"]),
        new_products_position=rule.get("new_products_position", "after_pinned"),
    )

    save_product_positions_batch(shop, collection_id, list(updated_positions.values()))
    demo_state[collection_id] = ordered_products

    return {
        "status": "new_product_added",
        "new_product_id": new_id,
        "products": ordered_products,
    }


# ---------------------------------------------------------------------------
# Shopify Billing API ($9.99/month with 14-day Free Trial)
# ---------------------------------------------------------------------------

@app.post("/api/billing/subscribe")
async def api_create_subscription(shop: str = Query(default=DEMO_SHOP)) -> Dict[str, Any]:
    """Creates a recurring $9.99/mo subscription via Shopify GraphQL Billing API."""
    session = get_shop_session(shop)
    return_url = f"{SHOPIFY_APP_URL.rstrip('/')}/?shop={urllib.parse.quote(shop)}&billing=confirmed"
    if shop == DEMO_SHOP or not session:
        return {
            "mode": "demo_sandbox",
            "plan": "SortFlow Pro Unlimited ($9.99/mo, 14-day trial)",
            "confirmation_url": return_url,
        }

    client = ShopifyGraphQLClient(shop, session["access_token"])
    sub = await client.create_recurring_subscription(return_url=return_url, test=True)
    return {
        "mode": "live_shopify",
        "subscription": sub,
        "confirmation_url": sub.get("confirmationUrl"),
    }


# ---------------------------------------------------------------------------
# Mandatory Shopify Webhooks (Inventory, Uninstall & GDPR Compliance)
# ---------------------------------------------------------------------------

async def _verify_webhook_or_401(request: Request, x_shopify_hmac_sha256: Optional[str]) -> bytes:
    raw_body = await request.body()
    if not x_shopify_hmac_sha256:
        raise HTTPException(status_code=401, detail="Missing X-Shopify-Hmac-Sha256 header")
    if not verify_shopify_webhook_hmac(raw_body, x_shopify_hmac_sha256, SHOPIFY_API_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook HMAC signature")
    return raw_body


@app.post("/webhooks/app_uninstalled")
async def webhook_app_uninstalled(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(default=None),
    x_shopify_shop_domain: Optional[str] = Header(default=None),
) -> JSONResponse:
    """Cleans up shop tokens and data when merchant uninstalls the app."""
    await _verify_webhook_or_401(request, x_shopify_hmac_sha256)
    if x_shopify_shop_domain:
        cleanup_shop_data(x_shopify_shop_domain)
    return JSONResponse({"status": "cleaned_up"})


@app.post("/webhooks/inventory_or_product")
async def webhook_inventory_or_product(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(default=None),
    x_shopify_shop_domain: Optional[str] = Header(default=None),
) -> JSONResponse:
    """
    Receives real-time `inventory_levels/update`, `products/create`, `products/update`
    webhooks from Shopify and re-evaluates collection ordering for the shop.
    """
    raw_body = await _verify_webhook_or_401(request, x_shopify_hmac_sha256)
    payload = json.loads(raw_body.decode("utf-8") or "{}")
    return JSONResponse(
        {
            "status": "processed",
            "shop": x_shopify_shop_domain,
            "topic_object_id": payload.get("id") or payload.get("inventory_item_id"),
        }
    )


@app.post("/webhooks/gdpr")
async def webhook_gdpr_compliance(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(default=None),
    x_shopify_topic: Optional[str] = Header(default=None),
    x_shopify_shop_domain: Optional[str] = Header(default=None),
) -> JSONResponse:
    """
    Mandatory Shopify GDPR webhooks:
      - customers/data_request
      - customers/redact
      - shop/redact
    Note: SortFlow Pro stores zero customer PII (only product IDs and collection rules).
    """
    await _verify_webhook_or_401(request, x_shopify_hmac_sha256)
    if x_shopify_topic == "shop/redact" and x_shopify_shop_domain:
        cleanup_shop_data(x_shopify_shop_domain)
    return JSONResponse({"status": "gdpr_acknowledged", "topic": x_shopify_topic})


if __name__ == "__main__":
    import uvicorn

    print("Starting SortFlow Pro (Shopify App + Local Demo Sandbox) on http://127.0.0.1:8000 ...")
    uvicorn.run("app.server:app", host="127.0.0.1", port=8000, reload=False)

