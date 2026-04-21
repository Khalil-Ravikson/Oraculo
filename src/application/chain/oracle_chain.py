"""
application/chain/oracle_chain.py — Pipeline RAG com LangChain Runnables
=========================================================================

MOTIVAÇÃO DA MIGRAÇÃO (LangGraph → Runnables):
  - LangGraph para um pipeline RAG simples é over-engineering.
    Ele brilha em grafos cíclicos complexos; para um pipeline linear, é ruído.
  - Runnables têm logging trivial: cada step é uma função Python normal.
  - Debug imediato: asyncio.Queue → SSE → chat.html mostra cada passo ao vivo.
  - Sem estado global opaco — tudo flui como um dict acumulado.

PIPELINE (sequência linear):
    input → load_memory → route_intent → transform_query
          → retrieve → grade_docs → generate → save_memory
          → ChainResult (com todos os dados de debug)

HITL (Human-in-the-Loop) sem LangGraph:
    - Intenção CRUD detectada → gera mensagem de confirmação
    - Armazena ação pendente em Redis (key: hitl:{session_id})
    - Na próxima mensagem, verifica Redis antes de qualquer LLM call
    - "sim" → executa a tool; "não" → cancela

COMO USAR:
    chain = OracleChain()
    result = await chain.invoke("quando é a matrícula?", "sess_123", user_ctx)
    print(result.answer)
    # Debug completo em result.steps
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de resultado
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Resultado de um passo do pipeline. Emitido via SSE para debug."""
    name: str
    status: str       # "running" | "ok" | "skip" | "error"
    detail: str = ""
    latency_ms: int = 0
    data: dict = field(default_factory=dict)


@dataclass
class ChainResult:
    """Resultado final do pipeline com todos os dados de debug."""
    answer: str
    route: str
    crag_score: float
    cache_hit: bool
    chunks_count: int
    tokens_used: int
    total_ms: int
    steps: list[StepResult] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def to_debug_dict(self) -> dict:
        return {
            "answer":       self.answer,
            "route":        self.route,
            "crag_score":   round(self.crag_score, 3),
            "cache_hit":    self.cache_hit,
            "chunks":       self.chunks_count,
            "tokens":       self.tokens_used,
            "total_ms":     self.total_ms,
            "steps": [
                {"name": s.name, "status": s.status,
                 "detail": s.detail, "ms": s.latency_ms}
                for s in self.steps
            ],
        }


# ─────────────────────────────────────────────────────────────────────────────
# OracleChain
# ─────────────────────────────────────────────────────────────────────────────

