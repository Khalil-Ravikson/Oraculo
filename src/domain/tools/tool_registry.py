"""
src/domain/tools/tool_registry.py
-----------------------------------
Registry de tools para o LangGraph.

DESIGN:
  Tools são criadas a partir das interfaces de domínio (não de mocks hardcoded).
  O LangGraph recebe uma lista de BaseTool instanciadas pelo registry.
  Adicionar tool = criar classe + registrar. Zero mudança no graph/nodes.

  Não usamos @langchain_core.tool diretamente nas implementações —
  usamos StructuredTool.from_function para manter o código testável
  (funções normais são mais fáceis de testar que decorators).
"""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Registry central de tools do agente.
    Injeta os serviços nas tools e retorna lista para o LangGraph.
    """

    def __init__(
        self,
        calendario_svc: Any,
        edital_svc: Any,
        contatos_svc: Any,
        wiki_svc: Any,
        glpi_svc: Any,
        email_svc: Any,
        scraping_svc: Any,
    ):
        self._calendario = calendario_svc
        self._edital = edital_svc
        self._contatos = contatos_svc
        self._wiki = wiki_svc
        self._glpi = glpi_svc
        self._email = email_svc
        self._scraping = scraping_svc
        self._tools: dict[str, StructuredTool] = {}
        self._build_all()

    def _build_all(self) -> None:
        self._tools = {
            "consultar_calendario": self._build_calendario(),
            "consultar_edital": self._build_edital(),
            "consultar_contatos": self._build_contatos(),
            "consultar_wiki_ctic": self._build_wiki(),
            "abrir_chamado_glpi": self._build_glpi(),
            "consultar_fila_chamados": self._build_fila(),
            "enviar_email": self._build_email(),
            "scraping_web": self._build_scraping(),
        }

    def get_all(self) -> list[StructuredTool]:
        return list(self._tools.values())

    def get(self, name: str) -> StructuredTool | None:
        return self._tools.get(name)

    def get_for_role(self, role: str) -> list[StructuredTool]:
        """Filtra tools por nível de acesso (GUEST/STUDENT/ADMIN)."""
        _ROLE_TOOLS = {
            "GUEST":   ["consultar_calendario", "consultar_edital", "consultar_contatos", "consultar_wiki_ctic"],
            "STUDENT": ["consultar_calendario", "consultar_edital", "consultar_contatos", "consultar_wiki_ctic",
                        "abrir_chamado_glpi", "consultar_fila_chamados", "enviar_email"],
            "ADMIN":   list(self._tools.keys()),
        }
        allowed = _ROLE_TOOLS.get(role.upper(), _ROLE_TOOLS["GUEST"])
        return [self._tools[k] for k in allowed if k in self._tools]

    # ─────────────────────────────────────────────────────────────────────────
    # Builders individuais
    # ─────────────────────────────────────────────────────────────────────────

    def _build_calendario(self) -> StructuredTool:
        from pydantic import BaseModel, Field

        class CalendarioInput(BaseModel):
            query: str = Field(..., description="Palavras-chave sobre o evento acadêmico. Ex: 'matrícula veteranos 2026.1'")

        svc = self._calendario

        async def _run(query: str) -> str:
            result = await svc.consultar(query)
            return result.to_agent_str()

        return StructuredTool(
            name="consultar_calendario",
            description=(
                "Consulta o Calendário Acadêmico da UEMA 2026. "
                "Use para: datas de matrícula, rematrícula, início das aulas, feriados, trancamento, "
                "prazo de avaliações, banca e defesas. "
                "Exemplos: 'matrícula veteranos 2026.1', 'início aulas fevereiro', 'feriados junho'."
            ),
            args_schema=CalendarioInput,
            coroutine=_run,
        )

    def _build_edital(self) -> StructuredTool:
        from pydantic import BaseModel, Field

        class EditalInput(BaseModel):
            query: str = Field(..., description="Palavras-chave sobre vagas, cotas ou procedimentos do PAES.")

        svc = self._edital

        async def _run(query: str) -> str:
            result = await svc.consultar(query)
            return result.to_agent_str()

        return StructuredTool(
            name="consultar_edital",
            description=(
                "Consulta o Edital PAES 2026 da UEMA. "
                "Use para: vagas por curso, categorias de cotas (AC, PcD, BR-PPI, BR-Q), "
                "documentos para inscrição, cronograma do processo seletivo, "
                "regras de heteroidentificação. "
                "Exemplos: 'vagas engenharia civil AC', 'documentos inscrição', 'o que é BR-PPI'."
            ),
            args_schema=EditalInput,
            coroutine=_run,
        )

    def _build_contatos(self) -> StructuredTool:
        from pydantic import BaseModel, Field

        class ContatosInput(BaseModel):
            query: str = Field(..., description="Nome do setor, cargo ou sigla para buscar contato.")

        svc = self._contatos

        async def _run(query: str) -> str:
            result = await svc.consultar(query)
            return result.to_agent_str()

        return StructuredTool(
            name="consultar_contatos",
            description=(
                "Consulta e-mails, telefones e responsáveis de setores da UEMA. "
                "Use para: PROG, PROEXAE, PRPPG, PRAD, CTIC, CECEN, CESB, reitoria, "
                "coordenadores de curso, secretarias. "
                "Exemplos: 'email PROG graduação', 'telefone CTIC TI', 'coordenador engenharia civil'."
            ),
            args_schema=ContatosInput,
            coroutine=_run,
        )

    def _build_wiki(self) -> StructuredTool:
        from pydantic import BaseModel, Field

        class WikiInput(BaseModel):
            query: str = Field(..., description="Palavras-chave sobre sistemas de TI, suporte ou serviços do CTIC.")

        svc = self._wiki

        async def _run(query: str) -> str:
            result = await svc.consultar(query)
            return result.to_agent_str()

        return StructuredTool(
            name="consultar_wiki_ctic",
            description=(
                "Consulta a Wiki do CTIC (Centro de TI da UEMA). "
                "Use para: SIGAA, e-mail institucional, senha, Wi-Fi, VPN, "
                "sistemas acadêmicos, laboratórios, suporte técnico. "
                "Exemplos: 'como resetar senha SIGAA', 'configurar e-mail uema android', 'wifi campus'."
            ),
            args_schema=WikiInput,
            coroutine=_run,
        )

    def _build_glpi(self) -> StructuredTool:
        from pydantic import BaseModel, Field
        from typing import Literal

        class GLPIAbrirInput(BaseModel):
            titulo: str = Field(..., description="Resumo do problema em até 60 caracteres.")
            descricao: str = Field(..., description="Descrição completa do problema relatado.")
            local: str = Field(default="Não informado", description="Sala, bloco ou laboratório.")
            urgencia: Literal["baixa", "media", "alta"] = Field(default="media")

        svc = self._glpi

        async def _run(titulo: str, descricao: str, local: str = "Não informado", urgencia: str = "media") -> str:
            result = await svc.abrir_chamado(titulo=titulo, descricao=descricao, local=local, urgencia=urgencia)
            return result.to_agent_str()

        return StructuredTool(
            name="abrir_chamado_glpi",
            description=(
                "Abre chamado de suporte técnico no GLPI da UEMA. "
                "Use quando o aluno reportar: sem internet, computador com problema, "
                "impressora quebrada, erro no SIGAA, sistema fora do ar. "
                "SEMPRE use após coletar: local (sala/bloco) e descrição detalhada do problema."
            ),
            args_schema=GLPIAbrirInput,
            coroutine=_run,
        )

    def _build_fila(self) -> StructuredTool:
        from pydantic import BaseModel, Field

        class FilaInput(BaseModel):
            user_email: str = Field(default="", description="E-mail do usuário para filtrar seus chamados.")

        svc = self._glpi

        async def _run(user_email: str = "") -> str:
            result = await svc.consultar_fila(user_email=user_email)
            return result.to_agent_str()

        return StructuredTool(
            name="consultar_fila_chamados",
            description="Consulta os chamados de suporte técnico pendentes do usuário no GLPI.",
            args_schema=FilaInput,
            coroutine=_run,
        )

    def _build_email(self) -> StructuredTool:
        from pydantic import BaseModel, Field

        class EmailInput(BaseModel):
            destinatario: str = Field(..., description="E-mail do destinatário. Ex: aluno@aluno.uema.br")
            assunto: str = Field(..., description="Assunto do e-mail.")
            mensagem: str = Field(..., description="Corpo do e-mail em texto simples.")

        svc = self._email

        async def _run(destinatario: str, assunto: str, mensagem: str) -> str:
            result = await svc.enviar(
                destinatario=destinatario,
                assunto=assunto,
                corpo=mensagem,
            )
            return result.to_agent_str()

        return StructuredTool(
            name="enviar_email",
            description=(
                "Envia e-mail institucional para qualquer endereço @uema.br ou @aluno.uema.br. "
                "Use para: confirmações de chamado, notificações de prazo, respostas formais. "
                "SEMPRE confirme com o usuário antes de enviar."
            ),
            args_schema=EmailInput,
            coroutine=_run,
        )

    def _build_scraping(self) -> StructuredTool:
        from pydantic import BaseModel, Field

        class ScrapingInput(BaseModel):
            url: str = Field(..., description="URL completa da página a ser scrapeada.")
            doc_type: str = Field(default="web", description="Tipo do documento: 'web', 'wiki', 'wiki_ctic'.")
            force_refresh: bool = Field(default=False, description="Forçar novo scraping ignorando cache.")

        svc = self._scraping

        async def _run(url: str, doc_type: str = "web", force_refresh: bool = False) -> str:
            from src.infrastructure.scraping.base_scraper import ScrapeRequest
            request = ScrapeRequest(url=url, doc_type=doc_type, force_refresh=force_refresh)
            result = await svc.scrape(request)
            if result.ok and result.document:
                doc = result.document
                preview = doc.content[:500] + ("..." if len(doc.content) > 500 else "")
                return f'{{"ok": true, "title": "{doc.title}", "words": {doc.word_count}, "preview": "{preview}", "from_cache": {str(result.from_cache).lower()}}}'
            return f'{{"ok": false, "error": "{result.error}"}}'

        return StructuredTool(
            name="scraping_web",
            description=(
                "Faz scraping de uma URL e extrai o conteúdo textual. "
                "Use para: buscar informações em páginas da UEMA, Wiki do CTIC, "
                "Wikipedia ou qualquer página web. O conteúdo é automaticamente "
                "indexado no RAG para consultas futuras."
            ),
            args_schema=ScrapingInput,
            coroutine=_run,
        )