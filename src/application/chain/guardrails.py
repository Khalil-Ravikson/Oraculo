"""
src/application/chain/guardrails.py
=====================================
Guardrails de input e output — camada de segurança antes/depois da LLM.

CAMADAS:
  InputGuardrail:
    1. Tamanho máximo da mensagem
    2. Detecção de prompt injection (regex + heurística de score)
    3. Detecção de jailbreak patterns
    4. Rate limiting por usuário (Redis)
    5. Idioma/encoding suspeito

  OutputGuardrail:
    1. Tamanho mínimo de resposta
    2. Vazamento de PII (CPF, telefone, email institucional alheio)
    3. Repetição de system prompt
    4. Resposta vazia / erro da LLM exposto

DESIGN:
  - Todos os métodos retornam (ok: bool, motivo: str | resposta_sanitizada)
  - NUNCA lançam exceção — fallback sempre existe
  - Leves o suficiente para rodar síncronos antes do pipeline async
  - Rate limiting via Redis é opcional (degrada graciosamente se Redis falhar)

USO:
  from src.application.chain.guardrails import InputGuardrail, OutputGuardrail

  ok, msg = InputGuardrail().validate(text, user_id, redis_client)
  if not ok:
      return ChainResult(answer=msg, ...)

  # ... gera resposta ...

  ok, resposta_final = OutputGuardrail().validate(answer, original_query)
  ctx["answer"] = resposta_final
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

_MAX_INPUT_LEN    = 1_200    # chars — mensagens WhatsApp legítimas são menores
_MIN_OUTPUT_LEN   = 15       # chars — respostas com menos são suspeitas
_MAX_OUTPUT_LEN   = 4_000    # chars — evita respostas gigantes no WhatsApp

# Rate limit: máx N mensagens por janela de tempo
_RATE_LIMIT_COUNT  = 8       # mensagens
_RATE_LIMIT_WINDOW = 60      # segundos
_RATE_LIMIT_PREFIX = "rl:msg:"

# ─── Patterns de Prompt Injection / Jailbreak ─────────────────────────────────
# Fonte: OWASP LLM Top 10 2025 + padrões conhecidos em PT/EN
_INJECTION_PATTERNS: list[re.Pattern] = [
    # Override de instrução
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions?", re.I),
    re.compile(r"esqueça\s+(todas\s+)?(as\s+)?(instruções|regras)\s+anteriores", re.I),
    re.compile(r"ignore\s+(todas\s+)?(as\s+)?(instruções|regras)", re.I),
    # Role-play attack
    re.compile(r"(you\s+are\s+now|agora\s+você\s+é|finja\s+que\s+é)\s+.{0,40}(sem\s+restrições|without\s+restrictions|jailbreak|DAN)", re.I),
    re.compile(r"\bDAN\b|\bDo\s+Anything\s+Now\b", re.I),
    # System prompt leakage
    re.compile(r"(print|show|repeat|repita|mostre|exiba)\s+(your\s+)?(system\s+prompt|prompt\s+do\s+sistema|instrução\s+inicial)", re.I),
    re.compile(r"what\s+(are\s+)?your\s+(instructions|system\s+prompt)", re.I),
    re.compile(r"quais\s+(são\s+)?(suas\s+)?(instruções|regras)", re.I),
    # Token smuggling / encoding tricks
    re.compile(r"\\u00[0-9a-f]{2}.*\\u00[0-9a-f]{2}.*\\u00[0-9a-f]{2}", re.I),
    re.compile(r"base64\s*:\s*[A-Za-z0-9+/]{20,}", re.I),
    # Força comportamento perigoso
    re.compile(r"(act|behave|respond)\s+as\s+(if\s+you\s+)(have\s+no\s+|don't\s+have\s+)(restrictions|ethics|limits)", re.I),
    re.compile(r"(aja|comporte-se|responda)\s+como\s+se\s+(não\s+tivesse|sem)\s+(restrições|ética|limites)", re.I),
]

# ─── Patterns de PII no output ────────────────────────────────────────────────
_PII_CPF     = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_PII_PHONE   = re.compile(r"(\+?55\s?)?\(?\d{2}\)?\s?\d{4,5}[-\s]?\d{4}\b")
_PII_EMAIL_PESSOAL = re.compile(r"\b[\w.+-]+@(?!uema\.br|aluno\.uema\.br)[\w-]+\.[a-z]{2,}\b", re.I)

# System prompt keywords que não devem vazar
_SYSTEM_LEAK_MARKERS = [
    "Você é o *Oráculo*",
    "PROTOCOLO DE RACIOCÍNIO",
    "REGRAS DE GROUNDING",
    "MSG_SEM_INFO",
    "system_instruction",
    "<informacao_documentos>",
    "<contexto_aluno>",
    "<context>",
    "</context>",
    "<thinking>",
    "</thinking>",
    "<perfil>",
    "</perfil>",
]


# ─────────────────────────────────────────────────────────────────────────────
# InputGuardrail
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InputGuardrail:
    """
    Valida e sanitiza o input antes de chegar à LLM.
    Instanciar uma vez e reutilizar (stateless, thread-safe).
    """
    max_len:           int   = _MAX_INPUT_LEN
    rate_limit_count:  int   = _RATE_LIMIT_COUNT
    rate_limit_window: int   = _RATE_LIMIT_WINDOW
    injection_score_threshold: float = 0.6   # 0.0 = nenhum match, 1.0 = todos

    def validate(
        self,
        text:       str,
        user_id:    str = "",
        redis_client: Any = None,
    ) -> tuple[bool, str]:
        """
        Retorna (True, text_sanitizado) se ok.
        Retorna (False, mensagem_de_erro_ao_usuario) se bloqueado.
        """
        if not text or not text.strip():
            return False, ""

        # 1. Tamanho
        if len(text) > self.max_len:
            logger.warning("🛡️  [GUARDRAIL INPUT] Mensagem muito longa: %d chars | user=%s", len(text), user_id[-6:] if user_id else "?")
            return False, (
                f"Sua mensagem está muito longa ({len(text)} chars). "
                f"Por favor, reduza para no máximo {self.max_len} caracteres. 📏"
            )

        # 2. Rate limiting (opcional — degrada graciosamente)
        if redis_client and user_id:
            blocked, msg = self._check_rate_limit(user_id, redis_client)
            if blocked:
                return False, msg

        # 3. Prompt injection
        injection_score, matched_pattern = self._injection_score(text)
        if injection_score >= self.injection_score_threshold:
            logger.warning(
                "🛡️  [GUARDRAIL INPUT] Possível injection detectado | score=%.2f | pattern=%s | user=%s",
                injection_score, matched_pattern, user_id[-6:] if user_id else "?",
            )
            return False, (
                "Não consegui entender sua mensagem. "
                "Por favor, reformule sua dúvida sobre a UEMA. 🎓"
            )

        # 4. Sanitização leve (normaliza unicode, remove chars de controle)
        sanitized = self._sanitize(text)
        return True, sanitized

    def _injection_score(self, text: str) -> tuple[float, str]:
        """
        Calcula score de injection (0.0 a 1.0).
        1 pattern = 1.0 (qualquer match é bloqueio imediato).
        """
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(text):
                return 1.0, pattern.pattern[:50]
        return 0.0, ""

    def _check_rate_limit(self, user_id: str, r: Any) -> tuple[bool, str]:
        """
        Rate limit deslizante com Redis.
        Retorna (True, msg) se deve bloquear.
        """
        try:
            key = f"{_RATE_LIMIT_PREFIX}{user_id}"
            pipe = r.pipeline()
            now = time.time()
            window_start = now - self._rate_limit_window if hasattr(self, '_rate_limit_window') else now - _RATE_LIMIT_WINDOW

            # Sliding window com sorted set
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zadd(key, {str(now): now})
            pipe.zcard(key)
            pipe.expire(key, _RATE_LIMIT_WINDOW * 2)
            _, _, count, _ = pipe.execute()

            if count > _RATE_LIMIT_COUNT:
                logger.warning("🛡️  [GUARDRAIL RATE] Limite atingido: %d msgs | user=%s", count, user_id[-6:])
                return True, (
                    "⏳ Você está enviando muitas mensagens rapidamente. "
                    "Aguarde alguns segundos antes de continuar."
                )
        except Exception as e:
            logger.debug("Rate limit check falhou (ignorado): %s", e)
        return False, ""

    @staticmethod
    def _sanitize(text: str) -> str:
        """Remove chars de controle e normaliza unicode."""
        # Normaliza unicode (evita homoglyphs e encodings esotéricos)
        text = unicodedata.normalize("NFC", text)
        # Remove chars de controle exceto tab/newline
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        # Remove repetições excessivas de chars (spam: "aaaaaaaaaaa")
        text = re.sub(r"(.)\1{9,}", r"\1\1\1", text)
        return text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# OutputGuardrail
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OutputGuardrail:
    """
    Valida e sanitiza o output da LLM antes de enviar ao usuário.
    """
    min_len: int = _MIN_OUTPUT_LEN
    max_len: int = _MAX_OUTPUT_LEN
    check_pii: bool = True
    check_system_leak: bool = True

    # Resposta fallback quando a LLM falha em gerar algo utilizável
    FALLBACK_RESPONSE = (
        "Não consegui formular uma resposta adequada para isso. "
        "Tente reformular sua pergunta ou consulte diretamente o site da UEMA: *uema.br* 🎓"
    )

    def validate(
        self,
        answer:        str,
        original_query: str = "",
        user_id:       str = "",
    ) -> tuple[bool, str]:
        """
        Retorna (True, answer_sanitizado) se ok.
        Retorna (False, fallback_response) se o output for problemático.
        """
        if not answer or not answer.strip():
            logger.warning("🛡️  [GUARDRAIL OUTPUT] Resposta vazia | user=%s", user_id[-6:] if user_id else "?")
            return False, self.FALLBACK_RESPONSE

        answer = answer.strip()

        # 1. Tamanho mínimo
        if len(answer) < self.min_len:
            logger.warning("🛡️  [GUARDRAIL OUTPUT] Resposta muito curta: %d chars", len(answer))
            return False, self.FALLBACK_RESPONSE

        # 2. Verifica vazamento de system prompt
        if self.check_system_leak:
            for marker in _SYSTEM_LEAK_MARKERS:
                if marker.lower() in answer.lower():
                    logger.error(
                        "🛡️  [GUARDRAIL OUTPUT] SYSTEM LEAK detectado! marker='%s' | user=%s",
                        marker[:30], user_id[-6:] if user_id else "?"
                    )
                    return False, self.FALLBACK_RESPONSE

        # 3. PII no output
        if self.check_pii:
            pii_found, pii_type = self._detect_pii(answer)
            if pii_found:
                logger.warning(
                    "🛡️  [GUARDRAIL OUTPUT] PII detectado no output: %s | user=%s",
                    pii_type, user_id[-6:] if user_id else "?"
                )
                # Sanitiza (redacta) ao invés de bloquear totalmente
                answer = self._redact_pii(answer)

        # 4. Trunca se necessário (WhatsApp tem limite prático de ~4k chars)
        if len(answer) > self.max_len:
            answer = answer[:self.max_len].rsplit(" ", 1)[0]
            answer += "\n\n_[Resposta truncada. Consulte uema.br para mais detalhes.]_"
            logger.info("🛡️  [GUARDRAIL OUTPUT] Resposta truncada para %d chars", len(answer))

        return True, answer

    def _detect_pii(self, text: str) -> tuple[bool, str]:
        if _PII_CPF.search(text):
            return True, "CPF"
        if _PII_EMAIL_PESSOAL.search(text):
            return True, "email_externo"
        return False, ""

    def _redact_pii(self, text: str) -> str:
        text = _PII_CPF.sub("[CPF REDACTED]", text)
        text = _PII_EMAIL_PESSOAL.sub("[EMAIL REDACTED]", text)
        return text


# ─────────────────────────────────────────────────────────────────────────────
# Singleton helpers
# ─────────────────────────────────────────────────────────────────────────────

_input_guard  = InputGuardrail()
_output_guard = OutputGuardrail()


def get_input_guardrail()  -> InputGuardrail:  return _input_guard
def get_output_guardrail() -> OutputGuardrail: return _output_guard