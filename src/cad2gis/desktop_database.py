"""Bounded read-only SQLite/GPKG explorer; never executes writes or extensions."""
from __future__ import annotations

from contextlib import contextmanager
import math
import sqlite3
import time
from typing import Any


def page_bounds(page: int, page_size: int, *, maximum: int = 200) -> tuple[int, int]:
    if type(page) is not int or type(page_size) is not int or not 1 <= page <= 1_000_000 or not 1 <= page_size <= maximum:
        raise ValueError("分页参数无效")
    return (page - 1) * page_size, page_size


@contextmanager
def connection(config, path: str):
    target = config.path(path)
    if target.suffix.lower() not in {".sqlite", ".sqlite3", ".db", ".gpkg"} or not target.is_file():
        raise ValueError("请选择 SQLite 或 GeoPackage 数据库")
    with target.open("rb") as file:
        if file.read(16) != b"SQLite format 3\x00":
            raise ValueError("文件不是有效的 SQLite 数据库")
    db = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 4 * 1024 * 1024)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 16384)
        deadline = time.monotonic() + 3
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        blocked = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA,
                   sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                   sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                   sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_TRANSACTION}

        def authorize(action, first, second, _database, _trigger):
            if action in blocked or (action == sqlite3.SQLITE_FUNCTION and
                                     str(second or first).lower() in {"load_extension", "writefile", "readfile"}):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        yield db
    except sqlite3.Error as exc:
        raise ValueError(f"数据库只读查询未完成：{exc}") from None
    finally:
        db.close()


def schema(config, path: str) -> dict[str, Any]:
    with connection(config, path) as db:
        tables = []
        for name, kind, sql in db.execute("SELECT name,type,sql FROM sqlite_schema WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 300"):
            # sqlite_schema is read as data. Table names are quoted independently below.
            quoted = '"' + name.replace('"', '""') + '"'
            try:
                columns = [item[0] for item in db.execute(f"SELECT * FROM {quoted} LIMIT 0").description]
            except sqlite3.Error:
                columns = []
            tables.append({"name": name, "type": kind, "columns": columns, "sql": sql})
        return {"path": path, "tables": tables, "readonly": True}


def query(config, path: str, *, table: str = "", sql: str = "", page: int = 1, page_size: int = 50) -> dict[str, Any]:
    offset, limit = page_bounds(page, page_size)
    if not isinstance(table, str) or not isinstance(sql, str) or len(sql) > 16000:
        raise ValueError("SQL 过长")
    with connection(config, path) as db:
        if table:
            found = db.execute("SELECT name FROM sqlite_schema WHERE name=? AND type IN ('table','view')", (table,)).fetchone()
            if not found:
                raise ValueError("数据表不存在")
            sql = 'SELECT * FROM "' + table.replace('"', '""') + '"'
        elif not sql.strip():
            raise ValueError("请选择数据表或输入只读 SQL")
        statement = sql.strip().rstrip(";")
        cursor = db.execute(f"SELECT * FROM ({statement}) AS cad2gis_page LIMIT ? OFFSET ?", (limit + 1, offset))
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()

        def cell(value):
            if isinstance(value, float) and not math.isfinite(value):
                return str(value)
            if isinstance(value, bytes):
                return f"[BLOB · {len(value)} bytes]"
            if isinstance(value, str) and len(value) > 2000:
                return value[:2000] + "…"
            return value

        return {"columns": columns, "rows": [[cell(value) for value in row] for row in rows[:limit]],
                "page": page, "page_size": limit, "has_more": len(rows) > limit, "readonly": True}
