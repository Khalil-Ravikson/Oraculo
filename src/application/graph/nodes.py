# src/application/graph/nodes.py
"""
Nós do LangGraph — cada nó é uma função pura assíncrona.

PRINCÍPIO: cada nó faz UMA coisa. Sem lógica de roteamento aqui
(isso fica em edges.py). Sem acesso direto a Redis ou banco (via portas).

ORDEM DO GRAFO:
  admin_interceptor → classify → [rag | ask_confirm → exec_tool | greeting] → respond
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.application.graph.state import OracleState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 0 — Admin Interceptor (topo do grafo, verifica ANTES de tudo)
# ─────────────────────────────────────────────────────────────────────────────

async def node_admin_interceptor(state: "OracleState") -> dict:
    """
    Verifica se a mensagem é um comando admin e se o token já foi validado.

    FLUXO:
      1. Não é admin → passa para classify (sem alteração)
      2. É admin, sem token verificado → verifica se a mensagem é o token
           a) Token correto → marca como verificado, processa comando
           b) Token errado  → solicita novamente
      3. É admin, token verificado → processa comando normalmente

    SEGURANÇA:
      O token de admin é um TOTP ou senha extra definida no .env
      (ADMIN_CONFIRMATION_TOKEN). Diferente da senha do portal web,
      este token é usado para comandos via WhatsApp.
    """
    if not state.get("is_admin"):
        return {}   # não é admin → grafo continua normal

    from src.infrastructure.settings import settings
    msg = (state.get("current_input") or "").strip()

    # ── Retomada após HITL admin ──────────────────────────────────────────────
    if state.get("pending_confirmation"):
        return _processar_resposta_hitl(state, msg)

    # ── Detecta se é um comando admin (começa com ! ou /) ────────────────────
    is_command = bool(re.match(r'^[!/]', msg))
    if not is_command:
        return {}   # admin enviando mensagem normal → trata como aluno

    # ── Verifica double-check de token ────────────────────────────────────────
    if not state.get("admin_token_verified"):
        # Guarda o comando original e solicita o token
        return {
            "pending_confirmation": (
                f"🔐 *Confirmação necessária*\n\n"
                f"Comando recebido: `{msg[:50]}`\n\n"
                f"Por segurança, insira o *token de autorização admin* "
                f"(ADMIN_CONFIRMATION_TOKEN do .env):"
            ),
            "admin_command":        msg,
            "confirmation_result":  "awaiting_token",
            "route":                "respond_only",
            "final_response": (
                f"🔐 *Confirmação necessária*\n\n"
                f"Comando: `{msg[:50]}`\n\n"
                f"Insira o token de autorização:"
            ),
        }

    # Admin já verificado → roteia para handler de comandos
    return {
        "route": "admin",
        "admin_command": msg,
    }


def _processar_resposta_hitl(state: "OracleState", msg: str) -> dict:
    """Processa resposta do admin para uma confirmação pendente."""
    from src.infrastructure.settings import settings

    confirmation_type = state.get("confirmation_result")

    # Verificação do token de autorização
    if confirmation_type == "awaiting_token":
        token_correto = settings.ADMIN_CONFIRMATION_TOKEN
        if msg == token_correto:
            logger.info("✅ Admin token verificado com sucesso.")
            return {
                "admin_token_verified": True,
                "pending_confirmation": None,
                "confirmation_result":  None,
                "route": "admin",
                # Mantém o comando original que estava guardado
            }
        else:
            logger.warning("❌ Token admin incorreto.")
            return {
                "pending_confirmation": None,
                "confirmation_result":  "cancelled",
                "final_response": "❌ Token inválido. Comando cancelado por segurança.",
                "route": "respond_only",
            }

    # Confirmação de ação CRUD (SIM/NÃO)
    msg_lower = msg.lower().strip()
    if msg_lower in ("sim", "s", "yes", "y", "confirmo"):
        return {"confirmation_result": "confirmed"}
    elif msg_lower in ("não", "nao", "n", "no", "cancelar"):
        return {
            "confirmation_result": "cancelled",
            "final_response": "❌ Ação cancelada.",
            "route": "respond_only",
        }
    else:
        return {
            "final_response": (
                f"{state.get('pending_confirmation', '')}\n\n"
                f"Responda *SIM* ou *NÃO*."
            ),
            "route": "respond_only",
        }


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 1 — Admin Command Handler
# ─────────────────────────────────────────────────────────────────────────────

async def node_admin_command(state: "OracleState") -> dict:
    """
    Executa comandos admin sem passar pelo RAG.
    Registra toda ação no IAuditLog.
    """
    command = (state.get("admin_command") or "").strip()
    admin_id = state.get("user_id", "admin")

    logger.info("⚙️  Admin command: %s | admin=%s", command, admin_id)

    try:
        from src.application.use_cases.admin_commands import AdminCommandsUseCase
        # O use case é stateless — criado aqui, sem injeção de estado
        use_case = AdminCommandsUseCase()
        resposta = await use_case.executar(command, admin_id)
        return {
            "final_response":  resposta,
            "audit_action":    command,
            "admin_command":   None,
        }
    except Exception as e:
        logger.exception("❌ Erro no comando admin '%s': %s", command, e)
        return {
            "final_response": f"❌ Erro ao executar `{command[:30]}`: `{str(e)[:80]}`",
        }


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 2 — Classify (determina a rota)
# ─────────────────────────────────────────────────────────────────────────────

async def node_classify(state: "OracleState") -> dict:
    """
    Classifica a intenção da mensagem e define a rota.
    Não chama LLM — usa routing semântico Redis (0 tokens).
    """
    msg = (state.get("current_input") or "").strip()

    # ── Retomada HITL (confirmação CRUD pendente) ─────────────────────────────
    if state.get("pending_confirmation") and state.get("confirmation_result") not in (
        "confirmed", "cancelled", "awaiting_token"
    ):
        msg_lower = msg.lower().strip()
        if msg_lower in ("sim", "s", "yes", "y", "confirmo", "ok"):
            return {"confirmation_result": "confirmed"}
        elif msg_lower in ("não", "nao", "n", "no", "cancelar"):
            return {
                "confirmation_result": "cancelled",
                "final_response": "❌ Operação cancelada.",
                "route": "respond_only",
            }
        else:
            return {
                "final_response": (
                    f"{state.get('pending_confirmation', '')}\n\n"
                    f"Responda *SIM* ou *NÃO*."
                ),
                "route": "respond_only",
            }

    # ── Verificação de modo manutenção ────────────────────────────────────────
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        if r.get("admin:maintenance_mode") == "1" and not state.get("is_admin"):
            return {
                "final_response": (
                    "🔧 *O Oráculo está em manutenção para melhorias.*\n\n"
                    "Voltarei em breve com novidades! 🎓"
                ),
                "route": "respond_only",
            }
    except Exception:
        pass

    # ── Routing semântico (0 tokens) ──────────────────────────────────────────
    try:
        from src.domain.semantic_router import rotear
        from src.domain.entities import EstadoMenu
        resultado = rotear(msg, EstadoMenu.MAIN)
        rota = resultado.rota.value.lower()
    except Exception:
        rota = "geral"

    # ── Detecta intent CRUD ───────────────────────────────────────────────────
    _CRUD_RE = re.compile(
        r"(atualiz|mudar|alterar|corrig|trocar|editar|modificar).{0,30}"
        r"(nome|email|telefone|matrícula|curso|senha|dados)",
        re.IGNORECASE,
    )
    if _CRUD_RE.search(msg) and state.get("user_role") != "guest":
        return {"route": "crud", "tool_name": "update_student_data"}

    # ── Saudações curtas → resposta imediata ──────────────────────────────────
    if len(msg.split()) <= 3 and re.match(
        r"^(oi|olá|ola|bom\s*dia|boa\s*tarde|boa\s*noite|ei|hey)$",
        msg, re.IGNORECASE,
    ):
        return {"route": "greeting"}

    return {"route": rota or "rag"}


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 3 — RAG (recuperação + geração)
# ─────────────────────────────────────────────────────────────────────────────

async def node_rag(state: "OracleState") -> dict:
    """
    RAG custom em Python puro — sem LangChain chains.
    Lê o system_prompt do Redis (prompt dinâmico do admin).
    """
    from src.application.graph.prompts import montar_prompt_geracao

    msg     = state.get("current_input", "")
    user_ctx = state.get("user_context", {})
    perfil  = (
        f"Aluno: {state.get('user_name', '')} | "
        f"Curso: {user_ctx.get('curso', '?')} | "
        f"Período: {user_ctx.get('periodo', '?')}"
        if user_ctx.get("curso") else ""
    )

    # ── Recupera contexto RAG ─────────────────────────────────────────────────
    contexto_rag = ""
    crag_score   = 0.0
    try:
        from src.application.use_cases.retrieve_context import RetrieveContextUseCase
        from src.infrastructure.adapters.redis_vector_adapter import RedisVectorAdapter
        from src.rag.query_transform import QueryTransformada

        qt = QueryTransformada(
            query_original=msg, query_principal=msg, foi_transformada=False,
        )
        resultado = await RetrieveContextUseCase(RedisVectorAdapter()).executar(qt)
        contexto_rag = resultado.contexto_formatado if resultado.encontrou else ""
        if resultado.chunks:
            scores = [c.rrf_score for c in resultado.chunks if c.rrf_score > 0]
            crag_score = sum(scores) / len(scores) if scores else 0.0
    except Exception as e:
        logger.warning("⚠️  RAG falhou, gerando sem contexto: %s", e)

    # ── System prompt dinâmico (admin pode alterar via Redis) ─────────────────
    system_prompt = _get_dynamic_system_prompt()

    # ── Monta prompt final ────────────────────────────────────────────────────
    prompt = montar_prompt_geracao(
        pergunta=msg,
        contexto_rag=contexto_rag,
        perfil_usuario=perfil,
    )

    # ── Geração via LLM provider ──────────────────────────────────────────────
    resposta_texto = "Desculpe, não consegui gerar uma resposta no momento."
    try:
        from src.infrastructure.adapters.gemini_provider import GeminiProvider
        llm = GeminiProvider()
        resposta = await llm.gerar_resposta_async(
            prompt=prompt,
            system_instruction=system_prompt,
        )
        if resposta.sucesso:
            resposta_texto = resposta.conteudo
    except Exception as e:
        logger.exception("❌ LLM falhou: %s", e)

    return {
        "final_response": resposta_texto,
        "rag_context":    contexto_rag[:500] if contexto_rag else "",
        "crag_score":     crag_score,
    }


def _get_dynamic_system_prompt() -> str:
    """Lê o system prompt do Redis — permite alteração em tempo real pelo admin."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        from src.application.graph.prompts import SYSTEM_UEMA
        custom = get_redis_text().get("admin:system_prompt")
        return custom or SYSTEM_UEMA
    except Exception:
        from src.application.graph.prompts import SYSTEM_UEMA
        return SYSTEM_UEMA


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 4 — Ask Confirm (HITL — pede confirmação antes do CRUD)
# ─────────────────────────────────────────────────────────────────────────────

