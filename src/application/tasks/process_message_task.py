"""
application/tasks/process_message_task.py — v6 (Cognitive OS)
=====================================================================

MUDANÇA PRINCIPAL:
  Migração completa para o Cognitive OS (Multi-agente).
  O antigo OracleChain (LangChain) foi totalmente removido.

Fluxo:
  - Validação de Identidade (Porteiro)
  - Redis Lock (anti-spam)
  - Chamada ao Cognitive OS
  - XACK no Redis Stream
  - Recovery de XPENDING no startup
"""
from __future__ import annotations

import asyncio
import logging
import time

from src.infrastructure.celery_app import celery_app, run_in_worker_loop
from src.infrastructure.redis_client import get_redis_text

logger = logging.getLogger(__name__)

_WARNING_DELAY = 3.0
_WARNING_MSG   = "⏳ Processando, aguarde um instante..."


@celery_app.task(
    name="processar_mensagem",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    queue="default",
)
def processar_mensagem_task(self, identity: dict, stream_id: str = "") -> None:
    """Entry point Celery. Usa Cognitive OS para gerar resposta."""
    run_in_worker_loop(_processar_async(self, identity, stream_id))


async def _processar_async(task, identity: dict, stream_id: str) -> None:
    phone   = identity.get("user_id") or identity.get("sender_phone", "unknown")
    chat_id = identity.get("chat_id", "")
    message = identity.get("body", "")

    # ── Validação de identidade (Porteiro) ────────────────────────────────────
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.pessoa_repository import PessoaRepository

    async with AsyncSessionLocal() as db:
        repo = PessoaRepository(db)
        identidade = await repo.obter_identidade_por_telefone(phone, chat_id)

    if not identidade:
        logger.info("🚫 [TASK] Usuário não cadastrado: %s", phone[-6:])
        from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
        gateway = EvolutionAdapter()
        await gateway.enviar_mensagem(
            chat_id,
            "👋 Para usar o Oráculo, você precisa estar cadastrado. "
            "Entre em contato com a secretaria ou CTIC."
        )
        if stream_id:
            _xack_stream(stream_id)
        return

    if identidade.status != "ativo":
        logger.info("🚫 [TASK] Usuário inativo: %s | status=%s", phone[-6:], identidade.status)
        if stream_id:
            _xack_stream(stream_id)
        return

    # Monta user_context rico a partir da identidade
    user_context = identidade.contexto_llm
    user_context["role"] = identidade.role
    user_context["is_admin"] = identidade.is_admin
    user_context["chat_id"] = chat_id

    if not message.strip():
        logger.debug("⏭️  Mensagem vazia ignorada para %s", phone)
        if stream_id:
            _xack_stream(stream_id)
        return

    # ── Lock Redis ─────────────────────────────────────────────────────────────
    r_text  = get_redis_text()
    lock    = r_text.lock(f"lock:msg:{phone}", timeout=90, blocking_timeout=5)

    if not lock.acquire():
        logger.warning("🔒 Lock indisponível para %s — retry.", phone[-6:])
        raise task.retry(countdown=5)

    gateway      = None
    warning_task = None
    success      = False

    try:
        from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
        gateway = EvolutionAdapter()

        # ── Aviso de latência após 3s ──────────────────────────────────────────
        warning_task = asyncio.create_task(
            _aviso_latencia(gateway, chat_id, phone, _WARNING_DELAY)
        )
        
        # ── Verifica bloqueio admin ────────────────────────────────────────────
        if r_text.get("admin:maintenance_mode") == "1":
            if not identity.get("is_admin"):
                if warning_task and not warning_task.done():
                    warning_task.cancel()
                await gateway.enviar_mensagem(
                    chat_id, "🔧 Sistema em manutenção. Volte em breve!"
                )
                success = True
                return



        # ── Carrega contexto da memória ────────────────────────────────────────
        from src.memory.container import create_memory_service
        mem_svc = create_memory_service()
        mem_ctx = mem_svc.carregar_contexto(user_id=phone, session_id=phone, query=message)
        
        # ── Orquestrador único (ADR 0008) ─────────────────────────────────────
        # ver src/application/orchestration/entrypoint.py
        from src.application.orchestration.entrypoint import processar as cognitive_processar

        t0 = time.monotonic()
        
        result_os = await cognitive_processar(
            message=message,
            session_id=phone,
            user_context=user_context,
            history=mem_ctx.historico.texto_formatado if mem_ctx.historico else "",
            fatos=[f.texto for f in mem_ctx.fatos] if mem_ctx.fatos else []
        )
        
        # ── Guardrails (Saída) ────────────────────────────────────────────────────
        from src.application.chain.guardrails import get_output_guardrail
        if result_os.answer:
            _, answer_final = get_output_guardrail().validate(result_os.answer, phone)
            result_os.answer = answer_final
        
        # Adaptador (Mock) para manter a compatibilidade com a função _salvar_metrica
        result = type("R", (), {
            "answer":       result_os.answer,
            "route":        result_os.rota,
            "crag_score":   0.9,
            "tokens_used":  0,
            "chunks_count": 0,
            "total_ms":     result_os.total_ms,
            "error":        result_os.error,
        })()
        
        ms = int((time.monotonic() - t0) * 1000)
        
        # ── Cancela aviso de latência ──────────────────────────────────────────
        if warning_task and not warning_task.done():
            warning_task.cancel()

        # ── Envia resposta ─────────────────────────────────────────────────────
        if result.answer:
            await gateway.enviar_mensagem(chat_id, result.answer)
            success = True
            logger.info(
                "✅ [TASK] Resposta enviada | phone=%s | %dms | route=%s",
                phone[-6:], ms, result.route
            )
            # Salva turno de resposta síncrona (fast-path)
            try:
                mem_svc.persistir_turno(
                    session_id=phone,
                    user_id=phone,
                    pergunta=message,
                    resposta=result.answer,
                    rota=result.route
                )
                mem_svc.extrair_fatos_background(user_id=phone, session_id=phone)
            except Exception as e:
                logger.warning("⚠️  Falha ao salvar turno síncrono na memória: %s", e)
        else:
            logger.warning("⚠️  [TASK] Sistema retornou resposta vazia para %s", phone[-6:])
            success = True   # não falha — pode ser HITL pendente

        # ── Registra métricas no Redis ─────────────────────────────────────────
        _salvar_metrica(phone, result)

    except Exception as exc:
        if warning_task and not warning_task.done():
            warning_task.cancel()
        logger.exception("❌ [TASK] Erro fatal para %s: %s", phone[-6:], exc)
        if gateway:
            try:
                await gateway.enviar_mensagem(
                    chat_id,
                    "😕 Tive um problema técnico. Tente novamente em instantes."
                )
            except Exception:
                pass
        raise task.retry(exc=exc, countdown=5 ** (task.request.retries + 1))

    finally:
        try:
            lock.release()
        except Exception:
            pass
    if success and stream_id:
        _xack_stream(stream_id)


