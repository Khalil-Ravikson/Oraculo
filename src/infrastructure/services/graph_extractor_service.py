"""
GraphExtractorService — Extrai entidades e triplas de texto via Gemini.
Salva em Postgres (tabelas entity/triple) para Graph RAG futuro.
"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_PROMPT = """Extraia entidades e relações do texto abaixo para construção de grafo de conhecimento.

Retorne APENAS JSON:
{
  "entities": [{"id": "slug_unico", "label": "PESSOA|LUGAR|EVENTO|SETOR|CURSO|DATA", "name": "Nome real"}],
  "triples":  [{"subject": "slug", "predicate": "RELACAO_EM_CAPS", "object": "slug_ou_valor"}]
}

Foque em: pessoas, setores, cursos, prazos, eventos acadêmicos, documentos.
Máximo 15 entidades e 20 triplas. Se não houver, retorne listas vazias.

<texto>
{texto}
</texto>"""


@dataclass
class GraphResult:
    ok: bool
    entities: list[dict] = field(default_factory=list)
    triples: list[dict] = field(default_factory=list)
    entities_saved: int = 0
    triples_saved: int = 0
    error: str = ""


class GraphExtractorService:

    async def extract_and_save(
        self, text: str, source: str, doc_type: str = "geral"
    ) -> GraphResult:
        # 1. Extrai via Gemini
        extracted = await self._extrair(text)
        if not extracted.ok:
            return extracted

        # 2. Salva no Postgres
        saved = await self._salvar(
            extracted.entities, extracted.triples, source, doc_type
        )
        extracted.entities_saved = saved["entities"]
        extracted.triples_saved  = saved["triples"]
        return extracted

    async def _extrair(self, text: str) -> GraphResult:
        try:
            from src.infrastructure.settings import settings
            import google.genai as genai
            from google.genai import types

            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=_PROMPT.format(texto=text[:3000]),
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=800,
                    response_mime_type="application/json",
                ),
            )
            data = json.loads(response.text or "{}")
            return GraphResult(
                ok=True,
                entities=data.get("entities", []),
                triples=data.get("triples", []),
            )
        except Exception as e:
            logger.warning("⚠️  [GRAPH] extração falhou: %s", e)
            return GraphResult(ok=False, error=str(e)[:200])

    async def _salvar(
        self,
        entities: list[dict],
        triples: list[dict],
        source: str,
        doc_type: str,
    ) -> dict:
        saved = {"entities": 0, "triples": 0}
        try:
            from src.infrastructure.database.session import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as db:
                for e in entities:
                    await db.execute(text("""
                        INSERT INTO kg_entities (entity_id, label, name, source, doc_type)
                        VALUES (:id, :label, :name, :source, :doc_type)
                        ON CONFLICT (entity_id) DO NOTHING
                    """), {"id": e["id"], "label": e.get("label",""), 
                           "name": e.get("name",""), "source": source, "doc_type": doc_type})
                    saved["entities"] += 1

                for t in triples:
                    await db.execute(text("""
                        INSERT INTO kg_triples (subject_id, predicate, object_val, source)
                        VALUES (:subj, :pred, :obj, :source)
                        ON CONFLICT DO NOTHING
                    """), {"subj": t["subject"], "pred": t["predicate"],
                           "obj": t["object"], "source": source})
                    saved["triples"] += 1

                await db.commit()
        except Exception as e:
            logger.warning("⚠️  [GRAPH] save falhou (ignorado): %s", e)
        return saved