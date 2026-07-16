"""
GmailService — Google Gmail API via service account.

SETUP (uma vez):
  1. Google Cloud Console → Service Account → download JSON
  2. Gmail → Settings → Delegate acesso à service account
  3. Adicionar ao .env:
     GOOGLE_SERVICE_ACCOUNT_JSON=/run/secrets/google_sa.json
     GMAIL_DELEGATED_USER=admin@uema.br
"""
from __future__ import annotations
import base64
import json
import logging
import os
from email.mime.text import MIMEText
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


class GmailService:
    def __init__(self):
        self._service: Any = None

    def _get_service(self):
        if self._service:
            return self._service
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
            delegated = os.getenv("GMAIL_DELEGATED_USER", "")

            if not sa_path or not delegated:
                raise ValueError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON e GMAIL_DELEGATED_USER obrigatórios"
                )

            creds = service_account.Credentials.from_service_account_file(
                sa_path,
                scopes=[
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.send",
                ],
            ).with_subject(delegated)

            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            return self._service
        except ImportError:
            raise ImportError("pip install google-api-python-client google-auth")

    async def search(self, query: str, max_results: int = 3) -> str:
        """Busca e-mails e retorna conteúdo para o RAG."""
        import asyncio
        return await asyncio.to_thread(self._search_sync, query, max_results)

    def _search_sync(self, query: str, max_results: int) -> str:
        try:
            svc = self._get_service()
            result = svc.users().messages().list(
                userId="me", q=query, maxResults=max_results
            ).execute()

            messages = result.get("messages", [])
            if not messages:
                return f"Nenhum e-mail encontrado para: '{query}'"

            blocos = []
            for msg_ref in messages[:max_results]:
                msg = svc.users().messages().get(
                    userId="me", id=msg_ref["id"], format="full"
                ).execute()

                headers = {
                    h["name"]: h["value"]
                    for h in msg["payload"].get("headers", [])
                }
                subject = headers.get("Subject", "Sem assunto")
                from_   = headers.get("From", "?")
                date    = headers.get("Date", "?")

                # Extrai body texto
                body = _extract_body(msg["payload"])

                blocos.append(
                    f"**De:** {from_}\n**Assunto:** {subject}\n"
                    f"**Data:** {date}\n\n{body[:800]}"
                )

            return "\n\n---\n\n".join(blocos)

        except Exception as e:
            logger.exception("GmailService.search falhou: %s", e)
            return f"Erro ao buscar e-mails: {str(e)[:100]}"

    async def send(self, destinatario: str, assunto: str, corpo: str) -> str:
        import asyncio
        return await asyncio.to_thread(self._send_sync, destinatario, assunto, corpo)

    def _send_sync(self, destinatario: str, assunto: str, corpo: str) -> str:
        try:
            svc = self._get_service()
            message = MIMEText(corpo)
            message["to"] = destinatario
            message["subject"] = assunto
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            svc.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            return f"✅ E-mail enviado para {destinatario}"
        except Exception as e:
            logger.exception("GmailService.send falhou: %s", e)
            return f"❌ Erro ao enviar: {str(e)[:100]}"


def _extract_body(payload: dict) -> str:
    """Extrai texto do payload Gmail (multipart ou simples)."""
    if payload.get("mimeType", "").startswith("text/plain"):
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []):
        result = _extract_body(part)
        if result:
            return result
    return ""


@lru_cache(maxsize=1)
def get_gmail_service() -> GmailService:
    return GmailService()