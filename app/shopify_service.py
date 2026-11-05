"""
Shopify Admin GraphQL API Client + Deterministic Collection Sorting Engine.
Key competitive advantages implemented here:
  1. Auto-switches collection sortOrder to MANUAL if it isn't already (fixes #1 competitor 1-star complaint).
  2. Restock Memory™: when an out-of-stock item is restocked (total_inventory > 0), it returns to its
     exact saved_in_stock_position instead of staying stuck at the bottom.
  3. Smart New-Item Placement: newly added products without an entry in product_positions are placed
     according to new_products_position ('after_pinned' = right after pinned items, 'top' = #1,
     'bottom' = above out-of-stock items — never buried below sold-out products!).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
from typing import Any, Dict, List, Optional, Tuple

import httpx

API_VERSION = "2025-01"


def compute_final_order(
    shopify_products: List[Dict[str, Any]],
    stored_positions: Dict[str, Dict[str, Any]],
    auto_demote_oos: bool = True,
    restore_on_restock: bool = True,
    new_products_position: str = "after_pinned",
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Pure, deterministic sorting engine:
      Tier 0: Pinned & In-Stock products (or Pinned regardless if auto_demote_oos=False)
      Tier 1: New Arrivals (if new_products_position == 'top' or 'after_pinned')
      Tier 2: Regular In-Stock products (restoring saved_in_stock_position on restock!)
      Tier 3: Out-of-Stock products (when auto_demote_oos=True), preserving their saved_in_stock_position
    """
    enriched: List[Dict[str, Any]] = []
    updated_positions: Dict[str, Dict[str, Any]] = {}

    for idx, raw in enumerate(shopify_products):
        prod = copy.deepcopy(raw)
        pid = str(prod["product_id"])
        inv = int(prod.get("total_inventory", 0))
        tracks = bool(prod.get("tracks_inventory", True))
        is_in_stock = (inv > 0) or (not tracks)
        prod["is_in_stock"] = is_in_stock

        st = stored_positions.get(pid)
        is_new_product = st is None

        if st is not None:
            is_pinned = bool(st.get("is_pinned", False))
            manual_pos = int(st.get("manual_position", (idx + 1) * 10))
            saved_mem = st.get("saved_in_stock_position")
            was_oos = bool(st.get("is_out_of_stock", False))

            if not is_in_stock:
                # Item is currently Out-of-Stock:
                # Preserve its pre-OOS in-stock position in Restock Memory!
                if saved_mem is None:
                    saved_mem = manual_pos
                effective_pos = manual_pos
                restock_boost = 0
            else:
                # Item is In-Stock! If it was previously OOS and restore_on_restock=True, restore exact rank
                if restore_on_restock and saved_mem is not None:
                    effective_pos = int(saved_mem)
                    restock_boost = -1 if was_oos else 0
                else:
                    effective_pos = manual_pos
                    restock_boost = 0
                saved_mem = effective_pos
        else:
            # Brand new product (not yet in stored_positions)
            is_pinned = bool(prod.get("is_pinned", False))
            if new_products_position == "top":
                effective_pos = -500 + idx
            elif new_products_position == "after_pinned":
                effective_pos = 5 + idx  # Right before regular items (which start at 10, 20, 30...)
            else:
                effective_pos = 90000 + idx  # Bottom of in-stock items, still above OOS
            saved_mem = effective_pos
            restock_boost = 0

        prod["is_pinned"] = is_pinned
        prod["saved_in_stock_position"] = saved_mem
        prod["_effective_pos"] = effective_pos
        prod["_restock_boost"] = restock_boost
        prod["_is_new"] = is_new_product
        prod["_orig_idx"] = idx
        enriched.append(prod)

    def sort_key(item: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
        oos_tier = 1 if (auto_demote_oos and not item["is_in_stock"]) else 0
        if item["_is_new"] and new_products_position == "top" and oos_tier == 0:
            pin_tier = -1
        else:
            pin_tier = 0 if item["is_pinned"] else 1
        return (
            oos_tier,
            pin_tier,
            int(item["_effective_pos"]),
            int(item["_restock_boost"]),
            int(item["_orig_idx"]),
        )

    sorted_products = sorted(enriched, key=sort_key)

    for rank_idx, item in enumerate(sorted_products):
        pid = str(item["product_id"])
        new_manual_pos = (rank_idx + 1) * 10
        item["effective_rank"] = rank_idx + 1

        # Preserve original saved_in_stock_position if already set, otherwise initialize to new_manual_pos
        if item["is_in_stock"]:
            saved_in_stock = new_manual_pos
        else:
            saved_in_stock = item.get("saved_in_stock_position") or new_manual_pos

        item["saved_in_stock_position"] = saved_in_stock
        item.pop("_effective_pos", None)
        item.pop("_restock_boost", None)
        item.pop("_is_new", None)
        item.pop("_orig_idx", None)

        updated_positions[pid] = {
            "product_id": pid,
            "manual_position": new_manual_pos,
            "saved_in_stock_position": saved_in_stock,
            "is_pinned": bool(item.get("is_pinned", False)),
            "is_out_of_stock": not item["is_in_stock"],
        }

    return sorted_products, updated_positions


class ShopifyGraphQLClient:
    """Async GraphQL client for Shopify Admin API (2025-10)."""

    def __init__(self, shop_domain: str, access_token: str) -> None:
        self.shop_domain = shop_domain
        self.access_token = access_token
        self.endpoint = f"https://{shop_domain}/admin/api/{API_VERSION}/graphql.json"

    async def execute(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.endpoint,
                json={"query": query, "variables": variables or {}},
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                raise RuntimeError(f"Shopify GraphQL error: {payload['errors']}")
            return payload.get("data", {})

    async def list_collections(self, limit: int = 50) -> List[Dict[str, Any]]:
        query = """
        query ListCollections($first: Int!) {
          collections(first: $first) {
            nodes {
              id
              title
              handle
              sortOrder
              productsCount {
                count
              }
            }
          }
        }
        """
        data = await self.execute(query, {"first": limit})
        nodes = data.get("collections", {}).get("nodes", [])
        return [
            {
                "id": n["id"].split("/")[-1],
                "gid": n["id"],
                "title": n["title"],
                "handle": n.get("handle", ""),
                "sortOrder": n.get("sortOrder", "MANUAL"),
                "productsCount": (n.get("productsCount") or {}).get("count", 0),
            }
            for n in nodes
        ]

    async def fetch_collection_products(self, collection_id: str, first: int = 250) -> List[Dict[str, Any]]:
        gid = collection_id if collection_id.startswith("gid://") else f"gid://shopify/Collection/{collection_id}"
        query = """
        query GetCollectionProducts($id: ID!, $first: Int!) {
          collection(id: $id) {
            id
            title
            sortOrder
            products(first: $first) {
              nodes {
                id
                title
                handle
                status
                totalInventory
                tracksInventory
                featuredImage {
                  url
                }
                variants(first: 1) {
                  nodes {
                    price
                    sku
                  }
                }
              }
            }
          }
        }
        """
        data = await self.execute(query, {"id": gid, "first": first})
        col = data.get("collection") or {}
        nodes = (col.get("products") or {}).get("nodes", [])
        result: List[Dict[str, Any]] = []
        for idx, n in enumerate(nodes):
            inv = int(n.get("totalInventory") or 0)
            tracks = bool(n.get("tracksInventory", True))
            img_url = (n.get("featuredImage") or {}).get("url") or ""
            var_nodes = (n.get("variants") or {}).get("nodes") or []
            first_var = var_nodes[0] if var_nodes else {}
            price = first_var.get("price") or ""
            sku = first_var.get("sku") or ""
            result.append(
                {
                    "product_id": n["id"].split("/")[-1],
                    "gid": n["id"],
                    "title": n["title"],
                    "handle": n.get("handle", ""),
                    "status": n.get("status", "ACTIVE"),
                    "total_inventory": inv,
                    "tracks_inventory": tracks,
                    "is_in_stock": (inv > 0) or (not tracks),
                    "price": price,
                    "sku": sku,
                    "image_url": img_url,
                    "shopify_rank": idx + 1,
                }
            )
        return result

    async def ensure_manual_sort_order(self, collection_id: str) -> bool:
        gid = collection_id if collection_id.startswith("gid://") else f"gid://shopify/Collection/{collection_id}"
        check_query = """
        query CheckSortOrder($id: ID!) {
          collection(id: $id) {
            id
            sortOrder
          }
        }
        """
        data = await self.execute(check_query, {"id": gid})
        current_sort = ((data.get("collection") or {}).get("sortOrder")) or "MANUAL"
        if current_sort == "MANUAL":
            return False

        mutation = """
        mutation SetManualSort($input: CollectionInput!) {
          collectionUpdate(input: $input) {
            collection {
              id
              sortOrder
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        await self.execute(mutation, {"input": {"id": gid, "sortOrder": "MANUAL"}})
        return True

    async def push_collection_order(self, collection_id: str, ordered_product_ids: List[str]) -> Dict[str, Any]:
        col_gid = collection_id if collection_id.startswith("gid://") else f"gid://shopify/Collection/{collection_id}"
        moves = [
            {
                "id": pid if pid.startswith("gid://") else f"gid://shopify/Product/{pid}",
                "newPosition": str(idx),
            }
            for idx, pid in enumerate(ordered_product_ids[:250])
        ]
        mutation = """
        mutation ReorderCollectionProducts($id: ID!, $moves: [MoveInput!]!) {
          collectionReorderProducts(id: $id, moves: $moves) {
            job {
              id
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        data = await self.execute(mutation, {"id": col_gid, "moves": moves})
        res = data.get("collectionReorderProducts") or {}
        return {
            "synced": True,
            "moved_count": len(moves),
            "job_id": (res.get("job") or {}).get("id"),
            "user_errors": res.get("userErrors", []),
        }

    async def create_recurring_subscription(
        self,
        return_url: str,
        plan_name: str = "SortFlow Pro Unlimited",
        price_usd: float = 9.99,
        trial_days: int = 14,
        test: bool = True,
    ) -> Dict[str, Any]:
        mutation = """
        mutation CreateSubscription($name: String!, $returnUrl: URL!, $trialDays: Int!, $test: Boolean!, $amount: Decimal!) {
          appSubscriptionCreate(
            name: $name,
            returnUrl: $returnUrl,
            trialDays: $trialDays,
            test: $test,
            lineItems: [{
              plan: {
                appRecurringPricingDetails: {
                  price: { amount: $amount, currencyCode: USD },
                  interval: EVERY_30_DAYS
                }
              }
            }]
          ) {
            confirmationUrl
            appSubscription {
              id
              status
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        data = await self.execute(
            mutation,
            {
                "name": plan_name,
                "returnUrl": return_url,
                "trialDays": trial_days,
                "test": test,
                "amount": str(price_usd),
            },
        )
        return data.get("appSubscriptionCreate") or {}


def verify_shopify_webhook_hmac(raw_body: bytes, hmac_header: str, client_secret: str) -> bool:
    digest = hmac.new(client_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    computed = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed, hmac_header or "")
