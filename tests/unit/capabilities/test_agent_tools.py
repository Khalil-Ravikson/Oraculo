"""
Plano A / Fase 5 — capabilities/agent_tools.py (vínculo agente↔capability,
`agente_tools`, migration 012). Postgres real (o CI provê).
"""
import pytest

from src.capabilities import agent_tools
from src.infrastructure.database.session import AsyncSessionLocal

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def db_limpo():
    import psycopg2
    from src.infrastructure.settings import settings
    url = settings.DATABASE_URL.replace("+asyncpg", "")
    try:
        conn = psycopg2.connect(url)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Postgres de teste indisponível: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("TRUNCATE agente_tools RESTART IDENTITY")
        for a, t in [("tickets", "get_student_info"), ("tickets", "update_student_email")]:
            cur.execute("INSERT INTO agente_tools (agente, tool) VALUES (%s, %s)", (a, t))
    conn.close()
    yield


async def test_tools_habilitados(db_limpo):
    async with AsyncSessionLocal() as s:
        assert await agent_tools.tools_habilitados(s, "tickets") == ["get_student_info", "update_student_email"]
        assert await agent_tools.tools_habilitados(s, "sigaa") == []


async def test_set_habilitado_desliga_e_liga(db_limpo):
    async with AsyncSessionLocal() as s:
        ok = await agent_tools.set_habilitado(s, "tickets", "update_student_email", False, admin="a")
        await s.commit()
    assert ok is True
    async with AsyncSessionLocal() as s:
        assert await agent_tools.tools_habilitados(s, "tickets") == ["get_student_info"]

    async with AsyncSessionLocal() as s:
        await agent_tools.set_habilitado(s, "tickets", "update_student_email", True, admin="a")
        await s.commit()
    async with AsyncSessionLocal() as s:
        assert "update_student_email" in await agent_tools.tools_habilitados(s, "tickets")


async def test_set_habilitado_binding_inexistente_retorna_false(db_limpo):
    async with AsyncSessionLocal() as s:
        ok = await agent_tools.set_habilitado(s, "tickets", "capability_fantasma", True)
        await s.commit()
    assert ok is False


async def test_upsert_binding_from_code_nao_mexe_no_habilitado(db_limpo):
    async with AsyncSessionLocal() as s:
        await agent_tools.set_habilitado(s, "tickets", "get_student_info", False, admin="a")
        await s.commit()
    # bootstrap roda de novo → upsert dos mesmos bindings
    async with AsyncSessionLocal() as s:
        await agent_tools.upsert_binding_from_code(s, "tickets", ["get_student_info", "update_student_email", "update_student_telefone"])
        await s.commit()
    async with AsyncSessionLocal() as s:
        habilitados = await agent_tools.tools_habilitados(s, "tickets")
    assert "get_student_info" not in habilitados          # toggle do admin preservado
    assert "update_student_telefone" in habilitados        # binding novo entra habilitado
