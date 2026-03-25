"""
memory/memory_extractor.py — Extração de Fatos em Background
========================================================================
"""
from __future__ import annotations

import logging
import time
from pydantic import BaseModel

from src.domain.ports.llm_provider import ILLMProvider
from src.memory.working_memory import get_ultimos_n_turns, get_sinais, set_sinal
from src.memory.long_term_memory import guardar_fatos_batch
from src.agent.prompts import PROMPT_EXTRACAO_FATOS

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────
_MIN_TURNS_PARA_EXTRACAO  = 2     
_COOLDOWN_EXTRACAO_S      = 120   
_TURNS_PARA_ANALISE       = 6     
_SINAL_ULTIMA_EXTRACAO    = "ultima_extracao_ts"
_MIN_CHARS_FATO           = 15    

_PREFIXOS_META = (
    "perguntou sobre", "questionou sobre", "demonstrou interesse",
    "demonstrou dúvida", "o aluno perguntou", "o utilizador perguntou",
    "bot respondeu", "assistente informou", "não foi possível",
    "informação não disponível",
)

_TERMOS_DE_FATO = frozenset({
    "curso", "turno", "semestre", "matrícula", "inscri",
    "categoria", "campus", "período", "trancamento", "reingresso",
    "veterano", "calouro", "engenharia", "direito", "medicina",
    "paes", "br-ppi", "br-q", "pcd", "noturno", "diurno",
    "coordenação", "departamento", "bolsa", "auxílio",
    "dificuldade", "dúvida recorrente", "frequência",
})

class ExtracaoFatosSchema(BaseModel):
    fatos: list[str]

# ─────────────────────────────────────────────────────────────────────────────
# API Principal (Exposta para o AgentCore)
# ─────────────────────────────────────────────────────────────────────────────

async def extrair_fatos_do_ultimo_turn_async(
    user_id: str, 
    session_id: str, 
    llm_provider: ILLMProvider
) -> int:
    """Ponto de entrada público para iniciar a extração em background."""
    return await _extrair_com_seguranca_async(user_id, session_id, llm_provider)


# ─────────────────────────────────────────────────────────────────────────────
# Lógica Interna
# ─────────────────────────────────────────────────────────────────────────────

async def _extrair_com_seguranca_async(
    user_id: str, 
    session_id: str, 
    llm_provider: ILLMProvider
) -> int:
    """Verifica pré-condições antes de chamar a extração."""

    # ── Verifica cooldown ─────────────────────────────────────────────────────
    sinais = get_sinais(session_id)
    try:
        ultima_ts = float(sinais.get(_SINAL_ULTIMA_EXTRACAO, "0"))
    except ValueError:
        ultima_ts = 0.0

    agora = time.time()
    restante = _COOLDOWN_EXTRACAO_S - (agora - ultima_ts)
    if restante > 0:
        logger.debug("⏳ Extração em cooldown [%s]: %.0fs restantes", user_id, restante)
        return 0

    # ── Verifica turns suficientes ────────────────────────────────────────────
    turns = get_ultimos_n_turns(session_id, n=_TURNS_PARA_ANALISE)
    n_user_turns = sum(1 for t in turns if t.get("role") == "user")

    if n_user_turns < _MIN_TURNS_PARA_EXTRACAO:
        logger.debug(
            "ℹ️  Poucos turns [%s]: %d/%d",
            user_id, n_user_turns, _MIN_TURNS_PARA_EXTRACAO,
        )
        return 0

    # ── Executa e actualiza cooldown ──────────────────────────────────────────
    # Repassamos o llm_provider para quem vai fazer o trabalho pesado
    guardados = await _executar_extracao_async(user_id, session_id, turns, llm_provider)

    # Actualiza sempre (mesmo com 0 fatos) para evitar re-tentativa imediata
    set_sinal(session_id, _SINAL_ULTIMA_EXTRACAO, str(agora))

    return guardados


async def _executar_extracao_async(
    user_id: str,
    session_id: str,
    turns: list[dict],
    llm_provider: ILLMProvider
) -> int:
    """Executa a extração de fatos via Provider Genérico."""

    if not turns:
        return 0

    conversa_formatada = _formatar_conversa(turns)
    if not conversa_formatada or len(conversa_formatada) < 50:
        return 0

    prompt = PROMPT_EXTRACAO_FATOS.format(conversa=conversa_formatada)

    # ── Chama Interface com Structured Output nativo ─────────────────────────
    resultado = await llm_provider.gerar_resposta_estruturada_async(
        prompt=prompt,
        response_schema=ExtracaoFatosSchema,
        temperatura=0.05,
    )

    if not resultado:
        logger.debug("ℹ️  Extração sem resultado [%s]", user_id)
        return 0

    # CORREÇÃO CRÍTICA AQUI: Acessando via Objeto Pydantic em vez de dicionário
    fatos_brutos: list[str] = resultado.fatos
    
    if not fatos_brutos:
        return 0

    # ── Valida e filtra ───────────────────────────────────────────────────────
    fatos_validos = _validar_fatos(fatos_brutos)
    if not fatos_validos:
        logger.debug(
            "ℹ️  Todos os %d fatos candidatos foram filtrados [%s]",
            len(fatos_brutos), user_id
        )
        return 0

    # ── Guarda na Long-Term Memory ────────────────────────────────────────────
    guardados = guardar_fatos_batch(user_id, fatos_validos)

    if guardados:
        logger.info(
            "🧠 Fatos extraídos [%s]: %d novos / %d candidatos / %d filtrados",
            user_id, guardados, len(fatos_brutos),
            len(fatos_brutos) - len(fatos_validos),
        )

    return guardados


def _formatar_conversa(turns: list[dict]) -> str:
    """Formata turns para o prompt de extração em formato compacto."""
    linhas = []
    for turn in turns:
        role    = turn.get("role", "")
        content = turn.get("content", "").strip()
        if not content:
            continue
        if role == "user":
            linhas.append(f"Aluno: {content[:250]}")
        elif role == "assistant":
            linhas.append(f"Bot: {content[:150]}")
    return "\n".join(linhas)


def _validar_fatos(fatos_brutos: list[str]) -> list[str]:
    """Filtra fatos inválidos, genéricos ou que sejam meta-descrições."""
    fatos_validos: list[str] = []

    for item in fatos_brutos:
        if not isinstance(item, str):
            continue

        fato = item.strip()
        fato_lower = fato.lower()

        if len(fato) < _MIN_CHARS_FATO:
            logger.debug("🔍 Fato descartado (curto): %r", fato)
            continue

        if fato.endswith("?"):
            logger.debug("🔍 Fato descartado (pergunta): %r", fato)
            continue

        if any(fato_lower.startswith(p) for p in _PREFIXOS_META):
            logger.debug("🔍 Fato descartado (meta-descrição): %r", fato)
            continue

        if not any(termo in fato_lower for termo in _TERMOS_DE_FATO):
            logger.debug("🔍 Fato descartado (sem termo de domínio): %r", fato)
            continue

        fatos_validos.append(fato)

    return fatos_validos