async def node_ask_confirm(state: "OracleState") -> dict:
    """
    Para a execução e pede confirmação ao usuário.
    O LangGraph pausará aqui (interrupt_before=["exec_tool_node"]).
    """
    tool   = state.get("tool_name", "ação")
    args   = state.get("tool_args", {})

    # Descreve o que vai acontecer em linguagem natural
    descricao = _descrever_acao(tool, args)

    pergunta = (
        f"⚠️ *Confirmação necessária*\n\n"
        f"{descricao}\n\n"
        f"Responda *SIM* para confirmar ou *NÃO* para cancelar."
    )

    return {
        "pending_confirmation": pergunta,
        "confirmation_result":  "pending",
        "final_response":       pergunta,
    }


def _descrever_acao(tool: str, args: dict) -> str:
    """Converte nome da tool e argumentos em texto legível."""
    descricoes = {
        "update_student_email":    f"Alterar e-mail para `{args.get('novo_valor', '?')}`",
        "update_student_telefone": f"Alterar telefone para `{args.get('novo_valor', '?')}`",
        "update_student_data":     f"Atualizar dados: {args}",
    }
    return descricoes.get(tool, f"Executar ação `{tool}`")


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 5 — Exec Tool (executa CRUD após confirmação)
# ─────────────────────────────────────────────────────────────────────────────

