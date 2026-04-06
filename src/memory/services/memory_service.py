"""
src/memory/services/memory_service.py
---------------------------------------
Serviço de memória: orquestra Working + Long-Term + Extração.

ESTE É O ÚNICO PONTO DE ENTRADA para a memória no Graph.
Os nodes do LangGraph recebem MemoryService por injeção — nunca
acessam Redis diretamente.

PATTERN: Service Layer (não é um God Object — delega para os adapters)
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from ..ports.long_term_port import Fato, ILongTermMemory, fatos_para_string
from ..ports.menu_state_port import IMenuStateRepository
from ..ports.working_memory_port import HistoricoCompactado, IWorkingMemory

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """
    Contexto de memória para um usuário — injetado no state do LangGraph.
    Imutável: criado uma vez por mensagem recebida.
    """
    session_id: str
    user_id: str
    historico: HistoricoCompactado
    fatos: list[Fato]
    sinais: dict[str, str]
    menu_state: str

    @property
    def fatos_str(self) -> str:
        return fatos_para_string(self.fatos)

    @property
    def tem_contexto(self) -> bool:
        return self.historico.tem_historico or bool(self.fatos)


class MemoryService:
    """
    Serviço de memória do Oráculo.

    RESPONSABILIDADES:
      1. Carregar contexto completo de memória antes do pipeline RAG
      2. Persistir turno após resposta gerada
      3. Disparar extração de fatos em background (sem bloquear resposta)
      4. Limpar memória quando usuário reinicia conversa
    """

    def __init__(
        self,
        working: IWorkingMemory,
        long_term: ILongTermMemory,
        menu_state: IMenuStateRepository,
        fact_extractor,          # IFactExtractor (tipagem evita import circular)
    ):
        self._working = working
        self._long_term = long_term
        self._menu = menu_state
        self._extractor = fact_extractor

    # ─────────────────────────────────────────────────────────────────────────
    # API principal
    # ─────────────────────────────────────────────────────────────────────────

    def carregar_contexto(
        self,
        user_id: str,
        session_id: str,
        query: str = "",
    ) -> MemoryContext:
        """
        Carrega contexto completo antes do pipeline RAG.
        Chamado no node de entrada do LangGraph.

        PARALELO (quando possível): historico + fatos em threads separadas
        para reduzir latência total.
        """
        historico_result: HistoricoCompactado = HistoricoCompactado.vazio()
        fatos_result: list[Fato] = []
        sinais_result: dict[str, str] = {}
        menu_result: str = "MAIN"

        def load_working():
            nonlocal historico_result, sinais_result
            historico_result = self._working.get_historico(session_id)
            sinais_result = self._working.get_signals(session_id)

        def load_long_term():
            nonlocal fatos_result
            if query:
                fatos_result = self._long_term.search_hybrid(user_id, query, limit=5)
            else:
                fatos_result = self._long_term.search_recent(user_id, limit=5)

        def load_menu():
            nonlocal menu_result
            menu_result = self._menu.get(user_id)

        threads = [
            threading.Thread(target=load_working),
            threading.Thread(target=load_long_term),
            threading.Thread(target=load_menu),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)  # timeout de 2s por operação Redis

        return MemoryContext(
            session_id=session_id,
            user_id=user_id,
            historico=historico_result,
            fatos=fatos_result,
            sinais=sinais_result,
            menu_state=menu_result,
        )

    def persistir_turno(
        self,
        session_id: str,
        user_id: str,
        pergunta: str,
        resposta: str,
        rota: str = "GERAL",
    ) -> None:
        """
        Persiste o par pergunta/resposta e atualiza sinais.
        Chamado no node final do LangGraph.
        """
        self._working.add_turn(session_id, "user", pergunta)
        self._working.add_turn(session_id, "assistant", resposta)
        self._working.set_signal(session_id, "ultima_rota", rota)
        self._working.set_signal(session_id, "ultimo_topico", pergunta[:80])

    def extrair_fatos_background(self, user_id: str, session_id: str) -> None:
        """
        Dispara extração de fatos em thread daemon.
        NÃO bloqueia a resposta ao usuário.
        """
        def _run():
            try:
                turns = self._working.get_recent_turns(session_id, n=6)
                if not turns:
                    return
                novos_fatos = self._extractor.extract(user_id, turns)
                if novos_fatos:
                    salvos = self._long_term.save_batch(user_id, novos_fatos)
                    if salvos:
                        logger.info("🧠 Fatos extraídos [%s]: %d novos", user_id, salvos)
            except Exception as e:
                logger.debug("ℹ️  Extração background [%s]: %s", user_id, e)

        t = threading.Thread(target=_run, daemon=True, name=f"extractor-{user_id[:8]}")
        t.start()

    def limpar_tudo(self, user_id: str, session_id: str) -> None:
        """Limpa working memory, menu state (mantém long-term)."""
        self._working.clear(session_id)
        self._menu.clear(user_id)

    def limpar_tudo_inclusive_fatos(self, user_id: str, session_id: str) -> None:
        """Limpa absolutamente tudo (comando admin)."""
        self.limpar_tudo(user_id, session_id)
        self._long_term.delete_all(user_id)

    def atualizar_menu(self, user_id: str, state: str) -> None:
        self._menu.set(user_id, state)

    def listar_fatos(self, user_id: str) -> list[Fato]:
        return self._long_term.list_all(user_id)