# Em process_message_task.py — _aviso_latencia()
async def _aviso_latencia(gateway, chat_id: str, number: str, delay: float) -> None:
    await asyncio.sleep(1.0)
    await gateway.enviar_digitando(number, duration_ms=3000)  # simula "digitando"
    await asyncio.sleep(delay - 1.0)
    if chat_id:
        await gateway.enviar_mensagem(chat_id, _WARNING_MSG)


def _xack_stream(stream_id: str) -> None:
    try:
        from src.infrastructure.message_stream import get_message_stream
        get_message_stream().acknowledge(stream_id)
    except Exception as e:
        logger.error("❌ XACK falhou para %s: %s", stream_id, e)


def _salvar_metrica(phone: str, result) -> None:
    """Persiste métricas no Redis para o monitor."""
    try:
        import json
        from datetime import datetime
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        entrada = json.dumps({
            "ts":         datetime.now().isoformat(),
            "user_id":    phone[-8:],
            "route":      result.route,
            "crag_score": result.crag_score,
            "tokens":     result.tokens_used,
            "total_ms":   result.total_ms,
            "chunks":     result.chunks_count,
        }, ensure_ascii=False)
        r.lpush("monitor:logs", entrada)
        r.ltrim("monitor:logs", 0, 499)
    except Exception:
        pass