class OracleChain:
    """
    Pipeline RAG do Oráculo usando LangChain Runnables puros.

    Cada passo é uma co-rotina assíncrona que:
      1. Recebe o estado acumulado (dict)
      2. Faz sua operação
      3. Atualiza o estado
      4. Emite um StepResult para debug via _debug_queue

    Não há estado global. Tudo passa pelo dict `ctx`.
    """

    # Fila de debug compartilhada por processo. O endpoint SSE consome daqui.
    _debug_queues: dict[str, asyncio.Queue] = {}

    def __init__(self):
        self._llm = None          # lazy — evita import circular no boot
        self._embeddings = None
        self._retriever = None

    # ── API pública ────────────────────────────────────────────────────────────

    async def invoke(
        self,
        message: str,
        session_id: str,
        user_context: dict,
        debug_queue: asyncio.Queue | None = None,
    ) -> ChainResult:
        """
        Executa o pipeline completo.

        Args:
            message:      Texto da mensagem do usuário.
            session_id:   ID da sessão (telefone normalizado).
            user_context: Dict com {nome, curso, periodo, matricula, ...}
            debug_queue:  Queue para streaming SSE (opcional).

        Returns:
            ChainResult com resposta e dados de debug completos.
        """
        t_total = time.monotonic()
        steps: list[StepResult] = []

        async def emit(step: StepResult):
            steps.append(step)
            if debug_queue:
                await debug_queue.put(step)

        # Estado acumulado que flui por todo o pipeline
        ctx: dict = {
            "message":       message,
            "session_id":    session_id,
            "user_context":  user_context,
            "history":       "",
            "facts":         [],
            "route":         "GERAL",
            "route_confidence": 0.0,
            "query_final":   message,
            "chunks":        [],
            "crag_score":    0.0,
            "answer":        "",
            "cache_hit":     False,
            "tokens_used":   0,
            "hitl_pending":  False,
        }

        try:
            # ── Pipeline sequencial ────────────────────────────────────────────
            await self._step_load_memory(ctx, emit)
            await self._step_check_hitl(ctx, emit)

            if ctx.get("hitl_response"):
                # HITL processou — retorna direto sem RAG
                return self._make_result(ctx, steps, t_total)

            await self._step_route(ctx, emit)
            await self._step_transform_query(ctx, emit)
            await self._step_retrieve(ctx, emit)
            await self._step_grade_docs(ctx, emit)
            await self._step_generate(ctx, emit)
            await self._step_save_memory(ctx, emit)

        except Exception as exc:
            logger.exception("❌ [CHAIN] Pipeline falhou | session=%s | erro: %s",
                             session_id, exc)
            ctx["answer"] = (
                "Desculpe, tive um problema técnico. Por favor tente novamente. 🙏"
            )
            ctx["error"] = str(exc)
            await emit(StepResult("pipeline", "error", str(exc)[:120]))
        # 👇👇👇 ADICIONE ESTAS DUAS LINHAS AQUI 👇👇👇
        if debug_queue:
            await debug_queue.put(StepResult("DONE", "ok"))
        return self._make_result(ctx, steps, t_total)

    # ── Steps do pipeline ──────────────────────────────────────────────────────

    async def _step_load_memory(self, ctx: dict, emit) -> None:
        """Carrega histórico e fatos do Redis."""
        t0 = time.monotonic()
        await emit(StepResult("load_memory", "running"))
        session_id = ctx["session_id"]
        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()

            # Histórico de conversa
            raw = r.lrange(f"chat:{session_id}", -10, -1) or []
            turns = []
            import json
            for item in raw:
                try:
                    d = json.loads(item)
                    pref = "Aluno" if d.get("role") == "user" else "Bot"
                    turns.append(f"{pref}: {d.get('content','')[:200]}")
                except Exception:
                    pass
            ctx["history"] = "\n".join(turns)

            # Fatos de longo prazo
            fatos = r.lrange(f"mem:facts:list:{session_id}", 0, 4) or []
            ctx["facts"] = [f if isinstance(f, str) else f.decode() for f in fatos]

            ms = int((time.monotonic() - t0) * 1000)
            detail = f"{len(turns)} msgs histórico, {len(fatos)} fatos"
            await emit(StepResult("load_memory", "ok", detail, ms))

        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            logger.warning("⚠️  [CHAIN] load_memory falhou: %s", e)
            await emit(StepResult("load_memory", "error", str(e)[:80], ms))

    async def _step_check_hitl(self, ctx: dict, emit) -> None:
        """Verifica se há ação pendente de confirmação HITL."""
        t0 = time.monotonic()
        session_id = ctx["session_id"]
        message = ctx["message"].lower().strip()

        try:
            from src.infrastructure.redis_client import get_redis_text
            import json
            r = get_redis_text()
            key = f"hitl:{session_id}"
            raw = r.get(key)

            if not raw:
                return  # sem HITL pendente

            pending = json.loads(raw if isinstance(raw, str) else raw.decode())
            action = pending.get("action", "")
            args = pending.get("args", {})

            # Interpreta a resposta do usuário
            confirmed = message in ("sim", "s", "yes", "y", "confirmar", "ok", "pode")
            cancelled = message in ("não", "nao", "n", "no", "cancelar", "não quero")

            if confirmed:
                # Executa a tool pendente
                result = await self._executar_crud_tool(action, args, session_id)
                ctx["answer"] = result
                ctx["hitl_response"] = True
                r.delete(key)
                ms = int((time.monotonic() - t0) * 1000)
                await emit(StepResult("hitl", "ok", f"Executou: {action}", ms))

            elif cancelled:
                ctx["answer"] = "❌ Operação cancelada. Posso ajudar com outra coisa?"
                ctx["hitl_response"] = True
                r.delete(key)
                ms = int((time.monotonic() - t0) * 1000)
                await emit(StepResult("hitl", "ok", "Cancelado pelo usuário", ms))

            else:
                # Mensagem não é confirmação nem cancelamento — repete o pedido
                desc = pending.get("description", "executar a operação")
                ctx["answer"] = (
                    f"Confirma: *{desc}*?\n\n"
                    "Responda *SIM* para confirmar ou *NÃO* para cancelar."
                )
                ctx["hitl_response"] = True
                ms = int((time.monotonic() - t0) * 1000)
                await emit(StepResult("hitl", "ok", "Aguardando confirmação", ms))

        except Exception as e:
            logger.warning("⚠️  [CHAIN] check_hitl falhou: %s", e)

    async def _step_route(self, ctx: dict, emit) -> None:
        """Detecta intenção via regex rápido + KNN Redis (sem LLM)."""
        t0 = time.monotonic()
        await emit(StepResult("route", "running"))
        message = ctx["message"]

        # Regex de alta velocidade (0ms, 0 tokens)
        route, confidence = _route_regex(message)

        # KNN semântico se regex ficou com confiança baixa
        if confidence < 0.80:
            route_knn, conf_knn = await _route_knn(message)
            if conf_knn > confidence:
                route, confidence = route_knn, conf_knn

        ctx["route"] = route
        ctx["route_confidence"] = confidence

        ms = int((time.monotonic() - t0) * 1000)
        detail = f"{route} (conf={confidence:.2f})"
        await emit(StepResult("route", "ok", detail, ms,
                              {"route": route, "confidence": round(confidence, 3)}))

    async def _step_transform_query(self, ctx: dict, emit) -> None:
        """
        Transforma query com contexto de histórico e fatos.
        Usa heurística local (0 tokens) quando possível.
        """
        t0 = time.monotonic()
        message = ctx["message"]
        facts = ctx.get("facts", [])

        # Queries já técnicas não precisam de transformação
        if _e_query_tecnica(message):
            ctx["query_final"] = message
            ms = int((time.monotonic() - t0) * 1000)
            await emit(StepResult("transform_query", "skip", "query já técnica", ms))
            return

        await emit(StepResult("transform_query", "running"))

        # Enriquece com fatos do usuário (local, sem LLM)
        extra = " ".join(facts[:2]) if facts else ""
        ctx["query_final"] = f"{message} {extra}".strip()

        ms = int((time.monotonic() - t0) * 1000)
        await emit(StepResult("transform_query", "ok",
                              f"'{ctx['query_final'][:60]}'", ms))

    async def _step_retrieve(self, ctx: dict, emit) -> None:
        """Busca híbrida Redis (BM25 + Vector)."""
        t0 = time.monotonic()
        await emit(StepResult("retrieve", "running"))

        query = ctx["query_final"]
        route = ctx["route"]

        try:
            emb = self._get_embeddings()
            vetor = await asyncio.to_thread(emb.embed_query, _normalize(query))

            # Mapeamento rota → source_filter
            source_map = {
                "CALENDARIO": "calendario-academico-2026.pdf",
                "EDITAL":     "edital_paes_2026.pdf",
                "CONTATOS":   "guia_contatos_2025.pdf",
            }
            source_filter = source_map.get(route)

            from src.infrastructure.redis_client import busca_hibrida
            chunks = await asyncio.to_thread(
                busca_hibrida,
                query_text=_normalize(query),
                query_embedding=vetor,
                source_filter=source_filter,
                k_vector=6,
                k_text=8,
            )

            ctx["chunks"] = chunks
            ms = int((time.monotonic() - t0) * 1000)
            detail = f"{len(chunks)} chunks | source={source_filter or 'all'}"
            await emit(StepResult("retrieve", "ok", detail, ms,
                                  {"chunks": len(chunks),
                                   "top_score": chunks[0].get("rrf_score", 0) if chunks else 0}))

        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception("❌ [CHAIN] retrieve falhou: %s", e)
            ctx["chunks"] = []
            await emit(StepResult("retrieve", "error", str(e)[:80], ms))

    async def _step_grade_docs(self, ctx: dict, emit) -> None:
        """
        CRAG: avalia qualidade dos chunks recuperados.
        Score baseado no rrf_score do top chunk — sem LLM extra.
        """
        t0 = time.monotonic()
        chunks = ctx.get("chunks", [])

        if not chunks:
            ctx["crag_score"] = 0.0
            await emit(StepResult("grade_docs", "ok", "sem chunks", 0,
                                  {"crag_score": 0.0}))
            return

        # Média ponderada dos top-3 chunks
        scores = [c.get("rrf_score", 0.0) for c in chunks[:3]]
        avg = sum(scores) / len(scores) if scores else 0.0
        # Normaliza: rrf_score típico fica entre 0.01 e 0.06
        # Mapeia para 0-1: score 0.04+ = bom retrieval
        normalized = min(1.0, avg / 0.04)

        ctx["crag_score"] = normalized
        ms = int((time.monotonic() - t0) * 1000)
        detail = f"score={normalized:.3f} | top_rrf={scores[0]:.4f}"
        await emit(StepResult("grade_docs", "ok", detail, ms,
                              {"crag_score": round(normalized, 3)}))

    async def _step_generate(self, ctx: dict, emit) -> None:
        """Gera resposta com Gemini usando contexto RAG."""
        t0 = time.monotonic()
        await emit(StepResult("generate", "running"))

        try:
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()

            # Verifica se Gemini está bloqueado pelo admin
            if r.get("admin:gemini_blocked") == "1":
                ctx["answer"] = "🔧 Sistema em manutenção. Volte em breve!"
                await emit(StepResult("generate", "skip", "gemini bloqueado", 0))
                return

            # System prompt (sobrescrito via admin se existir)
            system_raw = r.get("admin:system_prompt")
            system_prompt = (
                (system_raw if isinstance(system_raw, str) else system_raw.decode())
                if system_raw
                else _system_prompt_default()
            )

            # Monta contexto dos chunks
            contexto_rag = ""
            for chunk in ctx.get("chunks", [])[:5]:
                content = chunk.get("content", "").strip()
                source = chunk.get("source", "")
                if content:
                    contexto_rag += f"\n[{source}]\n{content}\n---\n"

            # Prompt final
            user_ctx = ctx.get("user_context", {})
            nome = user_ctx.get("nome", "")
            curso = user_ctx.get("curso", "")
            facts = ctx.get("facts", [])
            facts_str = "\n".join(f"- {f}" for f in facts) if facts else ""
            history = ctx.get("history", "")

            prompt_parts = []
            if nome or curso:
                prompt_parts.append(
                    f"<contexto_aluno>Aluno: {nome}"
                    + (f" | Curso: {curso}" if curso else "")
                    + "</contexto_aluno>"
                )
            if facts_str:
                prompt_parts.append(f"<perfil_aluno>\n{facts_str}\n</perfil_aluno>")
            if history:
                prompt_parts.append(
                    f"<historico_conversa>\n{history}\n</historico_conversa>"
                )
            if contexto_rag:
                prompt_parts.append(
                    f"<informacao_documentos>\n{contexto_rag}\n</informacao_documentos>"
                )
            else:
                prompt_parts.append(
                    "<informacao_documentos>Nenhuma informação encontrada nos documentos."
                    "</informacao_documentos>"
                )
            prompt_parts.append(
                f"<pergunta_usuario>\n{ctx['message']}\n</pergunta_usuario>"
            )
            prompt = "\n\n".join(prompt_parts)

            # Chama Gemini via LangChain
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import HumanMessage, SystemMessage
            from src.infrastructure.settings import settings

            llm = ChatGoogleGenerativeAI(
                model=settings.GEMINI_MODEL,
                temperature=0.2,
                google_api_key=settings.GEMINI_API_KEY,
            )
            messages = [SystemMessage(content=system_prompt),
                        HumanMessage(content=prompt)]

            response = await llm.ainvoke(messages)
            answer = response.content or ""
            tokens = (
                response.usage_metadata.get("total_tokens", 0)
                if hasattr(response, "usage_metadata") and response.usage_metadata
                else 0
            )

            ctx["answer"] = answer
            ctx["tokens_used"] = tokens

            ms = int((time.monotonic() - t0) * 1000)
            detail = f"{len(answer)} chars | {tokens} tokens | {ms}ms"
            await emit(StepResult("generate", "ok", detail, ms,
                                  {"tokens": tokens, "chars": len(answer)}))

        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            logger.exception("❌ [CHAIN] generate falhou: %s", e)
            ctx["answer"] = (
                "Tive dificuldades ao gerar a resposta. "
                "Tente reformular sua pergunta. 🙏"
            )
            await emit(StepResult("generate", "error", str(e)[:100], ms))

    async def _step_save_memory(self, ctx: dict, emit) -> None:
        """Persiste turno no Redis (working memory)."""
        t0 = time.monotonic()
        try:
            import json
            from src.infrastructure.redis_client import get_redis_text
            r = get_redis_text()
            session_id = ctx["session_id"]
            key = f"chat:{session_id}"

            for role, content in [("user", ctx["message"]),
                                   ("assistant", ctx["answer"])]:
                entry = json.dumps({"role": role, "content": content},
                                   ensure_ascii=False)
                r.rpush(key, entry)

            r.ltrim(key, -20, -1)   # sliding window 10 pares
            r.expire(key, 1800)

            ms = int((time.monotonic() - t0) * 1000)
            await emit(StepResult("save_memory", "ok", "persisted", ms))

        except Exception as e:
            ms = int((time.monotonic() - t0) * 1000)
            logger.warning("⚠️  [CHAIN] save_memory falhou: %s", e)
            await emit(StepResult("save_memory", "error", str(e)[:80], ms))

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _get_embeddings(self):
        if self._embeddings is None:
            from src.rag.embeddings import get_embeddings
            self._embeddings = get_embeddings()
        return self._embeddings

    @staticmethod
    def _make_result(ctx: dict, steps: list[StepResult], t0: float) -> ChainResult:
        return ChainResult(
            answer=ctx.get("answer", ""),
            route=ctx.get("route", "GERAL"),
            crag_score=ctx.get("crag_score", 0.0),
            cache_hit=ctx.get("cache_hit", False),
            chunks_count=len(ctx.get("chunks", [])),
            tokens_used=ctx.get("tokens_used", 0),
            total_ms=int((time.monotonic() - t0) * 1000),
            steps=steps,
            error=ctx.get("error", ""),
        )

    @staticmethod
    async def _executar_crud_tool(action: str, args: dict, user_id: str) -> str:
        """Executa a tool CRUD após confirmação HITL."""
        try:
            from src.domain.tools.crud_tools import executar_tool
            result = await executar_tool(action, {**args, "user_id": user_id})
            return result.get("mensagem", "✅ Operação realizada com sucesso!")
        except Exception as e:
            logger.error("❌ [HITL] Tool falhou: %s", e)
            return f"❌ Erro ao executar a operação: {str(e)[:100]}"


