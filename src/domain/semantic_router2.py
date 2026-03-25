"""
domain/semantic_router.py — Roteamento Semântico v2 (HyDE Routing)
====================================================================
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from src.domain.entities import EstadoMenu, Rota
from src.domain.ports.router_storage import IRouterStorage, ResultadoBuscaRouter

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────────────────────────────────────
THRESHOLD_ALTA   = 0.80
THRESHOLD_MEDIA  = 0.62
THRESHOLD_MINIMO = 0.40

# ─────────────────────────────────────────────────────────────────────────────
# HyDE Routing — Queries de Exemplo por Tool
# ─────────────────────────────────────────────────────────────────────────────
_QUERIES_POR_TOOL: dict[str, list[str]] = {
    # (MANTENHA AQUI TODO O SEU DICIONÁRIO ORIGINAL DE QUERIES - OMITI PARA BREVIDADE)
    "consultar_calendario_academico": ["quando é a matrícula?", "quando começa o semestre?", ...],
    "consultar_edital_paes_2026":     ["quantas vagas tem engenharia civil?", "o que é cota BR-Q?", ...],
    "consultar_contatos_uema":        ["qual o email da PROG?", "telefone da secretaria", ...],
    "consultar_wiki_ctic":            ["como acesso o SIGAA?", "esqueci minha senha do SIGAA", ...],
}

_TOOL_PARA_ROTA: dict[str, Rota] = {
    "consultar_calendario_academico": Rota.CALENDARIO,
    "consultar_edital_paes_2026":     Rota.EDITAL,
    "consultar_contatos_uema":        Rota.CONTATOS,
    "consultar_wiki_ctic":            Rota.WIKI if hasattr(Rota, "WIKI") else Rota.GERAL,
}

_ESTADO_PARA_ROTA: dict[EstadoMenu, Rota] = {
    EstadoMenu.SUB_CALENDARIO: Rota.CALENDARIO,
    EstadoMenu.SUB_EDITAL:     Rota.EDITAL,
    EstadoMenu.SUB_CONTATOS:   Rota.CONTATOS,
}

@dataclass
class ResultadoRoteamento:
    rota:      Rota
    tool_name: str | None = None
    score:     float      = 0.0
    confianca: str        = "baixa"    # "alta" | "media" | "baixa"
    metodo:    str        = "semantico"

    @property
    def usar_tool_diretamente(self) -> bool:
        return self.confianca == "alta"

# ─────────────────────────────────────────────────────────────────────────────
# Registo (Agora injectado com IRouterStorage)
# ─────────────────────────────────────────────────────────────────────────────

async def registar_tools_async(tools: list, storage: IRouterStorage) -> None:
    """Regista as tools no banco vetorial usando a interface."""
    tool_names = {getattr(tool, "name", None) for tool in tools if getattr(tool, "name", None)}
    
    total_registadas = 0
    total_queries = 0

    for tool_name in tool_names:
        queries = _QUERIES_POR_TOOL.get(tool_name)
        if not queries:
            # Fallback para a descrição da tool
            desc = next((getattr(t, "description", tool_name) for t in tools if getattr(t, "name") == tool_name), tool_name)
            queries = [desc[:200]]
            logger.warning("⚠️ '%s' sem queries de exemplo. Adicione em _QUERIES_POR_TOOL.", tool_name)

        logger.debug("📌 Registando '%s': %d queries de exemplo", tool_name, len(queries))
        
        # Delegamos o trabalho sujo (embeddings e salvamento) para a infraestrutura!
        inseridas = await storage.registrar_queries_tool_async(tool_name, queries)
        
        total_queries += inseridas
        total_registadas += 1

    logger.info("🗺️ HyDE Routing: %d tools | %d queries indexadas", total_registadas, total_queries)


# ─────────────────────────────────────────────────────────────────────────────
# Roteamento Principal
# ─────────────────────────────────────────────────────────────────────────────

async def rotear_async(
    texto: str,
    storage: IRouterStorage, # INJEÇÃO DE DEPENDÊNCIA
    estado_menu: EstadoMenu = EstadoMenu.MAIN,
) -> ResultadoRoteamento:
    """Determina a Rota mais adequada para o texto dado."""
    
    # 1. Submenu activo
    if estado_menu in _ESTADO_PARA_ROTA:
        rota = _ESTADO_PARA_ROTA[estado_menu]
        return ResultadoRoteamento(rota=rota, score=1.0, confianca="alta", metodo="estado_menu")

    # 2. KNN Semântico (via Infraestrutura)
    try:
        resultado = await _busca_tool_semantica_async(texto, storage)
        if resultado:
            return resultado
    except Exception as e:
        logger.warning("⚠️ Roteamento semântico falhou: %s", e)

    # 3. Fallback regex (Síncrono)
    return _fallback_regex(texto, estado_menu)


async def _busca_tool_semantica_async(texto: str, storage: IRouterStorage) -> ResultadoRoteamento | None:
    vazio = await storage.verificar_indice_vazio_async()
    if vazio:
        return None

    # O storage faz o embedding do texto e a busca vetorial!
    resultados = await storage.buscar_tool_semelhante_async(texto, limit=1)
    
    if not resultados:
        return None

    top = resultados[0]
    similarity = top["score"]
    tool_name = top["tool_name"]

    logger.info(
        "🎯 HyDE Routing | query='%.35s' → tool='%s' | score=%.3f",
        texto, tool_name, similarity,
    )

    if similarity < THRESHOLD_MINIMO:
        return ResultadoRoteamento(rota=Rota.GERAL, score=similarity, confianca="baixa")

    rota = _TOOL_PARA_ROTA.get(tool_name, Rota.GERAL)
    confianca = "alta" if similarity >= THRESHOLD_ALTA else "media" if similarity >= THRESHOLD_MEDIA else "baixa"

    return ResultadoRoteamento(rota=rota, tool_name=tool_name, score=similarity, confianca=confianca)

def _fallback_regex(texto: str, estado_menu: EstadoMenu) -> ResultadoRoteamento:
    from src.domain.router import analisar
    try:
        rota = analisar(texto, estado_menu)
        return ResultadoRoteamento(rota=rota, score=0.0, confianca="media", metodo="fallback_regex")
    except Exception:
        return ResultadoRoteamento(rota=Rota.GERAL, score=0.0, confianca="baixa", metodo="fallback_regex")