async def node_exec_tool(state: "OracleState") -> dict:
    """
    Executa a tool CRUD no banco de dados.
    Só é alcançado após interrupt_before ser liberado (confirmação recebida).
    Registra a ação no audit log.
    """
    tool    = state.get("tool_name", "")
    args    = dict(state.get("tool_args") or {})
    user_id = state.get("user_id", "")

    # Garante que user_id está nos args (tools de CRUD sempre precisam)
    args["user_id"] = user_id

    logger.info("🔧 Exec tool '%s' para user=%s", tool, user_id)

    try:
        from src.tools.crud_tools import executar_tool
        resultado = await executar_tool(tool, args)
        mensagem  = resultado.get("mensagem", "✅ Ação concluída.")

        # Registra no audit log
        await _registrar_audit(user_id, tool, args, "ok")

        return {
            "final_response":       mensagem,
            "pending_confirmation": None,
            "confirmation_result":  None,
            "tool_name":            None,
            "tool_args":            None,
        }
    except Exception as e:
        logger.exception("❌ Tool '%s' falhou: %s", tool, e)
        await _registrar_audit(user_id, tool, args, f"erro: {str(e)[:80]}")
        return {
            "final_response": (
                "❌ Ocorreu um erro ao executar a ação. "
                "Tente novamente ou contate o suporte."
            ),
            "pending_confirmation": None,
            "confirmation_result":  None,
        }