# ─────────────────────────────────────────────────────────────────────────────
# Funções auxiliares puras (fáceis de testar)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    s = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode()
    return s.lower().strip()


_TERMOS_TECNICOS = frozenset({
    "matricula", "rematricula", "trancamento", "edital", "paes",
    "ac", "pcd", "br-ppi", "br-q", "2026.1", "2026.2",
    "prog", "ctic", "cecen", "siape",
})

_REGEX_ROUTES: list[tuple[str, re.Pattern, float]] = [
    ("SAUDACAO",  re.compile(r"^(oi|olá|ola|bom dia|boa tarde|boa noite|tudo bem|hey)\s*[!.]?$", re.I), 1.0),
    ("EDITAL",    re.compile(r"paes|vestibular|vaga|cota|inscri|edital|br.ppi|pcd|ac\b", re.I), 0.92),
    ("CALENDARIO", re.compile(r"matr[íi]cula|rematr|calend|semestre|prazo|início.aulas|feriado|trancamento", re.I), 0.90),
    ("CONTATOS",  re.compile(r"email|telefone|contato|endereço|ramal|coord|ctic\b|prog\b|reitoria", re.I), 0.88),
    ("WIKI",      re.compile(r"sigaa|senha|wifi|sistema|suporte|laborat|vpn|ti\b", re.I), 0.88),
]


