"""
Camada 1 (continuação) — src/graph/node_config.py (`graph_node_config`,
migration 013). Postgres real (o CI provê). Comportamento validado
manualmente contra o Postgres real do docker-compose antes deste arquivo
ser escrito (curl + docker exec, ver commit).
"""
import pytest

from src.graph import node_config
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
        cur.execute("TRUNCATE graph_node_config RESTART IDENTITY")
    conn.close()
    yield


async def test_listar_vazio_sem_linhas(db_limpo):
    async with AsyncSessionLocal() as s:
        assert await node_config.listar(s) == []


async def test_set_habilitado_cria_linha_se_nao_existir(db_limpo):
    async with AsyncSessionLocal() as s:
        await node_config.set_habilitado(s, "lab_mcp", False, admin="tester")
        await s.commit()

    async with AsyncSessionLocal() as s:
        linhas = await node_config.listar(s)
    assert len(linhas) == 1
    assert linhas[0]["node_id"] == "lab_mcp"
    assert linhas[0]["habilitado"] is False
    assert linhas[0]["versao"] == 1
    assert linhas[0]["atualizado_por"] == "tester"


async def test_set_habilitado_atualiza_linha_existente_incrementa_versao(db_limpo):
    async with AsyncSessionLocal() as s:
        await node_config.set_habilitado(s, "lab_mcp", False, admin="a")
        await s.commit()

    async with AsyncSessionLocal() as s:
        await node_config.set_habilitado(s, "lab_mcp", True, admin="b")
        await s.commit()

    async with AsyncSessionLocal() as s:
        linhas = await node_config.listar(s)
    assert len(linhas) == 1  # upsert, não duplica
    assert linhas[0]["habilitado"] is True
    assert linhas[0]["versao"] == 2
    assert linhas[0]["atualizado_por"] == "b"


async def test_mesclar_com_registry_integra_com_listar_real(db_limpo):
    async with AsyncSessionLocal() as s:
        await node_config.set_habilitado(s, "lab_mcp", False, admin="a")
        await s.commit()

    async with AsyncSessionLocal() as s:
        linhas = await node_config.listar(s)

    nos = [{"id": "lab_mcp", "type": "x", "metadata": {}, "input_ports": [], "output_ports": []},
           {"id": "lab_rest", "type": "x", "metadata": {}, "input_ports": [], "output_ports": []}]
    resultado = node_config.mesclar_com_registry(nos, linhas)
    por_id = {r["id"]: r for r in resultado}
    assert por_id["lab_mcp"]["habilitado"] is False
    assert por_id["lab_rest"]["habilitado"] is True  # nunca tocado
