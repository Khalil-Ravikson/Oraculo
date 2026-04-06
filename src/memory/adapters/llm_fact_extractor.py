"""
src/memory/adapters/llm_fact_extractor.py
-------------------------------------------
Extrator de fatos via LLM com filtros de validação robustos.

MELHORIAS vs memory_extractor.py anterior:
  - Implementa IFactExtractor (não depende de Gemini diretamente)
  - LLMProvider injetado (qualquer LLM funciona)
  - Cooldown por sessão via Redis (evita chamadas excessivas)
  - Filtros de validação em método puro (testável sem LLM)
  - Structured output com Pydantic
  - RegexFactExtractor como alternativa 0-token
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from pydantic import BaseModel

from ..ports.fact_extractor_port import IFactExtractor
from ..ports.long_term_port import Fato
from ..ports.working_memory_port import ConversationTurn

logger = logging.getLogger(__name__)

_COOLDOWN_S = 120
_MIN_TURNS = 2
_MIN_CHARS_FATO = 15

_PREFIXOS_META = (
    "perguntou sobre", "questionou sobre", "demonstrou interesse",
    "o aluno perguntou", "o utilizador perguntou", "bot respondeu",
    "assistente informou", "não foi possível", "informação não disponível",
)

_TERMOS_DOMINIO = frozenset({
    "curso", "turno", "semestre", "matrícula", "inscri",
    "categoria", "campus", "período", "trancamento", "reingresso",
    "veterano", "calouro", "engenharia", "direito", "medicina",
    "paes", "br-ppi", "br-q", "pcd", "noturno", "diurno",
    "coordenação", "departamento", "bolsa", "auxílio", "sigaa",
    "dificuldade", "dúvida recorrente", "frequência",
})

_PROMPT_EXTRACAO = """Analise a conversa abaixo e extraia fatos ESTÁTICOS e de LONGO PRAZO sobre o aluno.

REGRAS:
1. Foque em: Curso, Centro, Vínculo (Calouro/Veterano), Turno, Problemas recorrentes
2. Ignore interesses passageiros ou perguntas pontuais
3. NÃO descreva a conversa — extraia fatos SOBRE O ALUNO
4. Se não houver fatos, retorne lista vazia

