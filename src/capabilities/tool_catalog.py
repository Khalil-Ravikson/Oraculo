"""
src/capabilities/tool_catalog.py — ferramentas criadas pelo painel
==================================================================
Lê/escreve `tools_catalogo` (migration 016, Hub v2). Complementa o registro
de código (`capabilities/registry.py`): o admin cadastra uma ferramenta pelo
`/hub/capabilities` sem tocar em `tool_*.py`.

Dois tipos (decisão do dono, Sprint 2):
  - `http`: chamada REST definida por dado. URL validada por
    `ssrf_validator.validar_url_publica()` no cadastro.
  - `mcp`: ferramenta exposta por um servidor de `mcp_servers` (migration
    014). Referencia o servidor pelo nome; a URL dele já passou por SSRF.

A EXECUÇÃO fica em `dynamic_tool_executor.py` — este módulo é só o CRUD e a
mesclagem com o registro de código para a UI.

Degrada sem exceção: falha de Postgres → lista vazia (as ferramentas de
código continuam funcionando normalmente).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.models import ToolCatalogo
from src.infrastructure.security.ssrf_validator import URLInseguraError, validar_url_publica

logger = logging.getLogger(__name__)

TIPOS_VALIDOS = ("http", "mcp")
_METODOS_HTTP = ("GET", "POST", "PUT", "PATCH", "DELETE")


class NomeDuplicadoError(ValueError):
    """Já existe uma ferramenta (código ou painel) com esse nome."""


class ConfigInvalidaError(ValueError):
    """`config` não bate com o `tipo` declarado."""


def _validar_config(tipo: str, config: dict) -> dict:
    """Valida e normaliza `config` conforme o tipo. Levanta
    `ConfigInvalidaError`/`URLInseguraError`. Retorna a config normalizada."""
    if tipo == "http":
        metodo = str(config.get("metodo", "GET")).upper()
        if metodo not in _METODOS_HTTP:
            raise ConfigInvalidaError(f"Método HTTP inválido: {metodo}.")
        url = str(config.get("url", "")).strip()
        if not url:
            raise ConfigInvalidaError("Informe a URL da ferramenta.")
        validar_url_publica(url)  # propaga URLInseguraError
        auth = config.get("auth") or {}
        if auth and auth.get("tipo") not in (None, "", "none", "bearer", "api_key"):
            raise ConfigInvalidaError("Tipo de autenticação inválido.")
        return {
            "metodo": metodo,
            "url": url,
            "headers": dict(config.get("headers") or {}),
            "corpo_template": config.get("corpo_template") or "",
            "auth": auth,
            "timeout_s": min(float(config.get("timeout_s", 15) or 15), 60.0),
        }
    if tipo == "mcp":
        servidor = str(config.get("servidor", "")).strip()
        tool_remota = str(config.get("tool_remota", "")).strip()
        if not servidor or not tool_remota:
            raise ConfigInvalidaError("Informe o servidor MCP e o nome da ferramenta remota.")
        return {
            "servidor": servidor,
            "tool_remota": tool_remota,
            "args_template": config.get("args_template") or {},
        }
    raise ConfigInvalidaError(f"Tipo de ferramenta inválido: {tipo}. Use um de {TIPOS_VALIDOS}.")


async def listar(session) -> list[dict]:
    try:
        result = await session.execute(
            select(ToolCatalogo).where(ToolCatalogo.tenant_id.is_(None)).order_by(ToolCatalogo.nome)
        )
        return [_row_dict(r) for r in result.scalars().all()]
    except Exception as exc:  # noqa: BLE001 — fail-open deliberado
        logger.warning("⚠️  [TOOL_CATALOG] Falha ao listar: %s", exc)
        return []


async def obter(session, tool_id: int) -> dict | None:
    r = (await session.execute(
        select(ToolCatalogo).where(ToolCatalogo.id == tool_id, ToolCatalogo.tenant_id.is_(None))
    )).scalar_one_or_none()
    return _row_dict(r) if r else None


async def obter_por_nome(session, nome: str) -> dict | None:
    r = (await session.execute(
        select(ToolCatalogo).where(ToolCatalogo.nome == nome, ToolCatalogo.tenant_id.is_(None))
    )).scalar_one_or_none()
    return _row_dict(r) if r else None


async def criar(
    session, nome: str, tipo: str, config: dict, *,
    descricao: str = "", permissoes: list | None = None,
    confirmacao: bool = False, admin: str | None = None,
) -> dict:
    """Cadastra uma ferramenta nova. Levanta `NomeDuplicadoError`,
    `ConfigInvalidaError` ou `URLInseguraError`."""
    nome = nome.strip()
    if not nome:
        raise ConfigInvalidaError("Informe um nome para a ferramenta.")
    # colisão com ferramenta de código
    from src.capabilities.registry import available as _codigo
    if nome in _codigo():
        raise NomeDuplicadoError(f"Já existe uma ferramenta de código chamada '{nome}'.")

    config_norm = _validar_config(tipo, config)

    registro = ToolCatalogo(
        nome=nome, tipo=tipo, descricao=descricao, config=config_norm,
        permissoes=list(permissoes or []), confirmacao=bool(confirmacao),
        atualizado_por=admin,
    )
    session.add(registro)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise NomeDuplicadoError(f"Já existe uma ferramenta chamada '{nome}'.") from exc
    return _row_dict(registro)


async def set_habilitado(session, tool_id: int, habilitado: bool, admin: str | None = None) -> bool:
    res = await session.execute(
        update(ToolCatalogo)
        .where(ToolCatalogo.id == tool_id, ToolCatalogo.tenant_id.is_(None))
        .values(habilitado=habilitado, atualizado_por=admin,
                atualizado_em=datetime.now(timezone.utc), versao=ToolCatalogo.versao + 1)
    )
    await session.flush()
    return res.rowcount > 0


async def remover(session, tool_id: int) -> bool:
    res = await session.execute(
        delete(ToolCatalogo).where(ToolCatalogo.id == tool_id, ToolCatalogo.tenant_id.is_(None))
    )
    await session.flush()
    return res.rowcount > 0


def mesclar_com_codigo(codigo_manifestos: list, painel_rows: list[dict]) -> list[dict]:
    """Une os manifestos de código (`CapabilityManifest`) com as ferramentas
    do painel, num formato único para a UI. `origem` distingue os dois."""
    saida = [
        {
            "id": None, "origem": "codigo", "nome": m.nome, "tipo": "codigo",
            "descricao": m.descricao, "permissoes": list(m.permissoes),
            "confirmacao": m.confirmacao, "habilitado": True, "config": {},
        }
        for m in codigo_manifestos
    ]
    saida.extend({**r, "origem": "painel"} for r in painel_rows)
    return sorted(saida, key=lambda x: x["nome"])


def _row_dict(r: ToolCatalogo) -> dict:
    return {
        "id": r.id, "nome": r.nome, "tipo": r.tipo, "descricao": r.descricao,
        "config": r.config, "permissoes": r.permissoes, "confirmacao": r.confirmacao,
        "habilitado": r.habilitado, "versao": r.versao,
        "atualizado_em": r.atualizado_em.isoformat() if r.atualizado_em else None,
        "atualizado_por": r.atualizado_por,
    }
