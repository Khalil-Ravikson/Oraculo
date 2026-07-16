"""
src/application/use_cases/sigaa_use_cases.py
=============================================
Caso de uso do SIGAA. Processa solicitações de usuários em linguagem natural,
detecta a intenção (Biblioteca, Extensão ou Processos Seletivos) e extrai os parâmetros.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

class SIGAAUseCase:
    """
    Controlador para interpretar comandos em linguagem natural direcionados ao SIGAA
    e preparar a estrutura de dados a ser despachada para os workers correspondentes.
    """

    def detectar_fluxo(self, mensagem: str) -> dict | None:
        """
        Determina qual fluxo do SIGAA o usuário deseja acionar e extrai os parâmetros de consulta.
        Retorna um dicionário com o 'worker' e os 'args' caso seja detectada a intenção, senão None.
        """
        m = mensagem.lower()

        # Novo Fluxo: Notas
        if any(t in m for t in ["nota", "média", "media", "boletim", "desempenho"]):
            logger.info("🎯 Fluxo de Notas detectado.")
            return {
                "worker": "sigaa_notas",
                "args": {}
            }

        # Novo Fluxo: Índice Acadêmico
        if any(t in m for t in ["cr", "ira", "rendimento", "coeficiente"]):
            logger.info("🎯 Fluxo de Índice Acadêmico detectado.")
            return {
                "worker": "sigaa_indice",
                "args": {}
            }

        # Novo Fluxo: Histórico / Integralização / Horas Complementares
        if any(t in m for t in ["histórico", "historico", "formar", "concluir", "complementar", "ch ", "integraliza"]):
            logger.info("🎯 Fluxo de Histórico detectado.")
            return {
                "worker": "sigaa_historico",
                "args": {}
            }

        # Novo Fluxo: Estrutura Curricular
        if any(t in m for t in ["estrutura curricular", "matriz curricular", "fluxograma", "matriz", "currículo", "curriculo"]):
            logger.info("🎯 Fluxo de Estrutura Curricular detectado.")
            return {
                "worker": "sigaa_estrutura",
                "args": {}
            }

        # Novo Fluxo: Turmas / Horários / Aulas
        if any(t in m for t in ["turma", "horário", "horario", "sala", "professor", "aula", "cursar", "próximo semestre", "proximo semestre"]):
            logger.info("🎯 Fluxo de Turmas detectado.")
            return {
                "worker": "sigaa_turmas",
                "args": {}
            }

        # Novo Fluxo: Calendário Acadêmico
        if any(t in m for t in ["calendário", "calendario"]):
            logger.info("🎯 Fluxo de Calendário detectado.")
            return {
                "worker": "sigaa_calendario",
                "args": {}
            }

        # Fluxo A: Biblioteca Pública e Exportação
        if any(t in m for t in ["biblioteca", "livro", "obra", "acervo", "busca livro", "pesquisa livro", "marc"]):
            logger.info("🎯 Fluxo de Biblioteca detectado.")
            return {
                "worker": "sigaa_biblioteca",
                "args": self._extrair_busca_biblioteca(mensagem)
            }

        # Fluxo B: Cadastro em Evento de Extensão
        if any(t in m for t in ["inscrever", "inscrição", "extensão", "projeto de extensão", "evento"]):
            logger.info("🎯 Fluxo de Extensão detectado.")
            return {
                "worker": "sigaa_extensao",
                "args": {"nome_evento": self._extrair_nome_evento(mensagem)}
            }

        # Fluxo C: Monitoramento de Processos Seletivos
        if any(t in m for t in ["processo seletivo", "edital", "seleção", "concurso", "vagas"]):
            logger.info("🎯 Fluxo de Processos Seletivos detectado.")
            # Define nível: 'L' para Lato Sensu (Especializações, etc.) ou 'G' para Graduação (Padrão)
            nivel = "L" if any(x in m for x in ["especialização", "lato", "pos", "pós", "mestrado"]) else "G"
            return {
                "worker": "sigaa_processos",
                "args": {
                    "nivel": nivel,
                    "filtro_titulo": self._extrair_filtro_processo(mensagem)
                }
            }

        return None

    def _extrair_busca_biblioteca(self, msg: str) -> dict:
        """Extrai parâmetros específicos de busca da biblioteca (Autor, Título, Assunto)."""
        args = {}
        # Captura padrões como: "autor: Paulo Freire" ou "autor Paulo Freire"
        if m := re.search(r"autor(?:es)?[:\s]+([A-Za-zÀ-ú\s]+)(?:,|$)", msg, re.I):
            args["autor"] = m.group(1).strip()
        if m := re.search(r"t[íi]tulo[:\s]+([A-Za-zÀ-ú\s\d]+)(?:,|$)", msg, re.I):
            args["titulo"] = m.group(1).strip()
        if m := re.search(r"assunto[:\s]+([A-Za-zÀ-ú\s]+)(?:,|$)", msg, re.I):
            args["assunto"] = m.group(1).strip()

        # Fallback simples caso não encontre chaves explícitas: usa o texto inteiro como título
        if not args:
            # Remove palavras de comando comuns
            limpa = re.sub(r"\b(buscar|pesquisar|livro|na biblioteca|no acervo|sobre)\b", "", msg, flags=re.I).strip()
            if limpa:
                args["titulo"] = limpa
        
        return args

    def _extrair_nome_evento(self, msg: str) -> str:
        """Extrai o nome do evento de extensão para inscrição."""
        # Se estiver entre aspas, captura
        if m := re.search(r'"([^"]+)"', msg):
            return m.group(1).strip()
        if m := re.search(r"'([^']+)'", msg):
            return m.group(1).strip()
            
        # Senão, tenta após termos como "evento", "projeto" ou "inscrição em"
        for termo in ["evento ", "atividade ", "projeto ", "inscrição em "]:
            if termo in msg.lower():
                idx = msg.lower().index(termo) + len(termo)
                return msg[idx:].strip()
                
        # Fallback padrão caso nenhum termo seja achado
        return "CTIC Insight Conference 2026"

    def _extrair_filtro_processo(self, msg: str) -> str:
        """Extrai termos de filtro para processos seletivos e editais."""
        for termo in ["processo ", "edital ", "seleção ", "concurso "]:
            if termo in msg.lower():
                idx = msg.lower().index(termo) + len(termo)
                return msg[idx:].strip()
        return ""
