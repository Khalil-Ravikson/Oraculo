# ─────────────────────────────────────────────────────────────────────────────
# FICHEIRO 4: src/memory/adapters/redisvl_session_manager.py
# Responsabilidade: Gestão de sessões de conversação via MessageHistory.
# MUDANÇAS: lpush/lrange manual → MessageHistory do RedisVL.
# ─────────────────────────────────────────────────────────────────────────────

"""
memory/adapters/redisvl_session_manager.py — MessageHistory (RedisVL 0.17.0)
=============================================================================
Substitui a gestão manual de histórico (lpush/lrange no redis_client.py).

IMPORT CORRECTO (0.17.0):
  from redisvl.extensions.message_history import MessageHistory
  (StandardSessionManager foi renomeado — import antigo deprecated)

API DO MESSAGHISTORY:
  - add_message({"role": "user", "content": "..."}, session_tag=user_id)
  - get_recent(top_k=10, session_tag=user_id) → lista de dicts
  - store(prompt, response, session_tag) → guarda par completo
  - clear(session_tag=user_id) → limpa a sessão

NOTA — SINCRONISMO:
  MessageHistory 0.17.0 não tem métodos async nativos (aadd_message, etc.).
  Usamos asyncio.to_thread() — JUSTIFICADO porque as chamadas fazem I/O Redis
  e o overhead de thread é marginal vs a latência de rede.
  O issue está reportado no GitHub redisvl — async support em roadmap.

COMPATIBILIDADE:
  Mantém a mesma interface do working_memory.py anterior para que os
  nodes do LangGraph não precisem de alterações.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from redisvl.extensions.message_history import MessageHistory

from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)

# TTL da sessão: 30 minutos de inactividade → reset automático
_SESSION_TTL    = 1800
_MAX_TURNS      = 10       # sliding window: 10 pares user/assistant
_MAX_CHARS      = 3_000    # budget de tokens (~1200 tokens de histórico)
_MAX_CHARS_MSG  = 400      # trunca mensagens individuais longas
_SESSION_NAME   = "oraculo:sessions"


@dataclass
class TurnoConversa:
    role:      str
    content:   str
    timestamp: int = field(default_factory=lambda: int(time.time()))


@dataclass
class HistoricoCompactado:
    turns:            list[TurnoConversa] = field(default_factory=list)
    texto_formatado:  str = ""
    total_chars:      int = 0
    turns_incluidos:  int = 0

    @classmethod
    def vazio(cls) -> "HistoricoCompactado":
        return cls()

    @property
    def tem_historico(self) -> bool:
        return self.turns_incluidos > 0


class RedisVLSessionManager:
    """
    Gestão de sessões de conversação usando MessageHistory do RedisVL.

    Responsabilidade única: persistir e recuperar histórico de conversa
    com sliding window e token budget aplicados.

    Compatível com a interface esperada pelos nodes do LangGraph.
    """

    def __init__(self) -> None:
        # MessageHistory é criado uma vez — usa connection pool interno
        self._history = MessageHistory(
            name             = _SESSION_NAME,
            redis_url        = settings.REDIS_URL,
        )
        logger.info("✅ [SESSION] RedisVLSessionManager iniciado")

    # ── API pública async ─────────────────────────────────────────────────────

    async def adicionar_turno(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """
        Adiciona um turno ao histórico da sessão.

        Args:
            session_id: ID único da sessão (telefone do utilizador).
            role:       "user" ou "assistant".
            content:    Conteúdo da mensagem.
        """
        # Trunca mensagens longas antes de persistir
        content_truncado = content[:_MAX_CHARS_MSG] + "…" \
            if len(content) > _MAX_CHARS_MSG else content

        mensagem = {
            "role":    role,
            "content": content_truncado,
        }

        try:
            await asyncio.to_thread(
                self._history.add_message,
                mensagem,
                session_tag=session_id,
            )
            logger.debug(
                "💬 [SESSION] Adicionado | role=%s | session=%s",
                role, session_id[-8:],
            )
        except Exception as exc:
            logger.exception(
                "❌ [SESSION] Falha ao adicionar turno | session=%s | erro: %s",
                session_id, exc,
            )

    async def get_historico(self, session_id: str) -> HistoricoCompactado:
        """
        Retorna o histórico compactado dentro do token budget.

        ALGORITMO:
          1. Busca os últimos _MAX_TURNS * 2 mensagens via get_recent()
          2. Aplica truncagem individual por mensagem
          3. Aplica budget total de chars (remove pares mais antigos)
          4. Garante que começa com "user" (Gemini exige)
        """
        try:
            raw_messages: list[dict] = await asyncio.to_thread(
                self._history.get_recent,
                top_k       = _MAX_TURNS * 2,
                session_tag = session_id,
                raw         = True,
            )

            if not raw_messages:
                return HistoricoCompactado.vazio()

            # Normaliza para TurnoConversa
            turns = [
                TurnoConversa(
                    role    = m.get("role", "user"),
                    content = m.get("content", ""),
                )
                for m in raw_messages
                if m.get("content", "").strip()
            ]

            if not turns:
                return HistoricoCompactado.vazio()

            # Aplica budget e sliding window
            turns = self._aplicar_budget(turns)
            turns = self._garantir_inicio_user(turns)

            if not turns:
                return HistoricoCompactado.vazio()

            texto = self._formatar_texto(turns)
            total = sum(len(t.content) for t in turns)

            return HistoricoCompactado(
                turns           = turns,
                texto_formatado = texto,
                total_chars     = total,
                turns_incluidos = sum(1 for t in turns if t.role == "user"),
            )

        except Exception as exc:
            logger.exception(
                "❌ [SESSION] Falha ao obter histórico | session=%s | erro: %s",
                session_id, exc,
            )
            return HistoricoCompactado.vazio()

    async def limpar_sessao(self, session_id: str) -> None:
        """
        Limpa o histórico da sessão.
        Chamado quando o utilizador digita "voltar" ou reinicia a conversa.
        """
        try:
            await asyncio.to_thread(
                self._history.clear,
                session_tag=session_id,
            )
            logger.debug(
                "🗑️  [SESSION] Sessão limpa: %s", session_id[-8:]
            )
        except Exception as exc:
            logger.exception(
                "❌ [SESSION] Falha ao limpar sessão %s: %s",
                session_id, exc,
            )

    async def get_ultimos_turns(
        self,
        session_id: str,
        n: int = 6,
    ) -> list[dict]:
        """
        Retorna os últimos N turnos para o extractor de fatos.
        Formato: [{"role": str, "content": str}, ...]
        """
        try:
            msgs = await asyncio.to_thread(
                self._history.get_recent,
                top_k       = n * 2,
                session_tag = session_id,
                raw         = True,
            )
            return [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in msgs
                if m.get("content", "").strip()
            ]
        except Exception:
            return []

    # ── Algoritmos de compactação (pure Python — sem I/O) ────────────────────

    @staticmethod
    def _aplicar_budget(turns: list[TurnoConversa]) -> list[TurnoConversa]:
        """Remove pares antigos até caber no budget de chars."""
        total = sum(len(t.content) for t in turns)
        if total <= _MAX_CHARS:
            return turns

        # Agrupa em pares user+assistant e remove do início
        resultado = list(turns)
        while resultado and sum(len(t.content) for t in resultado) > _MAX_CHARS:
            if len(resultado) >= 2:
                resultado = resultado[2:]   # remove par completo
            else:
                resultado = resultado[1:]
        return resultado

    @staticmethod
    def _garantir_inicio_user(turns: list[TurnoConversa]) -> list[TurnoConversa]:
        """Remove mensagens do início até começar com 'user'."""
        while turns and turns[0].role != "user":
            turns = turns[1:]
        return turns

    @staticmethod
    def _formatar_texto(turns: list[TurnoConversa]) -> str:
        """Formata histórico como string para injecção no prompt."""
        linhas = []
        for t in turns:
            prefixo = "Aluno" if t.role == "user" else "Assistente"
            linhas.append(f"{prefixo}: {t.content}")
        return "\n".join(linhas)