def recover_pending_messages() -> int:
    """Recovery de XPENDING no startup do worker."""
    try:
        from src.infrastructure.message_stream import get_message_stream
        stream = get_message_stream()
        summary = stream.get_pending_summary()
        if summary.get("total", 0) == 0:
            logger.info("✅ [TASK] Sem mensagens pendentes no startup.")
            return 0
        logger.warning("⚠️  [TASK] %d mensagem(ns) pendente(s). Iniciando recovery...",
                       summary["total"])
        recovered = stream.recover_pending()
        n = 0
        for item in recovered:
            sid      = item["stream_id"]
            identity = item["identity"]
            processar_mensagem_task.apply_async(args=[identity, sid], queue="default")
            n += 1
        logger.info("✅ [TASK] %d mensagem(ns) recuperada(s).", n)
        return n
    except Exception as e:
        logger.error("❌ [TASK] Stream recovery falhou: %s", e)
        return 0
    
    
@celery_app.task(name="processar_mensagem_whatsapp", bind=True, max_retries=2)
def processar_mensagem_whatsapp(
    self,
    remote_jid: str, sender_jid: str, text: str,
    push_name: str, is_group: bool, mentioned_bot: bool,
    msg_key_id: str = "", has_media: bool = False, media_type: str = "",
) -> None:
    run_in_worker_loop(_handle_message(
        remote_jid=remote_jid, sender_jid=sender_jid, text=text,
        push_name=push_name, is_group=is_group, mentioned_bot=mentioned_bot,
        msg_key_id=msg_key_id, has_media=has_media, media_type=media_type,
    ))


async def _handle_message(**kwargs) -> None:
    """Span raiz por mensagem (OpenTelemetry) — wrapper fino em vez de
    reindentar `_handle_message_impl` inteiro (função grande, muitos
    `return` cedo). NO-OP se ENABLE_TRACING=False. Correlaciona plan_id/
    session_id entre este span e os spans filhos (LLM/STT/TTS) criados nos
    pontos únicos de telemetria em llm_factory.py/audio_service.py."""
    from src.infrastructure.observability.tracing import get_tracer

    with get_tracer().start_as_current_span("processar_mensagem_whatsapp") as span:
        try:
            span.set_attribute("oraculo.session_id", kwargs.get("sender_jid", ""))
            span.set_attribute("oraculo.remote_jid", kwargs.get("remote_jid", ""))
        except Exception:
            pass
        await _handle_message_impl(**kwargs)


