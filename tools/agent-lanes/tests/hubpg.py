"""BD Postgres de PRUEBA para el backend supabase del kanban (F2-02/F2-03).

Solo si HUB_TEST_DSN apunta a una BD desechable (p. ej. `docker run --rm -p 127.0.0.1:55432:5432 postgres:16-alpine`);
sin ella, las pruebas que la necesitan se saltan. Cada prueba recrea el esquema `hub` desde
tests/fixtures/hub_schema_f2.sql. Nunca apuntar a Supabase de verdad: el fixture borra el esquema `hub`."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

SCHEMA = Path(__file__).parent / "fixtures" / "hub_schema_f2.sql"
HERMES_SCHEMA = Path(__file__).parent / "fixtures" / "hermes_kanban_schema.sql"


def dsn_or_skip() -> str:
    dsn = os.environ.get("HUB_TEST_DSN", "").strip()
    if not dsn:
        pytest.skip("sin HUB_TEST_DSN (BD Postgres de prueba)")
    if "supabase" in dsn:
        pytest.fail("HUB_TEST_DSN no puede ser Supabase: el fixture borra el esquema hub")
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        pytest.skip("sin psycopg2")
    return dsn


def reset_schema(dsn: str) -> None:
    import psycopg2
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS hub CASCADE")
            cur.execute(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def hub_dsn():
    dsn = dsn_or_skip()
    reset_schema(dsn)
    return dsn
