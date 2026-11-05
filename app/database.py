"""
SQLite WAL state store for SortFlow Pro (Shopify App).
Tracks:
  - shop_sessions: OAuth offline access tokens and billing plan status per shop domain
  - collection_rules: per-collection settings (auto_demote_oos, restore_on_restock, new_products_position)
  - product_positions: pinned products, manual_position, and saved_in_stock_position (Restock Memory™)
  - collection_snapshots: full JSON snapshots before every reorder for 1-click Undo/Restore
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "sortflow_shopify.sqlite"

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS shop_sessions (
    shop_domain TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'read_products,write_products',
    plan_name TEXT NOT NULL DEFAULT 'pro',
    installed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_rules (
    shop_domain TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    auto_demote_oos INTEGER NOT NULL DEFAULT 1,
    restore_on_restock INTEGER NOT NULL DEFAULT 1,
    new_products_position TEXT NOT NULL DEFAULT 'after_pinned',
    last_sorted_at TEXT,
    PRIMARY KEY (shop_domain, collection_id)
);

CREATE TABLE IF NOT EXISTS product_positions (
    shop_domain TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    manual_position INTEGER NOT NULL DEFAULT 300000,
    saved_in_stock_position INTEGER DEFAULT NULL,
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_out_of_stock INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (shop_domain, collection_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_pos_lookup
    ON product_positions (shop_domain, collection_id, is_pinned, manual_position);

CREATE TABLE IF NOT EXISTS collection_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_domain TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


def _get_db_path() -> Path:
    env_path = os.environ.get("SORTFLOW_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def get_db() -> sqlite3.Connection:
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    con = get_db()
    con.executescript(SCHEMA_SQL)
    con.commit()
    con.close()


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def upsert_shop_session(
    shop_domain: str,
    access_token: str,
    scope: str = "read_products,write_products",
    plan_name: str = "pro",
) -> None:
    now = _now_iso()
    con = get_db()
    con.execute(
        """
        INSERT INTO shop_sessions (shop_domain, access_token, scope, plan_name, installed_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(shop_domain) DO UPDATE SET
            access_token = excluded.access_token,
            scope = excluded.scope,
            plan_name = excluded.plan_name,
            updated_at = excluded.updated_at
        """,
        (shop_domain, access_token, scope, plan_name, now, now),
    )
    con.commit()
    con.close()


def get_shop_session(shop_domain: str) -> Optional[Dict[str, Any]]:
    con = get_db()
    row = con.execute("SELECT * FROM shop_sessions WHERE shop_domain = ?", (shop_domain,)).fetchone()
    con.close()
    return dict(row) if row else None


def cleanup_shop_data(shop_domain: str) -> None:
    con = get_db()
    for table in ("shop_sessions", "collection_rules", "product_positions", "collection_snapshots"):
        con.execute(f"DELETE FROM {table} WHERE shop_domain = ?", (shop_domain,))
    con.commit()
    con.close()


def get_collection_rule(shop_domain: str, collection_id: str) -> Dict[str, Any]:
    con = get_db()
    row = con.execute(
        "SELECT * FROM collection_rules WHERE shop_domain = ? AND collection_id = ?",
        (shop_domain, collection_id),
    ).fetchone()
    con.close()
    if row:
        return dict(row)
    return {
        "shop_domain": shop_domain,
        "collection_id": collection_id,
        "auto_demote_oos": 1,
        "restore_on_restock": 1,
        "new_products_position": "after_pinned",
        "last_sorted_at": None,
    }


def upsert_collection_rule(
    shop_domain: str,
    collection_id: str,
    auto_demote_oos: bool = True,
    restore_on_restock: bool = True,
    new_products_position: str = "after_pinned",
) -> None:
    now = _now_iso()
    con = get_db()
    con.execute(
        """
        INSERT INTO collection_rules
            (shop_domain, collection_id, auto_demote_oos, restore_on_restock, new_products_position, last_sorted_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(shop_domain, collection_id) DO UPDATE SET
            auto_demote_oos = excluded.auto_demote_oos,
            restore_on_restock = excluded.restore_on_restock,
            new_products_position = excluded.new_products_position,
            last_sorted_at = excluded.last_sorted_at
        """,
        (
            shop_domain,
            collection_id,
            1 if auto_demote_oos else 0,
            1 if restore_on_restock else 0,
            new_products_position,
            now,
        ),
    )
    con.commit()
    con.close()


def get_product_positions(shop_domain: str, collection_id: str) -> Dict[str, Dict[str, Any]]:
    con = get_db()
    rows = con.execute(
        "SELECT * FROM product_positions WHERE shop_domain = ? AND collection_id = ?",
        (shop_domain, collection_id),
    ).fetchall()
    con.close()
    result: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        d["is_pinned"] = bool(d["is_pinned"])
        d["is_out_of_stock"] = bool(d["is_out_of_stock"])
        result[d["product_id"]] = d
    return result


def save_product_positions_batch(
    shop_domain: str,
    collection_id: str,
    records: List[Dict[str, Any]],
) -> None:
    now = _now_iso()
    con = get_db()
    con.execute(
        "DELETE FROM product_positions WHERE shop_domain = ? AND collection_id = ?",
        (shop_domain, collection_id),
    )
    for rec in records:
        con.execute(
            """
            INSERT INTO product_positions
                (shop_domain, collection_id, product_id, manual_position, saved_in_stock_position, is_pinned, is_out_of_stock, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shop_domain,
                collection_id,
                str(rec["product_id"]),
                int(rec.get("manual_position", 300000)),
                rec.get("saved_in_stock_position"),
                1 if rec.get("is_pinned") else 0,
                1 if rec.get("is_out_of_stock") else 0,
                now,
            ),
        )
    con.commit()
    con.close()


def save_collection_snapshot(
    shop_domain: str,
    collection_id: str,
    snapshot_ids: List[str],
    note: str = "Pre-sort snapshot",
) -> int:
    now = _now_iso()
    con = get_db()
    cur = con.execute(
        """
        INSERT INTO collection_snapshots (shop_domain, collection_id, snapshot_json, note, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (shop_domain, collection_id, json.dumps(snapshot_ids), note, now),
    )
    con.commit()
    last_id = int(cur.lastrowid or 0)
    con.close()
    return last_id


def get_latest_snapshot(shop_domain: str, collection_id: str) -> Optional[Dict[str, Any]]:
    con = get_db()
    row = con.execute(
        """
        SELECT * FROM collection_snapshots
        WHERE shop_domain = ? AND collection_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (shop_domain, collection_id),
    ).fetchone()
    con.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "snapshot": json.loads(row["snapshot_json"]),
        "note": row["note"],
        "created_at": row["created_at"],
    }