async def _handle_message_impl(**kwargs) -> None:
    from src.infrastructure.redis_client import get_redis_text
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
    from src.router.gatekeeper import MessageRouter, DispatchTarget
    from src.application.routing.command_builder import CommandContext, dispatch_admin, dispatch_public
    from src.agents.conversation.registration import RegistrationFunnel
    from src.infrastructure.settings import settings 

    r          = get_redis_text()
    gateway    = EvolutionAdapter()
    router     = MessageRouter()
    funnel     = RegistrationFunnel()

    sender     = kwargs["sender_jid"]
    remote_jid = kwargs["remote_jid"]
    text       = kwargs["text"]
    chat_id    = remote_jid  

    # 🛑 FILTRO DE ENTRADA: Se não for o grupo exclusivo do .env, ignora na hora
    if remote_jid != settings.ALLOWED_GROUP_ID:
        return

    logger.info("🧠 [CELERY WORKER] Processando mensagem do grupo homologado: %s", text)

    # ── Contexto do usuário ───────────────────────────────────────────────────
    user_data      = await _get_user_data(sender)
    is_admin       = _is_admin(sender, r)
    # Rodada de testes: DEV_TEST_SKIP_REGISTRATION libera qualquer remetente
    # a pular o funil de cadastro (ver settings.py) — sem isso, com
    # DEV_TEST_NO_DB_WRITE ligado, ninguém "vira registrado" de verdade
    # (o SELECT abaixo nunca encontra a linha em `pessoas`) e o gatekeeper
    # força REGISTER_MODE pra sempre, num loop sem saída.
    is_registered  = True if settings.DEV_TEST_SKIP_REGISTRATION else (user_data is not None)
    in_reg_mode    = r.get(f"register:mode:{sender}") == "1"
    
    allowed_group  = settings.ALLOWED_GROUP_ID

    decision = router.route(
        text=text, sender_jid=sender, is_group=kwargs["is_group"],
        is_admin=is_admin, is_registered=is_registered,
        in_register_mode=in_reg_mode,
        allowed_group_jid=allowed_group, remote_jid=remote_jid,
    )

    # TD-013 (ADR 0008 Fase 1): antes, TODA decisão IGNORE do gatekeeper era
    # reescrita pra LLM incondicionalmente — os filtros de segurança
    # (`nao_e_admin`, `admin_cmd_invalido`, `texto_vazio_apos_mention`,
    # `mensagem_inutil`, `privado_bloqueado_no_beta`) não bloqueavam nada, só
    # mudavam o motivo no log. O único IGNORE que o grupo de homologação
    # realmente quer sobrescrever é `sem_trigger_grupo` (o bot responde a
    # qualquer mensagem no grupo, com ou sem `@oraculo`/`$`/`!`). Os demais
    # voltam a de fato descartar a mensagem.
    if decision.target == DispatchTarget.IGNORE:
        if decision.reason == "sem_trigger_grupo":
            decision.target = DispatchTarget.LLM
        else:
            logger.info("🚫 [GATEKEEPER] Mensagem ignorada (%s) | sender=%s", decision.reason, sender)
            return

    ctx = CommandContext(
        sender_jid=sender, chat_id=chat_id, text=decision.text, redis_text=r,
    )

    # ── Funil de cadastro ─────────────────────────────────────────────────────
    if decision.target == DispatchTarget.REGISTER_MODE:
        reply = await funnel.process(sender, text, push_name=kwargs["push_name"], redis=r, chat_id=chat_id)
        if reply:
            await gateway.enviar_mensagem(chat_id, reply)
        return

    # ── Admin Command ─────────────────────────────────────────────────────────
    if decision.target == DispatchTarget.ADMIN_COMMAND:
        reply = await dispatch_admin(decision.command, ctx)
        await gateway.enviar_mensagem(chat_id, reply)
        return

    # ── Public Command ────────────────────────────────────────────────────────
    if decision.target == DispatchTarget.PUBLIC_COMMAND:
        reply = await dispatch_public(decision.command, ctx)
        await gateway.enviar_mensagem(chat_id, reply)
        return
    
    # ── LLM (COGNITIVE OS) ────────────────────────────────────────────────────
    if decision.target == DispatchTarget.LLM:
        user_context = {
            "nome":  user_data.get("nome", "") if user_data else kwargs.get("push_name", "Estudante"),
            "curso": user_data.get("curso", "") if user_data else "Instituição",
            "role":  user_data.get("role", "student") if user_data else "guest",
            "chat_id": chat_id,
            "has_media": kwargs.get("has_media", False),
            "media_type": kwargs.get("media_type", ""),
            "msg_key_id": kwargs.get("msg_key_id", ""),
        }

        # ── Humanização: simula "digitando..." no grupo ───────────────────────
        try:
            await gateway.enviar_digitando(
                number=remote_jid,   
                duration_ms=4000,
            )
        except Exception as e:
            logger.warning("⚠️  enviar_digitando falhou: %s", e)

        # ── Fase 3 do roadmap multimodal: pedido de resposta em áudio, opt-in ──
        # Detectado ANTES de montar `message` — a frase-gatilho ("em áudio",
        # "manda um áudio"...) é removida do texto que vai pro RAG/síntese,
        # senão o LLM vê a frase completa e responde SOBRE o pedido de áudio
        # em vez de responder a pergunta de verdade (bug real de produção
        # encontrado testando ao vivo — ver notas.md seção 12).
        from src.application.runtime.audio_intake import _quer_resposta_em_audio, _remover_pedido_audio
        quer_audio = _quer_resposta_em_audio(text)
        mensagem = _remover_pedido_audio(text) if quer_audio else text

        # ── Carrega contexto da memória ────────────────────────────────────────
        from src.memory.container import create_memory_service
        mem_svc = create_memory_service()
        mem_ctx = mem_svc.carregar_contexto(user_id=sender, session_id=sender, query=mensagem)

        # ── CognitiveOS: ───────────────────────────────────────────────────────
        # Orquestrador único (ADR 0008) — ver orchestration/entrypoint.py
        from src.application.orchestration.entrypoint import processar as cognitive_processar
        result_os = await cognitive_processar(
            message=mensagem,
            session_id=sender,
            user_context=user_context,
            history=mem_ctx.historico.texto_formatado if mem_ctx.historico else "",
            fatos=[f.texto for f in mem_ctx.fatos] if mem_ctx.fatos else []
        )
        
        # ── Guardrails (Saída) ────────────────────────────────────────────────────
        from src.application.chain.guardrails import get_output_guardrail
        if result_os.answer:
            _, answer_final = get_output_guardrail().validate(result_os.answer, sender)
            result_os.answer = answer_final
        
        # Adaptador (Mock) para manter a compatibilidade
        result = type("R", (), {
            "answer":       result_os.answer,
            "route":        result_os.rota,
            "crag_score":   0.0,
            "tokens_used":  0,
            "chunks_count": 0,
            "total_ms":     result_os.total_ms,
            "error":        result_os.error,
        })()


        answer = result.answer or ""
        if answer and not result.error:
            answer += "\n\n_Avalie: !1 (péssimo) a !5 (perfeito)_"

        if answer:
            await gateway.enviar_mensagem(chat_id, answer)
            logger.info("✅ [CELERY WORKER] Resposta da IA enviada com sucesso para o grupo!")
            # Salva turno de resposta síncrona (fast-path de grupo)
            try:
                mem_svc.persistir_turno(
                    session_id=sender,
                    user_id=sender,
                    pergunta=mensagem,
                    resposta=result.answer,
                    rota=result.route
                )
                mem_svc.extrair_fatos_background(user_id=sender, session_id=sender)
            except Exception as e:
                logger.warning("⚠️  Falha ao salvar turno síncrono (grupo) na memória: %s", e)

            # `quer_audio` já detectado no texto original, antes da limpeza —
            # ver bloco acima. `result.answer` (não `answer`, que já tem o
            # sufixo de avaliação "!1 a !5") é o que vai ser sintetizado.
            if quer_audio:
                try:
                    await _enviar_resposta_em_audio(gateway, chat_id, result.answer)
                except Exception as e:
                    logger.warning("⚠️  Falha ao gerar/enviar resposta em áudio: %s", e)

        try:
            _salvar_metrica(sender, result)
        except Exception:
            pass
        return