def _route_regex(message: str) -> tuple[str, float]:
    """Roteamento rápido via regex. Retorna (rota, confiança)."""
    for route, pattern, conf in _REGEX_ROUTES:
        if pattern.search(message):
            return route, conf
    return "GERAL", 0.40


async def _route_knn(message: str) -> tuple[str, float]:
    """KNN semântico no Redis (fallback quando regex tem baixa confiança)."""
    try:
        import struct
        from src.rag.embeddings import get_embeddings
        from src.infrastructure.redis_client import get_redis, IDX_TOOLS
        from redis.commands.search.query import Query

        emb = get_embeddings()
        vetor = await asyncio.to_thread(emb.embed_query, _normalize(message))
        vetor_bytes = struct.pack(f"{len(vetor)}f", *vetor)

        r = get_redis()
        q = (
            Query("*=>[KNN 1 @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("name", "score")
            .dialect(2)
        )
        res = r.ft(IDX_TOOLS).search(q, {"vec": vetor_bytes})
        if res.docs:
            doc = res.docs[0]
            similarity = max(0.0, 1.0 - float(doc.score))
            name = getattr(doc, "name", "")
            route_map = {
                "consultar_calendario_academico": "CALENDARIO",
                "consultar_edital_paes_2026":     "EDITAL",
                "consultar_contatos_uema":        "CONTATOS",
                "consultar_wiki_ctic":            "WIKI",
            }
            route = route_map.get(name, "GERAL")
            return route, similarity
    except Exception as e:
        logger.debug("⚠️  [ROUTE KNN] falhou: %s", e)
    return "GERAL", 0.0


def _e_query_tecnica(message: str) -> bool:
    """True se a query já tem termos técnicos suficientes (sem transformação)."""
    norm = _normalize(message)
    palavras = set(re.split(r"\W+", norm))
    count = sum(1 for t in _TERMOS_TECNICOS if t in palavras or any(t in p for p in palavras))
    return count >= 2


def _system_prompt_default() -> str:
    return """Você é o Oráculo, assistente virtual oficial da UEMA.
Responda em até 3 parágrafos curtos. Use *negrito* para datas e prazos importantes.
Use APENAS as informações em <informacao_documentos>. Se não souber, diga claramente.
Nunca invente datas ou valores. Mantenha tom acadêmico mas acolhedor."""


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_chain_instance: OracleChain | None = None


def get_oracle_chain() -> OracleChain:
    """Singleton do OracleChain por processo."""
    global _chain_instance
    if _chain_instance is None:
        _chain_instance = OracleChain()
        logger.info("✅ [CHAIN] OracleChain inicializado (LangChain Runnables)")
    return _chain_instance