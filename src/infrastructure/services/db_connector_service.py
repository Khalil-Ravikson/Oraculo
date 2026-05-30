"""
DBConnectorService — Consulta APIs externas de sistemas acadêmicos.
Nunca expõe credenciais ao LLM. Retorna dados sanitizados.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class DBQueryResult:
    ok: bool
    data: dict = field(default_factory=dict)
    source: str = ""
    error: str = ""

    def to_context_str(self) -> str:
        """Formata para injeção no prompt do LLM."""
        if not self.ok:
            return f"[{self.source}] Dados indisponíveis."
        lines = [f"[{self.source}]"]
        for k, v in self.data.items():
            lines.append(f"  {k}: {v}")
        return "\n".join(lines)


class DBConnectorService:
    """
    Conector genérico para APIs de sistemas acadêmicos (SIGAA, SIG, etc).
    Configure endpoints no .env:
      SIGAA_BASE_URL, SIGAA_API_KEY
    """

    _TIMEOUT = httpx.Timeout(10.0, connect=5.0)

    async def buscar_dados_aluno(self, matricula: str) -> DBQueryResult:
        """Busca dados acadêmicos do aluno pelo número de matrícula."""
        base_url = getattr(settings, "SIGAA_BASE_URL", "")
        api_key  = getattr(settings, "SIGAA_API_KEY",  "")

        if not base_url or not api_key:
            logger.debug("SIGAA não configurado — retornando vazio")
            return DBQueryResult(ok=False, source="SIGAA",
                                 error="Integração não configurada")
        try:
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.get(
                    f"{base_url}/aluno/{matricula}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                raw = resp.json()

            # Sanitiza — nunca retorna CPF, senha, dados sensíveis
            safe = {
                "nome":       raw.get("nome", ""),
                "curso":      raw.get("curso", ""),
                "periodo":    raw.get("periodo_atual", ""),
                "cr":         raw.get("cr", ""),
                "situacao":   raw.get("situacao", ""),
                "centro":     raw.get("centro", ""),
            }
            return DBQueryResult(ok=True, data=safe, source="SIGAA")

        except httpx.HTTPStatusError as e:
            return DBQueryResult(ok=False, source="SIGAA",
                                 error=f"HTTP {e.response.status_code}")
        except Exception as e:
            logger.warning("⚠️  [DBCONN] buscar_dados_aluno: %s", e)
            return DBQueryResult(ok=False, source="SIGAA", error=str(e)[:100])

    async def buscar_notas(self, matricula: str, semestre: str = "") -> DBQueryResult:
        """Busca histórico de notas."""
        base_url = getattr(settings, "SIGAA_BASE_URL", "")
        api_key  = getattr(settings, "SIGAA_API_KEY",  "")
        if not base_url:
            return DBQueryResult(ok=False, source="SIGAA", error="Não configurado")
        try:
            params = {"matricula": matricula}
            if semestre:
                params["semestre"] = semestre
            async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                resp = await client.get(
                    f"{base_url}/notas",
                    params=params,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                return DBQueryResult(ok=True, data=resp.json(), source="SIGAA")
        except Exception as e:
            return DBQueryResult(ok=False, source="SIGAA", error=str(e)[:100])