_TTS_TIMEOUT_S = 1000.0  # Celery pickup (queue=media) + cold-load do Kokoro (>15s em container
                       # com CPU mais fraca que dev local, confirmado real: 1º pedido de áudio
                       # após subir o worker estourou 25s — ver notas.md seção 12) + polling


async def _enviar_resposta_em_audio(gateway, chat_id: str, texto: str) -> None:
    """
    Sintetiza `texto` via TTS e envia como mídia de áudio.

    Despacha pro worker `media` via Celery (`worker_text_to_audio`) em vez de
    chamar AudioService.synthesize() inline neste processo — bug real de
    produção corrigido nesta sessão: rodar Kokoro (torch + spacy +
    curated-transformers) INLINE no worker `default` (mem_limit 768m,
    já compartilhado com Playwright/Chromium do fluxo SIGAA) OOM-matava o
    container inteiro (SIGKILL durante o carregamento do modelo — derrubava
    TODO o processamento de mensagens, não só a resposta em áudio). O worker
    `media` é o lugar certo (mesmo padrão do STT — ver
    `_transcrever_audio_recebido` em dispatcher.py). Ver notas.md seção 12.
    """
    import uuid
    from src.application.workers.registry import dispatch as worker_dispatch
    from src.capabilities.persistence.redis_state import get_result_cache

    plan_id = f"fast_tts_{chat_id[-6:] if chat_id else 'x'}_{uuid.uuid4().hex[:8]}"
    step_id = "s_tts"
    task_id = worker_dispatch("text_to_audio", {
        "plan_id": plan_id, "session_id": chat_id, "step_id": step_id,
        "text": texto, "lang": "pt",
    })
    if task_id is None:
        logger.warning("⚠️  [TTS] worker_text_to_audio não encontrado no registry.")
        return

    r = get_redis_text()
    deadline = time.monotonic() + _TTS_TIMEOUT_S
    payload  = None
    while time.monotonic() < deadline:
        payload = await get_result_cache(r, plan_id, step_id)
        if payload is not None:
            break
        await asyncio.sleep(0.3)

    ok = bool(payload and payload.get("status") == "ok" and payload.get("audio_b64"))
    if not ok:
        logger.warning("⚠️  [TTS] Síntese falhou ou deu timeout | plan=%s | payload=%s",
                        plan_id, payload)
        return

    # mimetype/filename precisam bater com o que o provider de TTS realmente
    # gera — Kokoro e gTTS produzem MP3 os dois (ver kokoro_tts_provider.py/
    # gtts_provider.py). "audio/wav" foi testado ao vivo e a Evolution API
    # aceita (HTTP 201) mas o áudio nunca chega no WhatsApp de verdade — bug
    # real corrigido nesta sessão, ver notas.md seção 12.
    await gateway.enviar_midia_base64(
        chat_id, payload["audio_b64"], mediatype="audio", mimetype="audio/mpeg",
        filename="resposta.mp3",
    )
    logger.info("🔊 [TTS] Resposta em áudio enviada | chat=%s", chat_id)