<conversa>
{conversa}
</conversa>"""


class _ExtracaoSchema(BaseModel):
    fatos: list[str]


class LLMFactExtractor(IFactExtractor):
    """
    Extrator de fatos usando LLM com cooldown e validação.
    Aceita qualquer provider que implemente gerar_resposta_estruturada_async().
    """

    def __init__(
        self,
        llm_provider: Any,
        redis_client: Any | None = None,
        cooldown_s: int = _COOLDOWN_S,
        min_turns: int = _MIN_TURNS,
    ):
        self._llm = llm_provider
        self._redis = redis_client
        self._cooldown_s = cooldown_s
        self._min_turns = min_turns

    def extract(self, user_id: str, turns: list[ConversationTurn]) -> list[Fato]:
        """Extrai fatos com cooldown e filtros de qualidade."""
        if len([t for t in turns if t.role == "user"]) < self._min_turns:
            return []

        if not self._pode_extrair(user_id):
            return []

        conversa = self._formatar_conversa(turns)
        if len(conversa) < 50:
            return []

        try:
            import asyncio
            resultado = asyncio.run(self._llm.gerar_resposta_estruturada_async(
                prompt=_PROMPT_EXTRACAO.format(conversa=conversa),
                response_schema=_ExtracaoSchema,
                temperatura=0.05,
            ))
        except Exception as e:
            logger.warning("⚠️  LLMFactExtractor.extract [%s]: %s", user_id, e)
            return []

        if not resultado:
            return []

        fatos_brutos = resultado.fatos if hasattr(resultado, "fatos") else resultado.get("fatos", [])
        fatos_validos = validar_fatos(fatos_brutos)

        self._atualizar_cooldown(user_id)

        return [
            Fato(texto=texto, user_id=user_id, source="extractor")
            for texto in fatos_validos
        ]

    def _pode_extrair(self, user_id: str) -> bool:
        if not self._redis:
            return True
        try:
            key = f"ltm:extract_ts:{user_id}"
            ultima = self._redis.get(key)
            if ultima:
                ts = float(ultima if isinstance(ultima, str) else ultima.decode())
                if time.time() - ts < self._cooldown_s:
                    return False
        except Exception:
            pass
        return True

    def _atualizar_cooldown(self, user_id: str) -> None:
        if not self._redis:
            return
        try:
            key = f"ltm:extract_ts:{user_id}"
            self._redis.setex(key, self._cooldown_s * 2, str(time.time()))
        except Exception:
            pass

    def _formatar_conversa(self, turns: list[ConversationTurn]) -> str:
        linhas = []
        for t in turns[-12:]:  # max 6 pares
            pref = "Aluno" if t.role == "user" else "Bot"
            linhas.append(f"{pref}: {t.content[:250]}")
        return "\n".join(linhas)


class RegexFactExtractor(IFactExtractor):
    """
    Extrator de fatos por padrões regex — 0 tokens, 0ms de rede.
    Extrai fatos óbvios que o aluno menciona explicitamente.
    Complementa o LLMFactExtractor (rodar em conjunto).
    """

    _PADROES = [
        (re.compile(r"(curso|faço|estudo|sou)\s+(?:de\s+)?([A-ZÁÉÍÓÚ][a-záéíóú\s]{3,30})", re.I), "curso"),
        (re.compile(r"(noturno|diurno|matutino|vespertino)", re.I), "turno"),
        (re.compile(r"(veterano|calouro|ingress[ei])", re.I), "vinculo"),
        (re.compile(r"(CECEN|CESB|CESC|CCSA|CEEA|CCS|CCT)", re.I), "centro"),
        (re.compile(r"(20\d{2}\.[12])", re.I), "semestre"),
    ]

    def extract(self, user_id: str, turns: list[ConversationTurn]) -> list[Fato]:
        fatos = []
        textos_user = " ".join(t.content for t in turns if t.role == "user")

        for padrao, tipo in self._PADROES:
            match = padrao.search(textos_user)
            if match:
                texto = match.group(0).strip()
                if len(texto) >= _MIN_CHARS_FATO:
                    fatos.append(Fato(texto=f"[{tipo}] {texto}", user_id=user_id, source="regex"))

        return fatos


def validar_fatos(brutos: list) -> list[str]:
    """
    Valida e filtra fatos extraídos pelo LLM.
    Função pura — testável sem LLM nem Redis.
    """
    validos: list[str] = []
    for item in brutos:
        if not isinstance(item, str):
            continue
        fato = item.strip()
        fl = fato.lower()

        if len(fato) < _MIN_CHARS_FATO:
            continue
        if fato.endswith("?"):
            continue
        if any(fl.startswith(p) for p in _PREFIXOS_META):
            continue
        if not any(t in fl for t in _TERMOS_DOMINIO):
            continue

        validos.append(fato)
    return validos


class CompositeFactExtractor(IFactExtractor):
    """
    Combina múltiplos extratores (Composite Pattern).
    Exemplo: RegexFactExtractor (0 tokens) + LLMFactExtractor (quando há histórico suficiente).
    Adicionar um novo extrator = não alterar código existente.
    """

    def __init__(self, extractors: list[IFactExtractor]):
        self._extractors = extractors

    def extract(self, user_id: str, turns: list[ConversationTurn]) -> list[Fato]:
        vistos: set[str] = set()
        todos: list[Fato] = []
        for extractor in self._extractors:
            try:
                novos = extractor.extract(user_id, turns)
                for f in novos:
                    if f.hash_id not in vistos:
                        vistos.add(f.hash_id)
                        todos.append(f)
            except Exception as e:
                logger.warning("⚠️  Extractor %s falhou: %s", type(extractor).__name__, e)
        return todos