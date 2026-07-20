"""
src/agents/sigaa/service.py
==============================
Ex `application/workers/worker_sigaa.py` (Fase 5 do
PLANO_REFATORACAO_SUPERVISOR.md, seção 2.3) — a parte de DECISÃO: qual
scraping rodar, cache, formatação de mensagem, e a elegibilidade de
matrícula (via `agents/sigaa/eligibility.py`).

`SigaaService` concentra a lógica; `SigaaAgent` é o `BaseAgent` fino que a
embrulha e é registrado no Agent Registry (mesmo padrão de
`agents/academic_knowledge/service.py` na Fase 4: ainda NÃO é o caminho
quente de produção — os workers Celery em
`application/workers/worker_sigaa.py` continuam chamando `SigaaService`
diretamente, não `agent.execute(context)`, porque recebem um `event: dict`
do Celery, não um `AgentContext`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.agents.base import AgentEnabledMixin
from src.agents.sigaa import eligibility
from src.capabilities.sigaa.browser import SIGAAAgent, SIGAASeleniumFallback

logger = logging.getLogger(__name__)

CACHE_TTL = 1800  # 30 min


@dataclass
class SigaaFlowResult:
    status: str  # "ok" | "error"
    answer: str
    error: str = ""
    data: Any = None


class SigaaService:
    """Orquestra scraping (via `capabilities/sigaa/browser.py`) + cache + formatação."""

    def _cache_get(self, tipo: str, chave: str) -> dict | None:
        import json
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        cached = r.get(f"sigaa:cache:{tipo}:{chave}")
        if not cached:
            return None
        logger.info("⚡ [%s] Cache hit para %s", tipo.capitalize(), chave)
        return json.loads(cached if isinstance(cached, str) else cached.decode())

    def _cache_set(self, tipo: str, chave: str, data: dict) -> None:
        import json
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.setex(f"sigaa:cache:{tipo}:{chave}", CACHE_TTL, json.dumps(data, ensure_ascii=False))

    # ── Portal do Discente ───────────────────────────────────────────────────

    async def consultar_notas(self, login: str, senha: str, session_id: str) -> SigaaFlowResult:
        chave = login or session_id
        data = self._cache_get("notas", chave)
        if data is None:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res = await agent.fluxo_consultar_notas()
            if not res.ok:
                return SigaaFlowResult(status="error", answer=f"❌ Erro ao consultar notas: {res.error}", error=res.error)
            data = res.data
            self._cache_set("notas", chave, data)

        msg = "📝 *Suas Notas no SIGAA neste Semestre:*\n\n"
        notas = data.get("notas", [])
        if not notas:
            msg += "Nenhuma nota cadastrada até o momento."
        else:
            for n in notas:
                msg += f"🔹 **{n['disciplina']}**\n   Nota: {n['nota']} | Média: {n['media']} ({n['situacao']})\n\n"

        return SigaaFlowResult(status="ok", answer=msg, data=data)

    async def consultar_indice(self, login: str, senha: str, session_id: str) -> SigaaFlowResult:
        chave = login or session_id
        data = self._cache_get("indice", chave)
        if data is None:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res = await agent.fluxo_consultar_indice()
            if not res.ok:
                return SigaaFlowResult(status="error", answer=f"❌ Erro ao obter índice: {res.error}", error=res.error)
            data = res.data
            self._cache_set("indice", chave, data)

        msg = "📊 *Seus Índices Acadêmicos no SIGAA:*\n\n"
        msg += f"📈 **CR (Coeficiente de Rendimento):** {data.get('cr', 'N/A')}\n"
        msg += f"🎓 **IRA (Índice de Rendimento Acadêmico):** {data.get('ira', 'N/A')}\n"

        return SigaaFlowResult(status="ok", answer=msg, data=data)

    async def emitir_historico(self, login: str, senha: str, session_id: str, query: str = "") -> SigaaFlowResult:
        query = (query or "").lower()
        chave = login or session_id
        data = self._cache_get("historico", chave)
        if data is None:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res = await agent.fluxo_emitir_historico()
            if not res.ok:
                return SigaaFlowResult(status="error", answer=f"❌ Erro ao obter histórico: {res.error}", error=res.error)
            data = res.data
            self._cache_set("historico", chave, data)

        if "complementar" in query or "atividade" in query:
            ch_comp_concl = data.get("horas_complementares_concluidas", 90)
            ch_comp_exig = data.get("horas_complementares_exigidas", 150)
            ch_comp_rest = max(0, ch_comp_exig - ch_comp_concl)

            msg = "🎗️ *Horas Complementares (Atividades Autônomas):*\n\n"
            msg += f"✅ **Concluído:** {ch_comp_concl} horas\n"
            msg += f"🎯 **Exigido:** {ch_comp_exig} horas\n"
            msg += f"⏳ **Faltam:** {ch_comp_rest} horas para atingir o mínimo curricular."

        elif "falta" in query or "formar" in query or "concluir" in query:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res_est = await agent.fluxo_consultar_estrutura()
            obrigatorias = res_est.data.get("obrigatorias", [])
            disciplinas = data.get("disciplinas", [])

            faltam = eligibility.calcular_faltantes(disciplinas, obrigatorias)

            ch_total = data.get("ch_exigida", 3915)
            ch_concl = data.get("ch_concluida", 3135)
            percentual = eligibility.calcular_percentual_integralizacao(ch_concl, ch_total)

            msg = "🎓 *Seu Progresso de Integralização:*\n\n"
            msg += f"✅ **Carga Horária Concluída:** {ch_concl} horas / {ch_total} horas\n"
            msg += f"📈 **Progresso:** {percentual}% integralizado\n\n"
            msg += f"📚 *Disciplinas Obrigatórias Restantes ({len(faltam)}):*\n"
            for d in faltam:
                prereq_str = f" (Pré-req: {', '.join(d['prerequisitos'])})" if d["prerequisitos"] else ""
                msg += f"- ❌ {d['nome']} ({d['ch']}h){prereq_str}\n"
        else:
            msg = "📄 *Resumo do Histórico Escolar (SIGAA):*\n\n"
            disciplinas = data.get("disciplinas", [])
            for d in disciplinas[:8]:
                msg += f"- {d['semestre']} | {d['disciplina']} | CH: {d['ch']}h | Nota: {d['nota']} ({d['situacao']})\n"
            if len(disciplinas) > 8:
                msg += f"\n_...e outras {len(disciplinas) - 8} disciplinas concluídas._"

        return SigaaFlowResult(status="ok", answer=msg, data=data)

    async def consultar_estrutura(self, login: str, senha: str, session_id: str) -> SigaaFlowResult:
        chave = login or session_id
        data = self._cache_get("estrutura", chave)
        if data is None:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res = await agent.fluxo_consultar_estrutura()
            if not res.ok:
                return SigaaFlowResult(status="error", answer=f"❌ Erro ao obter estrutura curricular: {res.error}", error=res.error)
            data = res.data
            self._cache_set("estrutura", chave, data)

        msg = "🕸️ *Estrutura Curricular (Engenharia de Computação):*\n\n"
        msg += f"📖 **Disciplinas Obrigatórias ({len(data.get('obrigatorias', []))}):**\n"
        for d in data.get("obrigatorias", [])[:8]:
            prereq = f" [Pré-req: {', '.join(d['prerequisitos'])}]" if d["prerequisitos"] else ""
            msg += f"- {d['nome']} ({d['ch']}h){prereq}\n"
        msg += "\n_...e outras disciplinas obrigatórias listadas no currículo._"

        return SigaaFlowResult(status="ok", answer=msg, data=data)

    async def consultar_turmas(self, login: str, senha: str, session_id: str, query: str = "") -> SigaaFlowResult:
        query = (query or "").lower()
        chave = login or session_id
        data = self._cache_get("turmas", chave)
        if data is None:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res = await agent.fluxo_consultar_turmas()
            if not res.ok:
                return SigaaFlowResult(status="error", answer=f"❌ Erro ao obter turmas: {res.error}", error=res.error)
            data = res.data
            self._cache_set("turmas", chave, data)

        if "próximo" in query or "proximo" in query or "posso cursar" in query or "matérias posso" in query:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res_hist = await agent.fluxo_emitir_historico()
            res_est = await agent.fluxo_consultar_estrutura()

            elegiveis = eligibility.calcular_elegiveis_proximo_semestre(
                res_hist.data.get("disciplinas", []),
                res_est.data.get("obrigatorias", []),
            )

            msg = "🗓️ *Matérias elegíveis para matrícula no próximo semestre:*\n\n"
            if not elegiveis:
                msg += "Nenhuma disciplina pendente com pré-requisitos cumpridos."
            else:
                msg += "Você já possui os pré-requisitos necessários para cursar:\n"
                for d in elegiveis:
                    msg += f"- ➡️ **{d['nome']}** ({d['ch']}h)\n"
        else:
            msg = "🏫 *Suas Turmas e Horários neste Semestre (SIGAA):*\n\n"
            turmas = data.get("turmas", [])
            if not turmas:
                msg += "Nenhuma turma ativa localizada para este semestre."
            else:
                for t in turmas:
                    msg += f"🔹 **{t['nome']}**\n   📍 {t['local']}\n   ⏰ Horário: {t['horario']}\n\n"

        return SigaaFlowResult(status="ok", answer=msg, data=data)

    async def consultar_calendario(self, login: str, senha: str, session_id: str) -> SigaaFlowResult:
        chave = login or session_id
        data = self._cache_get("calendario", chave)
        if data is None:
            agent = SIGAAAgent(login=login, senha=senha, session_id=session_id)
            res = await agent.fluxo_calendario_academico()
            if not res.ok:
                return SigaaFlowResult(status="error", answer=f"❌ Erro ao obter calendário acadêmico: {res.error}", error=res.error)
            data = res.data
            self._cache_set("calendario", chave, data)

        msg = "📅 *Datas Importantes do Calendário Acadêmico (SIGAA):*\n\n"
        msg += f"🚀 **Início do Semestre:** {data.get('inicio_semestre')}\n"
        msg += f"🏁 **Fim do Semestre:** {data.get('fim_semestre')}\n"
        msg += f"📝 **Período de Matrícula:** {data.get('periodo_matricula')}\n"
        msg += f"⏳ **Limite de Trancamento:** {data.get('prazo_trancamento')}\n"
        msg += f"🌴 **Férias Acadêmicas:** {data.get('ferias')}\n"

        return SigaaFlowResult(status="ok", answer=msg, data=data)

    # ── Fluxos Públicos (A/B/C) ──────────────────────────────────────────────

    async def buscar_biblioteca(self, autor: str = "", titulo: str = "", assunto: str = "") -> SigaaFlowResult:
        agent = SIGAAAgent()
        try:
            res = await agent.fluxo_a_biblioteca(autor=autor, titulo=titulo, assunto=assunto)
            if res.ok:
                return SigaaFlowResult(status="ok", answer=self._formatar_biblioteca(res.data), data=res.data)
            raise Exception(res.error)
        except Exception as e:
            logger.warning("⚠️ Playwright falhou na biblioteca. Tentando fallback para Selenium: %s", e)
            fallback = SIGAASeleniumFallback()
            try:
                res_fb = fallback.biblioteca_buscar(autor=autor, titulo=titulo)
                if res_fb.get("ok"):
                    obras = res_fb.get("obras", [])
                    if not obras:
                        msg = "🔍 Nenhuma obra encontrada (via Fallback)."
                    else:
                        linhas = [f"📚 *{o['titulo']}* — {o['autor']} ({o['tipo']})" for o in obras[:5]]
                        msg = "📖 *Resultados da Biblioteca (Fallback Selenium):*\n\n" + "\n".join(linhas)
                    return SigaaFlowResult(status="ok", answer=msg, data=res_fb)
                return SigaaFlowResult(
                    status="error",
                    answer=f"❌ Erro na consulta da biblioteca: {res_fb.get('error')}",
                    error=str(res_fb.get("error")),
                )
            except Exception as fb_err:
                logger.error("❌ Fallback para Selenium também falhou: %s", fb_err)
                return SigaaFlowResult(
                    status="error",
                    answer=f"❌ Falha crítica ao acessar a biblioteca: {e}",
                    error=str(fb_err),
                )

    @staticmethod
    def _formatar_biblioteca(data: dict) -> str:
        obras = data.get("obras", [])
        arquivo = data.get("arquivo", "")
        if not obras:
            return "🔍 Nenhuma obra foi encontrada na biblioteca pública com os filtros informados."
        linhas = [f"📚 *{o['titulo']}* — {o['autor']} ({o['tipo']})" for o in obras[:5]]
        msg = "📖 *Resultados da Biblioteca Pública SIGAA:*\n\n" + "\n".join(linhas)
        if len(obras) > 5:
            msg += f"\n\n_...e outras {len(obras) - 5} obras listadas._"
        if arquivo:
            msg += f"\n\n📎 *Metadados exportados (MARC/PDF):* `{arquivo}`"
        return msg

    async def inscrever_extensao(self, nome_evento: str) -> SigaaFlowResult:
        agent = SIGAAAgent()
        try:
            res = await agent.fluxo_b_extensao(nome_evento=nome_evento)
            if res.ok:
                status = res.data.get("status")
                if status == "inscrito":
                    msg = f"✅ Inscrição no evento de extensão *\"{nome_evento}\"* realizada com sucesso no SIGAA!"
                else:
                    msg = f"⚠️ A solicitação de inscrição em *\"{nome_evento}\"* foi submetida, porém aguarda confirmação final."
                return SigaaFlowResult(status="ok", answer=msg, data=res.data)
            return SigaaFlowResult(
                status="error",
                answer=f"❌ Não foi possível realizar a inscrição no evento de extensão:\n{res.error}",
                error=res.error,
            )
        except Exception as e:
            logger.exception("Falha na inscrição de extensão: %s", e)
            return SigaaFlowResult(status="error", answer=f"❌ Erro interno ao tentar se inscrever no evento de extensão: {e}", error=str(e))

    async def processos_seletivos(self, nivel: str = "L", filtro_titulo: str = "") -> SigaaFlowResult:
        agent = SIGAAAgent()
        try:
            res = await agent.fluxo_c_processos_seletivos(nivel=nivel, filtro_titulo=filtro_titulo)
            if res.ok:
                processos = res.data.get("processos", [])
                arquivos = res.data.get("arquivos_editais", [])
                if not processos:
                    msg = "🔍 Nenhum edital ou processo seletivo ativo foi localizado no momento."
                else:
                    linhas = [f"🎓 *{p['titulo']}* — Prazo: {p['periodo']}" for p in processos[:5]]
                    msg = "📣 *Processos Seletivos Ativos no SIGAA:*\n\n" + "\n".join(linhas)
                    if len(processos) > 5:
                        msg += f"\n\n_...e outros {len(processos) - 5} processos ativos._"
                    if arquivos:
                        msg += f"\n\n📥 *Editais baixados com sucesso:* {len(arquivos)} arquivos PDF salvos temporariamente."
                return SigaaFlowResult(status="ok", answer=msg, data=res.data)
            return SigaaFlowResult(status="error", answer=f"❌ Falha ao listar os processos seletivos:\n{res.error}", error=res.error)
        except Exception as e:
            logger.exception("Falha nos processos seletivos: %s", e)
            return SigaaFlowResult(status="error", answer=f"❌ Erro ao monitorar processos seletivos: {e}", error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SigaaAgent — BaseAgent (ver agents/base.py e agents/registry.py, Fase 2)
# ─────────────────────────────────────────────────────────────────────────────

class SigaaAgent(AgentEnabledMixin):
    name = "sigaa"
    description = "Consulta dados acadêmicos do discente no SIGAA (notas, histórico, turmas, CR/IRA, estrutura curricular)."
    permissions: list[str] = []

    def __init__(self) -> None:
        self._service = SigaaService()

    async def execute(self, context):
        from src.agents.base import AgentResponse

        identity = context.identity or {}
        conversation = context.conversation or {}
        flow = conversation.get("sigaa_flow", "indice")
        login = identity.get("login", "")
        senha = identity.get("senha", "")
        query = conversation.get("query", "")

        metodo = {
            "notas": lambda: self._service.consultar_notas(login, senha, context.session_id),
            "indice": lambda: self._service.consultar_indice(login, senha, context.session_id),
            "historico": lambda: self._service.emitir_historico(login, senha, context.session_id, query),
            "estrutura": lambda: self._service.consultar_estrutura(login, senha, context.session_id),
            "turmas": lambda: self._service.consultar_turmas(login, senha, context.session_id, query),
            "calendario": lambda: self._service.consultar_calendario(login, senha, context.session_id),
        }.get(flow)

        if metodo is None:
            return AgentResponse(answer=f"Fluxo SIGAA desconhecido: '{flow}'.", status="error")

        resultado = await metodo()
        return AgentResponse(answer=resultado.answer, status=resultado.status)