async def _get_user_data(phone: str) -> dict | None:
    try:
        from src.infrastructure.database.session import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as db:
            r = await db.execute(
                text("SELECT nome, curso, role FROM pessoas WHERE telefone=:p AND status='ativo'"),
                {"p": phone},
            )
            row = r.fetchone()
            return dict(row._mapping) if row else None
    except Exception:
        return None


def _is_admin(phone: str, redis_client) -> bool:
    from src.infrastructure.settings import settings
    admin_numbers = [n.strip() for n in settings.ADMIN_NUMBERS.split(",") if n.strip()]
    return phone in admin_numbers


@celery_app.task(name="enviar_resposta_whatsapp", bind=True, max_retries=3)
def enviar_resposta_whatsapp_task(self, synth_result: dict, delivery_ctx: dict) -> dict:
    import asyncio
    return asyncio.run(_enviar_resposta_whatsapp_async(self, synth_result, delivery_ctx))


async def _enviar_resposta_whatsapp_async(task, synth_result: dict, delivery_ctx: dict) -> dict:
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter
    from src.infrastructure.redis_client import get_redis_text
    
    plan_id = synth_result.get("plan_id") or delivery_ctx.get("plan_id")
    chat_id = delivery_ctx.get("chat_id")
    
    # ── Normalização de JID para evitar HTTP 400 na Evolution API ────────────
    if chat_id and "@" not in chat_id:
        # Remover caracteres não numéricos
        import re
        numeros = re.sub(r"\D", "", chat_id)
        # Se não tiver DDI
        if not numeros.startswith("55"):
            numeros = f"55{numeros}"
        chat_id = f"{numeros}@s.whatsapp.net"
    
    sender = delivery_ctx.get("sender_jid")
    answer = synth_result.get("answer") or ""
    status = synth_result.get("status") or "ok"
    error = synth_result.get("error") or ""

    # ── Guardrails (Saída - Async) ───────────────────────────────────────────
    from src.application.chain.guardrails import get_output_guardrail
    _, answer_final = get_output_guardrail().validate(answer, sender or chat_id)
    answer = answer_final

    r = get_redis_text()
    
    # 1. Marca plano como concluído no Redis
    r.set(f"plan:status:{plan_id}", "completed")

    if not answer:
        logger.warning("⚠️  [DELIVERY] Resposta de síntese vazia para o plan=%s", plan_id)
        return {"status": "empty", "plan_id": plan_id}

    # 2. Adiciona avaliação se status ok
    if status == "ok" and not error:
        answer += "\n\n_Avalie: !1 (péssimo) a !5 (perfeito)_"

    # 3. Envia mensagem via Gateway
    gateway = EvolutionAdapter()
    try:
        file_path = synth_result.get("file_path")
        if file_path:
            import os, base64, mimetypes
            if os.path.exists(file_path):
                mimetype, _ = mimetypes.guess_type(file_path)
                mimetype = mimetype or "application/octet-stream"
                media_type = synth_result.get("media_type", "document")
                
                with open(file_path, "rb") as f:
                    b64_data = base64.b64encode(f.read()).decode("utf-8")
                
                response = await gateway.enviar_midia_url(
                    number=chat_id,
                    url=b64_data,
                    mediatype=media_type,
                    mimetype=mimetype,
                    caption=answer,
                    filename=os.path.basename(file_path)
                )
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            else:
                logger.error("❌ Arquivo de mídia não encontrado: %s", file_path)
                response = await gateway.enviar_mensagem(chat_id, answer)
        else:
            response = await gateway.enviar_mensagem(chat_id, answer)
        
        from src.memory.services.redis_memory_service import get_cognitive_memory
        cog_mem = get_cognitive_memory()
        user_id = sender or chat_id
        
        if not response: # Ou se status_code >= 400
            # O whatsapp falhou
            await cog_mem.save_task_result(
                session_id=user_id,
                worker=delivery_ctx.get("route", "GERAL"),
                result="⚠️ A tarefa foi concluída, mas falhei ao entregar a mensagem final pelo WhatsApp."
            )
            # Limpa a memoria operacional
            await cog_mem.clear_operational(user_id)
            return {"status": "failed", "reason": "evolution_api_error"}

        logger.info("✅ [DELIVERY] Resposta entregue com sucesso via WhatsApp para %s", chat_id)
        
        # Salva turno de resposta assíncrona
        try:
            from src.memory.container import create_memory_service
            mem_svc = create_memory_service()
            query = delivery_ctx.get("query") or ""
            if query and answer and status == "ok":
                mem_svc.persistir_turno(
                    session_id=user_id,
                    user_id=user_id,
                    pergunta=query,
                    resposta=synth_result.get("answer") or answer, # evita o sufixo de avaliação no histórico
                    rota=delivery_ctx.get("route", "GERAL")
                )
                mem_svc.extrair_fatos_background(user_id=user_id, session_id=user_id)
                
                # Cognitive Memory Service update
                await cog_mem.add_turn(user_id, "user", query)
                await cog_mem.add_turn(user_id, "assistant", synth_result.get("answer") or answer)
                await cog_mem.save_task_result(
                    session_id=user_id,
                    worker=delivery_ctx.get("route", "GERAL"),
                    result=(synth_result.get("answer") or answer)[:400],
                )
                await cog_mem.clear_operational(user_id)
        except Exception as e:
            logger.warning("⚠️  Falha ao salvar turno assíncrono na memória: %s", e)
    except Exception as exc:
        logger.error("❌ [DELIVERY] Falha ao entregar resposta: %s", exc)
        raise task.retry(exc=exc, countdown=3)

    # 4. Salva métricas
    result_metric = type("R", (), {
        "route": delivery_ctx.get("route", "GERAL"),
        "crag_score": 0.0,
        "tokens_used": 0,
        "chunks_count": 0,
        "total_ms": synth_result.get("latency_ms", 0),
        "error": error,
    })()
    try:
        _salvar_metrica(sender, result_metric)
    except Exception:
        pass

    return {"status": "delivered", "plan_id": plan_id}


