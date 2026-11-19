"""BD Postgres de PRUEBA para el backend supabase del kanban (F2-02/F2-03).

Solo si HUB_TEST_DSN apunta a una BD desechable (p. ej. `docker run --rm -p 127.0.0.1:55432:5432 postgres:16-alpine`)
o HUB_TEST_EMBEDDED=1 con `pgserver` instalado (Postgres embebido en 127.0.0.1); sin ninguno, las pruebas que la
necesitan se saltan. Cada prueba recrea el esquema `hub` desde
tests/fixtures/hub_schema_f2.sql. Nunca apuntar a Supabase de verdad: el fixture borra el esquema `hub`."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

SCHEMA = Path(__file__).parent / "fixtures" / "hub_schema_f2.sql"
HERMES_SCHEMA = Path(__file__).parent / "fixtures" / "hermes_kanban_schema.sql"


_EMBEDDED: dict = {}


def _embedded_dsn() -> str | None:
    """Postgres EMBEBIDO (paquete `pgserver`, solo en 127.0.0.1) cuando se pide con HUB_TEST_EMBEDDED=1 y no hay
    HUB_TEST_DSN: las pruebas de varios workers (dos reservas a la vez, caducidad, reloj desfasado) corren con un Postgres
    de verdad y no con un doble de SQL. Es OPT-IN: el `test_cmd` del carril hub corre toda esta carpeta y arrancar un
    Postgres en cada verificación pesaría en una máquina justa de RAM. Un servidor por sesión de pytest, en una carpeta
    temporal que se borra al salir."""
    if not os.environ.get("HUB_TEST_EMBEDDED"):
        return None
    if "dsn" not in _EMBEDDED:
        try:
            import atexit
            import shutil
            import tempfile

            import pgserver
            folder = tempfile.mkdtemp(prefix="hub_pg_")
            server = pgserver.get_server(folder, cleanup_mode="stop")

            def _stop():
                try:
                    server.cleanup()
                finally:
                    shutil.rmtree(folder, ignore_errors=True)
            atexit.register(_stop)
            _EMBEDDED["dsn"] = server.get_uri()
        except Exception:  # sin pgserver, o no arranca: las pruebas se saltan como siempre
            _EMBEDDED["dsn"] = None
    return _EMBEDDED["dsn"]


def dsn_or_skip() -> str:
    dsn = os.environ.get("HUB_TEST_DSN", "").strip() or _embedded_dsn() or ""
    if not dsn:
        pytest.skip("sin HUB_TEST_DSN (BD Postgres de prueba) ni HUB_TEST_EMBEDDED=1 (pgserver)")
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