async def _registrar_audit(user_id: str, action: str, payload: dict, resultado: str) -> None:
    """Registra ação no audit log de forma não-bloqueante."""
    try:
        from src.infrastructure.adapters.redis_audit_log import RedisAuditLog
        await RedisAuditLog().registar(
            admin_id=user_id, action=action, target=user_id,
            payload=payload, resultado=resultado,
        )
    except Exception as e:
        logger.debug("⚠️  Audit log falhou (não crítico): %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 6 — Greeting (saudação rápida, sem RAG)
# ─────────────────────────────────────────────────────────────────────────────

async def node_greeting(state: "OracleState") -> dict:
    """Resposta de saudação personalizada — 0 tokens de RAG."""
    from src.application.use_cases.messages import MSG_BOAS_VINDAS_USUARIO
    nome = (state.get("user_name") or "").split()[0] or "Olá"
    return {
        "final_response": MSG_BOAS_VINDAS_USUARIO.format(nome=nome),
    }


# ─────────────────────────────────────────────────────────────────────────────
# NÓ 7 — Respond (finaliza e registra métricas)
# ─────────────────────────────────────────────────────────────────────────────

async def node_respond(state: "OracleState") -> dict:
    """
    Nó terminal — registra métricas no Redis e retorna sem alteração de estado.
    """
    try:
        import json, datetime
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        entrada = json.dumps({
            "ts":          datetime.datetime.now().isoformat(),
            "user_id":     state.get("user_id", ""),
            "role":        state.get("user_role", ""),
            "route":       state.get("route", ""),
            "crag_score":  state.get("crag_score", 0.0),
            "pergunta":    (state.get("current_input") or "")[:100],
            "resposta":    (state.get("final_response") or "")[:200],
        }, ensure_ascii=False)
        r.lpush("monitor:logs", entrada)
        r.ltrim("monitor:logs", 0, 499)
    except Exception:
        pass

    return {}