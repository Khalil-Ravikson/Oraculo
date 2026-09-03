"""
infrastructure/route_registry.py — mapa rota→EXECUÇÃO (runtime, sem restart)
================================================================================
Plano A / Fase 2 + ADR 0008. Fonte única de "dada a rota fina, qual nó do
grafo é o ponto de entrada, com que doc_type/k, se é cacheável, se permite
detour, e qual agente (kill-switch)".

Mesma mecânica de `dynamic_config.py`:

    Redis (espelho de leitura rápida)  →  Postgres (fonte de verdade)  →  _DEFAULTS

`get()` é SÍNCRONO (caminho quente: supervisor, semantic_cache, entrypoint).
`aget()` é async com read-repair. `hydrate_redis()` roda no boot do FastAPI e
de cada worker Celery. Nenhuma leitura levanta — cai em `_DEFAULTS` (as 12
rotas hardcoded). Rota fora de `ROTAS` cai em `_UNKNOWN` (nó `rag` do grafo).

NÃO cobre CLASSIFICAÇÃO — regex/embeddings/`router:config` continuam em
`intents_router` (migration 003), intocada.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, replace

logger = logging.getLogger(__name__)

_PREFIXO_REDIS = "route:"

# Valores válidos de `entrypoint_node` = as chaves do dict de conditional edges
# em `src/application/orchestration/builder.py::build_graph()` (o que vira `state.route`).
NODES_ENTRYPOINT: frozenset[str] = frozenset({
    "rag", "ticket", "crud", "greeting", "sigaa", "media_download", "check_status",
    "human_handoff",
})

# ADR 0008 Fase 3: `dispatcher.py` legado foi deletado — todo assunto roda
# no grafo. `owner` fica só como registro (todas as rotas = "langgraph").
OWNERS_VALIDOS: frozenset[str] = frozenset({"langgraph"})

# Campos que o Hub pode editar (o resto é identidade/auditoria).
CAMPOS_EDITAVEIS: frozenset[str] = frozenset({
    "entrypoint_node", "owner", "agente", "cacheavel", "permite_detour",
    "doc_type", "k",
})
_MAX_LEN = {"entrypoint_node": 40, "owner": 24, "agente": 50, "doc_type": 30}


@dataclass(frozen=True)
class RouteConfig:
    rota: str
    entrypoint_node: str
    owner: str
    agente: str | None
    cacheavel: bool
    permite_detour: bool
    doc_type: str | None
    k: int | None
    versao: int = 1


def to_dict(cfg: RouteConfig) -> dict:
    """RouteConfig → dict JSON-serializável (fonte única da serialização —
    usada pelo espelho Redis, pelo snapshot do Hub e pelo histórico do repo)."""
    return asdict(cfg)


def _rc(rota, entrypoint_node, owner, agente, cacheavel, permite_detour, doc_type, k):
    return RouteConfig(
        rota=rota, entrypoint_node=entrypoint_node, owner=owner, agente=agente,
        cacheavel=cacheavel, permite_detour=permite_detour, doc_type=doc_type, k=k,
        versao=1,
    )


# Fallback hardcoded. Espelha o seed das migrations 010 + 022 + 023.
# `test_route_registry` trava a paridade contra elas.
_DEFAULTS: dict[str, RouteConfig] = {
    "GERAL":           _rc("GERAL", "rag", "langgraph", "academic_knowledge", True, True, "geral", 6),
    "CALENDARIO":      _rc("CALENDARIO", "rag", "langgraph", "academic_knowledge", True, True, "calendario", 8),
    "EDITAL":          _rc("EDITAL", "rag", "langgraph", "academic_knowledge", True, True, "edital", 10),
    "CONTATOS":        _rc("CONTATOS", "rag", "langgraph", "academic_knowledge", True, True, "contatos", 6),
    "WIKI":            _rc("WIKI", "rag", "langgraph", "academic_knowledge", True, True, "wiki_ctic", 6),
    "CRUD":            _rc("CRUD", "crud", "langgraph", "tickets", False, False, None, 0),
    "TICKET_ABERTURA": _rc("TICKET_ABERTURA", "ticket", "langgraph", "tickets", True, False, None, 0),
    "GREETING":        _rc("GREETING", "greeting", "langgraph", None, False, False, None, 0),
    "SIGAA":           _rc("SIGAA", "sigaa", "langgraph", "sigaa", False, False, None, 0),
    "MEDIA_DOWNLOAD":  _rc("MEDIA_DOWNLOAD", "media_download", "langgraph", None, False, False, None, 0),
    "CHECK_STATUS":    _rc("CHECK_STATUS", "check_status", "langgraph", None, False, False, None, 0),
    # ADR 0008 Fase 2: escalonamento pra atendente humano. owner="langgraph"
    # (nó nativo, sem flag), agente=NULL (utilitário, sempre ligado), nunca
    # cacheável, não permite detour (é terminal).
    "ESCALAR_HUMANO":  _rc("ESCALAR_HUMANO", "human_handoff", "langgraph", None, False, False, None, 0),
}

ROTAS: frozenset[str] = frozenset(_DEFAULTS)

# As rotas fixas que vivem no código — nunca deletáveis pelo painel. Rotas
# criadas em /hub/routes existem só no Postgres (+ espelho Redis) e podem ser
# apagadas. `ROTAS` continua sendo só as fixas (usado por checagens síncronas
# do caminho quente que não têm sessão de banco).
DEFAULTS_FIXOS: frozenset[str] = frozenset(_DEFAULTS)

_RE_NOME_ROTA = re.compile(r"^[A-Z][A-Z0-9_]{2,23}$")


def validar_nome_rota(nome: str) -> str:
    nome = (nome or "").strip().upper()
    if not _RE_NOME_ROTA.match(nome):
        raise CamposInvalidos(
            "Nome de rota: 3–24 caracteres, MAIÚSCULAS, dígitos ou '_', "
            "começando por letra."
        )
    if nome in DEFAULTS_FIXOS:
        raise CamposInvalidos(f"'{nome}' é uma rota fixa — escolha outro nome.")
    return nome


def pode_apagar(rota: str) -> bool:
    return rota not in DEFAULTS_FIXOS

# Rota classificada mas não registrada (ex.: intent custom adicionada por um
# operador). Cai no nó `rag` do grafo, cacheável, sem detour.
_UNKNOWN: RouteConfig = _rc(
    "?", "rag", "langgraph", None, True, False, "geral", 6,
)

# Rota canônica por node de entrada (reverso, p/ o caminho de resume do
# entrypoint). Ambíguo só p/ "rag" → resolve pra GERAL.
_NODE_PARA_ROTA: dict[str, str] = {
    "rag": "GERAL", "ticket": "TICKET_ABERTURA", "crud": "CRUD",
    "greeting": "GREETING", "sigaa": "SIGAA",
    "media_download": "MEDIA_DOWNLOAD", "check_status": "CHECK_STATUS",
    "human_handoff": "ESCALAR_HUMANO",
}


class CamposInvalidos(ValueError):
    pass


# ─── Serialização Redis ──────────────────────────────────────────────────────

def _redis_key(rota: str) -> str:
    return f"{_PREFIXO_REDIS}{rota}"


def _from_dict(d: dict) -> RouteConfig:
    return RouteConfig(
        rota=d["rota"], entrypoint_node=d["entrypoint_node"], owner=d["owner"],
        agente=d.get("agente"), cacheavel=bool(d["cacheavel"]),
        permite_detour=bool(d.get("permite_detour", False)),
        doc_type=d.get("doc_type"), k=d.get("k"),
        versao=int(d.get("versao", 1)),
    )


def _ler_redis(rota: str) -> RouteConfig | None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        raw = get_redis_text().get(_redis_key(rota))
        if not raw:
            return None
        return _from_dict(json.loads(raw if isinstance(raw, str) else raw.decode()))
    except Exception as exc:
        logger.warning("⚠️  [ROUTE_REGISTRY] Falha ao ler rota '%s' no Redis: %s", rota, exc)
        return None


def espelhar_redis(cfg: RouteConfig) -> None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        get_redis_text().set(_redis_key(cfg.rota), json.dumps(to_dict(cfg), ensure_ascii=False))
    except Exception as exc:
        logger.warning("⚠️  [ROUTE_REGISTRY] Falha ao espelhar rota '%s' no Redis: %s", cfg.rota, exc)


def espelhar_varias(cfgs: list[RouteConfig]) -> int:
    for cfg in cfgs:
        espelhar_redis(cfg)
    return len(cfgs)


# ─── Leitura síncrona (caminho quente): Redis → _DEFAULTS ────────────────────

def _default(rota: str) -> RouteConfig:
    conf = _DEFAULTS.get(rota)
    return conf if conf is not None else replace(_UNKNOWN, rota=rota)


def get(rota: str) -> RouteConfig:
    """RouteConfig da rota. Redis → _DEFAULTS. Nunca levanta. Rota fora de
    `ROTAS` → `_UNKNOWN` (nó `rag` do grafo)."""
    return _ler_redis(rota) or _default(rota)


def rota_do_node(node: str) -> str:
    """Reverso node→rota canônica (caminho de resume do entrypoint)."""
    return _NODE_PARA_ROTA.get(node, (node or "GERAL").upper())


# ─── Leitura assíncrona com read-repair ─────────────────────────────────────

async def _obter_postgres(rota: str) -> RouteConfig | None:
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from src.infrastructure.repositories.route_registry_repository import RouteRegistryRepository
        async with AsyncSessionLocal() as session:
            return await RouteRegistryRepository(session).obter(rota)
    except Exception as exc:
        logger.warning("⚠️  [ROUTE_REGISTRY] Falha ao ler rota '%s' no Postgres: %s", rota, exc)
        return None


async def aget(rota: str) -> RouteConfig:
    cfg = _ler_redis(rota)
    if cfg is None:
        cfg = await _obter_postgres(rota)
        if cfg is not None:
            espelhar_redis(cfg)
    return cfg or _default(rota)


# ─── Hydrate ────────────────────────────────────────────────────────────────

async def hydrate_redis() -> int:
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from src.infrastructure.repositories.route_registry_repository import RouteRegistryRepository
        async with AsyncSessionLocal() as session:
            cfgs = await RouteRegistryRepository(session).listar()
    except Exception as exc:
        logger.warning("⚠️  [ROUTE_REGISTRY] hydrate_redis falhou ao ler Postgres: %s", exc)
        return 0
    n = espelhar_varias(cfgs)
    logger.info("🔧 [ROUTE_REGISTRY] %d rota(s) espelhada(s) no Redis.", n)
    return n


# ─── Hub: merge + validação de escrita ──────────────────────────────────────

def snapshot(cfgs: list[RouteConfig]) -> list[dict]:
    """Une as linhas do Postgres com `_DEFAULTS`. Rota fixa ainda não gravada
    aparece com o default e `versao: 0`. Rota personalizada (só Postgres)
    aparece com `fixa: False`."""
    por_rota = {c.rota: c for c in cfgs}
    saida = []
    for rota in _DEFAULTS:
        cfg = por_rota.get(rota)
        d = to_dict(cfg or _DEFAULTS[rota])
        d["versao"] = cfg.versao if cfg else 0
        d["fixa"] = True
        saida.append(d)
    for rota, cfg in sorted(por_rota.items()):
        if rota in _DEFAULTS:
            continue
        d = to_dict(cfg)
        d["versao"] = cfg.versao
        d["fixa"] = False
        saida.append(d)
    return saida


def validar_campos(campos: dict) -> dict:
    """Valida os campos vindos do POST admin. Devolve o dict normalizado.
    Levanta `CamposInvalidos`."""
    desconhecidos = set(campos) - CAMPOS_EDITAVEIS
    if desconhecidos:
        raise CamposInvalidos(f"Campos não editáveis: {sorted(desconhecidos)}")

    out = dict(campos)

    if "owner" in out and out["owner"] not in OWNERS_VALIDOS:
        raise CamposInvalidos(f"owner deve ser um de {sorted(OWNERS_VALIDOS)}")

    if "entrypoint_node" in out and out["entrypoint_node"] not in NODES_ENTRYPOINT:
        raise CamposInvalidos(
            f"entrypoint_node deve ser um node real do grafo: {sorted(NODES_ENTRYPOINT)}"
        )

    if out.get("agente"):
        nomes = set()
        try:
            from src.agents.registry import registry
            nomes = {a.name for a in registry.all()}
        except Exception:
            pass
        nomes = nomes or {"academic_knowledge", "sigaa", "tickets", "conversation"}
        if out["agente"] not in nomes:
            raise CamposInvalidos(f"agente '{out['agente']}' não existe no registry: {sorted(nomes)}")
    elif "agente" in out and not out["agente"]:
        out["agente"] = None

    if "k" in out and out["k"] is not None:
        try:
            out["k"] = int(out["k"])
        except (TypeError, ValueError):
            raise CamposInvalidos("k deve ser inteiro ou nulo")
        if out["k"] < 0:
            raise CamposInvalidos("k não pode ser negativo")

    for b in ("cacheavel", "permite_detour"):
        if b in out:
            out[b] = bool(out[b])

    for campo, limite in _MAX_LEN.items():
        if isinstance(out.get(campo), str) and len(out[campo]) > limite:
            raise CamposInvalidos(f"{campo} não pode ter mais de {limite} caracteres")

    return out


def merge_default(rota: str, campos: dict) -> RouteConfig:
    """RouteConfig resultante de aplicar `campos` sobre o default da rota —
    usado quando a linha ainda não existe no Postgres. Rota personalizada
    (fora de `_DEFAULTS`) parte de `_UNKNOWN` (nó `rag`)."""
    base = _DEFAULTS.get(rota) or replace(_UNKNOWN, rota=rota)
    return replace(base, **campos)
