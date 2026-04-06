"""
src/memory/adapters/redis_working_memory.py
---------------------------------------------
Implementação Redis da IWorkingMemory.

MELHORIAS vs working_memory.py anterior:
  - Implementa IWorkingMemory (testável com mocks)
  - RedisClient injetado no construtor (não usa get_redis_text() global)
  - sliding window + token budget em método privado puro (_compact)
  - Sinais de sessão num Hash separado do histórico (sem colisão de keys)
  - Dados imutáveis: ConversationTurn(frozen=True)
  - Sem dependência de LangChain (usa JSON puro)

ESTRUTURA NO REDIS:
  chat:{session_id}         → List de JSON (histórico)
  signals:{session_id}      → Hash de sinais de contexto
  TTL ambos: 30 min inatividade
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..ports.working_memory_port import (
    ConversationTurn,
    HistoricoCompactado,
    IWorkingMemory,
    Papel,
)

logger = logging.getLogger(__name__)

_TTL_SECONDS = 1800      # 30 min
_MAX_TURNS = 10          # sliding window: max pares user/assistant
_MAX_CHARS = 3_000       # token budget: ~1200 tokens de histórico
_MAX_CHARS_PER_MSG = 400 # trunca mensagens individuais longas
_PREFIX_CHAT = "chat:"
_PREFIX_SIG = "signals:"


class RedisWorkingMemory(IWorkingMemory):
    """
    Implementação Redis da memória de trabalho.
    Recebe o cliente Redis por injeção de dependência.
    """

    def __init__(self, redis_client: Any):
        self._r = redis_client

    # ─────────────────────────────────────────────────────────────────────────

    def add_turn(self, session_id: str, role: Papel, content: str) -> None:
        try:
            key = f"{_PREFIX_CHAT}{session_id}"
            entry = json.dumps({
                "role": role,
                "content": content,
                "ts": int(time.time()),
            }, ensure_ascii=False)
            self._r.rpush(key, entry)
            self._r.ltrim(key, -(_MAX_TURNS * 2), -1)
            self._r.expire(key, _TTL_SECONDS)
        except Exception as e:
            logger.warning("⚠️  WorkingMemory.add_turn [%s]: %s", session_id, e)

    def get_historico(self, session_id: str) -> HistoricoCompactado:
        try:
            key = f"{_PREFIX_CHAT}{session_id}"
            raw = self._r.lrange(key, 0, -1)
            if not raw:
                return HistoricoCompactado.vazio()

            turns = self._deserialize(raw)
            turns = self._compact(turns)

            if not turns:
                return HistoricoCompactado.vazio()

            texto = self._format_as_text(turns)
            return HistoricoCompactado(
                turns=turns,
                texto_formatado=texto,
                total_chars=sum(len(t.content) for t in turns),
                turns_incluidos=sum(1 for t in turns if t.role == "user"),
            )
        except Exception as e:
            logger.warning("⚠️  WorkingMemory.get_historico [%s]: %s", session_id, e)
            return HistoricoCompactado.vazio()

    def clear(self, session_id: str) -> None:
        try:
            self._r.delete(f"{_PREFIX_CHAT}{session_id}")
            self._r.delete(f"{_PREFIX_SIG}{session_id}")
        except Exception as e:
            logger.warning("⚠️  WorkingMemory.clear [%s]: %s", session_id, e)

    def get_recent_turns(self, session_id: str, n: int = 6) -> list[ConversationTurn]:
        try:
            key = f"{_PREFIX_CHAT}{session_id}"
            raw = self._r.lrange(key, -(n * 2), -1)
            return self._deserialize(raw)
        except Exception:
            return []

    def set_signal(self, session_id: str, key: str, value: str) -> None:
        try:
            redis_key = f"{_PREFIX_SIG}{session_id}"
            self._r.hset(redis_key, key, value)
            self._r.expire(redis_key, _TTL_SECONDS)
        except Exception as e:
            logger.warning("⚠️  WorkingMemory.set_signal [%s/%s]: %s", session_id, key, e)

    def get_signals(self, session_id: str) -> dict[str, str]:
        try:
            return dict(self._r.hgetall(f"{_PREFIX_SIG}{session_id}") or {})
        except Exception:
            return {}

    # ─────────────────────────────────────────────────────────────────────────
    # Métodos privados puros (sem IO — fáceis de unit testar)
    # ─────────────────────────────────────────────────────────────────────────

    def _deserialize(self, raw_items: list) -> list[ConversationTurn]:
        """Converte bytes/strings Redis em ConversationTurn."""
        turns = []
        for item in raw_items:
            try:
                if isinstance(item, bytes):
                    item = item.decode("utf-8")
                d = json.loads(item)
                turns.append(ConversationTurn(
                    role=d["role"],
                    content=d.get("content", ""),
                    timestamp=d.get("ts", 0),
                ))
            except Exception:
                continue
        return turns

    def _compact(self, turns: list[ConversationTurn]) -> list[ConversationTurn]:
        """
        Aplica sliding window + token budget.
        Método puro: sem IO, testável com dados sintéticos.

        ALGORITMO:
          1. Trunca cada mensagem individual para _MAX_CHARS_PER_MSG
          2. Agrupa em pares (user, assistant)
          3. Remove pares do início até caber no budget
          4. Garante que começa com turno "user"
        """
        if not turns:
            return []

        # Passo 1: trunca individuais
        turns = [t.truncated(_MAX_CHARS_PER_MSG) for t in turns]

        # Passo 2: aplica sliding window (máx _MAX_TURNS turnos)
        if len(turns) > _MAX_TURNS * 2:
            turns = turns[-(_MAX_TURNS * 2):]

        # Passo 3: remove pares do início até caber no budget
        total = sum(len(t.content) for t in turns)
        i = 0
        while total > _MAX_CHARS and i + 1 < len(turns):
            removed = turns[i]
            total -= len(removed.content)
            if i + 1 < len(turns):
                total -= len(turns[i + 1].content)
                i += 2  # remove par completo
            else:
                i += 1

        turns = turns[i:]

        # Passo 4: garante início com "user"
        while turns and turns[0].role != "user":
            turns = turns[1:]

        return turns

    def _format_as_text(self, turns: list[ConversationTurn]) -> str:
        """Formata histórico como string para injeção no prompt."""
        linhas = []
        for t in turns:
            prefixo = "Aluno" if t.role == "user" else "Assistente"
            linhas.append(f"{prefixo}: {t.content}")
        return "\n".join(linhas)