@celery_app.task(name="enviar_aviso_latencia", bind=True)
def enviar_aviso_latencia_task(self, chat_id: str, plan_id: str) -> dict:
    import asyncio
    return asyncio.run(_enviar_aviso_latencia_async(chat_id, plan_id))


async def _enviar_aviso_latencia_async(chat_id: str, plan_id: str) -> dict:
    from src.infrastructure.redis_client import get_redis_text
    from src.infrastructure.adapters.evolution_adapter import EvolutionAdapter

    r = get_redis_text()
    status = r.get(f"plan:status:{plan_id}")
    
    if status == "completed" or (isinstance(status, bytes) and status.decode() == "completed"):
        logger.info("ℹ️  [LATENCY WARNING] Plano %s concluído. Aviso cancelado.", plan_id)
        return {"status": "skipped", "reason": "completed"}

    logger.warning("⏳ [LATENCY WARNING] Plano %s pendente. Enviando aviso de latência...", plan_id)
    gateway = EvolutionAdapter()
    try:
        await gateway.enviar_digitando(chat_id, duration_ms=2000)
        await gateway.enviar_mensagem(chat_id, _WARNING_MSG)
        return {"status": "sent", "plan_id": plan_id}
    except Exception as exc:
        logger.error("❌ [LATENCY WARNING] Falha ao enviar aviso: %s", exc)
        return {"status": "error", "error": str(exc)}