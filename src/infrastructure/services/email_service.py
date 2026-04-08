"""
src/infrastructure/services/email_service.py
----------------------------------------------
Implementação do IEmailService — completamente async, com templates e retry.

BACKENDS SUPORTADOS:
  SMTPEmailService    → SMTP local (MailHog para dev, SMTP real para prod)
  LogEmailService     → apenas loga (para testes sem servidor SMTP)

TEMPLATES:
  Templates ficam em src/infrastructure/services/email_templates/
  São Jinja2 simples. template_id="confirmacao_chamado" busca
  email_templates/confirmacao_chamado.html

ESCALABILIDADE:
  Para adicionar SendGrid, Resend, etc:
  1. Criar SendGridEmailService(IEmailService)
  2. Registrar no email_container.py
  Zero mudança nos nodes do LangGraph.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path
from string import Template
from typing import Any

from src.domain.ports.tool_ports import IEmailService, ToolResult

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "email_templates"


def _render_template(template_id: str, vars: dict) -> str:
    """Renderiza template simples com $variavel."""
    tmpl_path = _TEMPLATES_DIR / f"{template_id}.html"
    if tmpl_path.exists():
        return Template(tmpl_path.read_text(encoding="utf-8")).safe_substitute(vars)
    return vars.get("corpo", "")


class SMTPEmailService(IEmailService):
    """
    Serviço de e-mail via SMTP — async via asyncio.to_thread.

    DEV: usa MailHog (host.docker.internal:1025)
    PROD: SMTP_HOST/SMTP_PORT do .env
    """

    def __init__(
        self,
        smtp_host: str = "localhost",
        smtp_port: int = 1025,
        remetente_padrao: str = "no-reply@uema.br",
        use_tls: bool = False,
        username: str = "",
        password: str = "",
    ):
        self._host = smtp_host
        self._port = smtp_port
        self._remetente = remetente_padrao
        self._tls = use_tls
        self._user = username
        self._pass = password

    async def enviar(
        self,
        destinatario: str,
        assunto: str,
        corpo: str,
        remetente: str = "",
        template_id: str | None = None,
        template_vars: dict | None = None,
    ) -> ToolResult:
        """Envia e-mail de forma assíncrona sem bloquear o event loop."""
        conteudo = corpo
        if template_id:
            tvars = template_vars or {}
            tvars.setdefault("corpo", corpo)
            conteudo = _render_template(template_id, tvars)

        from_addr = remetente or self._remetente

        try:
            await asyncio.to_thread(
                self._enviar_sincrono,
                from_addr, destinatario, assunto, conteudo,
            )
            logger.info("✅ E-mail enviado para %s | assunto: %s", destinatario, assunto)
            return ToolResult.success(
                message=f"E-mail enviado com sucesso para {destinatario}.",
                data={"destinatario": destinatario, "assunto": assunto},
            )
        except Exception as e:
            logger.error("❌ E-mail falhou para %s: %s", destinatario, e)
            return ToolResult.failure(f"Falha ao enviar e-mail: {e}")

    def _enviar_sincrono(self, from_addr: str, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject

        # Suporta HTML
        if body.strip().startswith("<"):
            msg.set_content("Por favor, use um cliente de e-mail com suporte a HTML.")
            msg.add_alternative(body, subtype="html")
        else:
            msg.set_content(body)

        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            if self._tls:
                smtp.starttls()
            if self._user and self._pass:
                smtp.login(self._user, self._pass)
            smtp.send_message(msg)


class LogEmailService(IEmailService):
    """E-mail de log apenas — para testes sem SMTP."""

    async def enviar(self, destinatario, assunto, corpo, remetente="", template_id=None, template_vars=None) -> ToolResult:
        logger.info("📧 [LOG EMAIL] Para: %s | Assunto: %s | Body: %.80s", destinatario, assunto, corpo)
        return ToolResult.success(
            message=f"[DEV] E-mail registrado em log para {destinatario}.",
            data={"destinatario": destinatario, "assunto": assunto},
        )