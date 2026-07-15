#!/usr/bin/env python3
"""将 Koishi maibot 账号绑定与协议记录导入 QueryBot。

支持：
  1. 含 ``maibot_bindings`` 表的完整 Koishi SQLite 数据库；
  2. JSON 数组，或 ``{"maibot_bindings": [...]}``。

Koishi ``koishi:<id>`` 无法天然对应 QQ 时，可传入 JSON identity map：
``{"koishi:12": "123456789"}``。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS account_bindings (
    user_key       TEXT PRIMARY KEY,
    mai_uid        TEXT NOT NULL DEFAULT '',
    qrcode         TEXT NOT NULL DEFAULT '',
    user_name      TEXT NOT NULL DEFAULT '',
    rating         INTEGER NOT NULL DEFAULT 0,
    fish_token     TEXT NOT NULL DEFAULT '',
    lxns_token     TEXT NOT NULL DEFAULT '',
    bound_at       REAL NOT NULL,
    updated_at     REAL NOT NULL,
    last_upload_at REAL
);
"""

AGREEMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_agreements (
    user_id       TEXT PRIMARY KEY,
    version       TEXT NOT NULL,
    accepted_at   REAL NOT NULL,
    revoked_at    REAL
);
"""


def _row_get(row: dict[str, Any], *names: str, default: Any = "") -> Any:
    normalized = {re.sub(r"[_-]", "", str(k)).lower(): v for k, v in row.items()}
    for name in names:
        key = re.sub(r"[_-]", "", name).lower()
        if key in normalized and normalized[key] is not None:
            return normalized[key]
    return default


def _timestamp(value: Any, default: float) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        # JavaScript timestamp is milliseconds.
        return float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return default


def _normalize_user_key(raw: Any, identity_map: dict[str, str]) -> str:
    value = str(raw or "").strip()
    if value in identity_map:
        return str(identity_map[value]).strip()
    if value.isdigit():
        return value
    match = re.fullmatch(r"(?:onebot|qq):([0-9]+)", value, re.IGNORECASE)
    if match:
        return match.group(1)
    # koishi:<id> 是 Koishi 内部 ID，不猜测其 QQ 映射。
    if value.lower().startswith("koishi:"):
        raise ValueError(f"{value} 需要在 identity map 中提供 QueryBot 用户 ID")
    if not value:
        raise ValueError("缺少 userId")
    return value


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _discover_binding_table(conn: sqlite3.Connection, requested: str = "") -> str:
    tables = [
        str(row[0]) for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    if requested:
        if requested not in tables:
            raise ValueError(f"SQLite 中没有表 {requested}")
        return requested
    if "maibot_bindings" in tables:
        return "maibot_bindings"
    candidates: list[str] = []
    characteristic = {"maiuid", "qrcode", "fishtoken", "lxnstoken", "lxnscode"}
    for table in tables:
        columns = {
            re.sub(r"[_-]", "", str(row[1])).lower()
            for row in conn.execute(f"PRAGMA table_info({_quote_identifier(table)})")
        }
        if ({"userid", "userkey"} & columns) and len(characteristic & columns) >= 2:
            candidates.append(table)
    if not candidates:
        raise ValueError(
            "未找到 maibot 绑定表；可使用 --table 指定，源库不会被修改"
        )
    if len(candidates) > 1:
        raise ValueError(f"发现多个候选表 {candidates}，请使用 --table 指定")
    return candidates[0]


def _load_source(path: Path, table_name: str = "") -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("maibot_bindings") or payload.get("bindings") or []
        if not isinstance(payload, list):
            raise ValueError("JSON 必须是绑定数组或包含 maibot_bindings 数组")
        return [dict(item) for item in payload if isinstance(item, dict)]

    # URI mode=ro 确保即使 Koishi 数据库里有其它插件表，源库也绝不会被写入。
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        table = _discover_binding_table(conn, table_name)
        return [dict(row) for row in conn.execute(f"SELECT * FROM {_quote_identifier(table)}")]
    finally:
        conn.close()


def _load_terms_source(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows = payload.get("maibot_user_terms") or payload.get("user_terms") or []
            return [dict(item) for item in rows if isinstance(item, dict)]
        return []
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='maibot_user_terms'"
        ).fetchone()
        if not exists:
            return []
        return [dict(row) for row in conn.execute("SELECT * FROM maibot_user_terms")]
    finally:
        conn.close()


def migrate(
    rows: Iterable[dict[str, Any]],
    target: Path,
    identity_map: dict[str, str],
    *,
    dry_run: bool,
) -> tuple[int, list[str]]:
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.executescript(SCHEMA)
    now = time.time()
    imported = 0
    skipped: list[str] = []
    try:
        for row in rows:
            raw_user = _row_get(row, "userId", "user_key")
            try:
                key = _normalize_user_key(raw_user, identity_map)
            except ValueError as exc:
                skipped.append(str(exc))
                continue
            rating_raw = _row_get(row, "rating", default=0)
            try:
                rating = int(float(rating_raw or 0))
            except (TypeError, ValueError):
                rating = 0
            values = (
                key,
                str(_row_get(row, "maiUid", "mai_uid")),
                str(_row_get(row, "qrCode", "qrcode")),
                str(_row_get(row, "userName", "user_name")),
                rating,
                str(_row_get(row, "fishToken", "fish_token")),
                str(_row_get(row, "lxnsCode", "lxnsToken", "lxns_token")),
                _timestamp(_row_get(row, "bindTime", "bound_at"), now),
                now,
            )
            if not dry_run:
                conn.execute(
                    """
                    INSERT INTO account_bindings
                        (user_key, mai_uid, qrcode, user_name, rating, fish_token,
                         lxns_token, bound_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_key) DO UPDATE SET
                        mai_uid = CASE WHEN excluded.mai_uid != '' THEN excluded.mai_uid ELSE account_bindings.mai_uid END,
                        qrcode = CASE WHEN excluded.qrcode != '' THEN excluded.qrcode ELSE account_bindings.qrcode END,
                        user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE account_bindings.user_name END,
                        rating = CASE WHEN excluded.rating != 0 THEN excluded.rating ELSE account_bindings.rating END,
                        fish_token = CASE WHEN excluded.fish_token != '' THEN excluded.fish_token ELSE account_bindings.fish_token END,
                        lxns_token = CASE WHEN excluded.lxns_token != '' THEN excluded.lxns_token ELSE account_bindings.lxns_token END,
                        updated_at = excluded.updated_at
                    """,
                    values,
                )
            imported += 1
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    return imported, skipped


def migrate_terms(
    rows: Iterable[dict[str, Any]], target: Path, identity_map: dict[str, str],
    *, dry_run: bool,
) -> tuple[int, list[str]]:
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.executescript(AGREEMENT_SCHEMA)
    imported = 0
    skipped: list[str] = []
    try:
        for row in rows:
            try:
                key = _normalize_user_key(
                    _row_get(row, "userId", "user_id"), identity_map
                )
            except ValueError as exc:
                skipped.append(str(exc))
                continue
            accepted_at = _timestamp(
                _row_get(row, "acceptedAt", "accepted_at"), time.time()
            )
            version = str(
                _row_get(row, "termsVersion", "version", default="2.0.0")
                or "2.0.0"
            )
            if not dry_run:
                conn.execute(
                    """INSERT INTO user_agreements
                       (user_id, version, accepted_at, revoked_at)
                       VALUES (?, ?, ?, NULL)
                       ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,
                       accepted_at=excluded.accepted_at, revoked_at=NULL""",
                    (key, version, accepted_at),
                )
            imported += 1
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    finally:
        conn.close()
    return imported, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="maibot SQLite 或 JSON 导出")
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "account" / "account.db",
    )
    parser.add_argument(
        "--admin-target",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "admin" / "admin.db",
        help="协议记录目标管理库",
    )
    parser.add_argument("--identity-map", type=Path, help="旧 userId 到 QueryBot ID 的 JSON 映射")
    parser.add_argument("--table", default="", help="Koishi 绑定表名；默认自动识别")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    identity_map: dict[str, str] = {}
    if args.identity_map:
        identity_map = {
            str(k): str(v)
            for k, v in json.loads(args.identity_map.read_text(encoding="utf-8")).items()
        }
    rows = _load_source(args.source, args.table)
    term_rows = _load_terms_source(args.source)
    imported, skipped = migrate(
        rows, args.target, identity_map, dry_run=bool(args.dry_run)
    )
    terms_imported, terms_skipped = migrate_terms(
        term_rows, args.admin_target, identity_map, dry_run=bool(args.dry_run)
    )
    mode = "可导入" if args.dry_run else "已导入"
    print(
        f"绑定{mode} {imported} 条，协议{mode} {terms_imported} 条，"
        f"跳过 {len(skipped) + len(terms_skipped)} 条"
    )
    print(f"账号目标：{args.target}\n管理目标：{args.admin_target}")
    for reason in (skipped + terms_skipped)[:20]:
        print("-", reason)
    return 0 if not skipped and not terms_skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())
