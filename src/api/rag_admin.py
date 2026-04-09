"""
src/api/rag_admin.py — Controle Administrativo do RAG
======================================================

ENDPOINTS:
  GET  /rag/sources              → lista todos os sources com status
  GET  /rag/sources/{source}     → detalhes de um source (chunks, score médio)
  POST /rag/sources              → adiciona novo source (upload ou URL)
  DELETE /rag/sources/{source}   → remove source do índice
  POST /rag/sources/{source}/reindex  → re-ingere um source
  POST /rag/rebuild              → re-ingere tudo
  GET  /rag/search               → busca manual no índice (debug)
  GET  /rag/chunks/{source}      → lista chunks de um source
  DELETE /rag/chunks/{chunk_id}  → remove chunk específico
  GET  /rag/allowed-sources      → sources permitidos para cada role
  PUT  /rag/allowed-sources      → atualiza permissões de sources por role
  POST /rag/cache/flush          → limpa cache semântico
  GET  /rag/stats                → métricas gerais do índice

CONTROLE DE ACESSO POR SOURCE:
  O admin pode definir quais sources cada role pode consultar.
  Ex: "edital_paes_2026.pdf" → [guest, student, admin]
       "contrato_docentes.pdf" → [professor, admin]

  Isso é armazenado no Redis: rag:permissions:{source} → JSON list of roles
  O retriever verifica antes de incluir chunks na resposta.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from src.api.middleware.auth_middleware import require_admin_jwt, TokenPayload
from src.infrastructure.settings import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rag", tags=["RAG Admin"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class SourcePermissions(BaseModel):
    source: str
    allowed_roles: list[str]  # ["guest", "student", "professor", "admin"]
    description: str = ""


class SearchRequest(BaseModel):
    query: str
    source_filter: Optional[str] = None
    doc_type: Optional[str] = None
    k: int = 5


# ─────────────────────────────────────────────────────────────────────────────
# Permissões de Sources
# ─────────────────────────────────────────────────────────────────────────────

def _get_source_permissions(source: str) -> list[str]:
    """Retorna roles permitidos para um source. Padrão: todos."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        raw = r.get(f"rag:permissions:{source}")
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            return data.get("roles", ["guest", "student", "professor", "admin"])
    except Exception:
        pass
    return ["guest", "student", "professor", "admin"]


def _set_source_permissions(source: str, roles: list[str], description: str = "") -> None:
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        r.set(
            f"rag:permissions:{source}",
            json.dumps({"roles": roles, "description": description}, ensure_ascii=False),
        )
    except Exception as e:
        logger.error("❌ set_source_permissions: %s", e)


def check_source_access(source: str, role: str) -> bool:
    """Verifica se um role pode consultar um source. Usado pelo retriever."""
    allowed = _get_source_permissions(source)
    return role in allowed


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def rag_stats(payload: TokenPayload = Depends(require_admin_jwt)):
    """Métricas gerais do índice RAG."""
    try:
        from src.infrastructure.redis_client import get_redis, PREFIX_CHUNKS, IDX_CHUNKS
        r = get_redis()

        _, all_keys = r.scan(0, match=f"{PREFIX_CHUNKS}*", count=2000)
        chunks_por_source: dict[str, int] = {}
        for key in all_keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            parts = key_str.split(":", 3)
            if len(parts) >= 3:
                src = parts[2]
                chunks_por_source[src] = chunks_por_source.get(src, 0) + 1

        # Info do índice RediSearch
        try:
            info = r.ft(IDX_CHUNKS).info()
            idx_info = {
                "num_docs":  info.get("num_docs", 0),
                "num_terms": info.get("num_terms", 0),
            }
        except Exception:
            idx_info = {}

        # Cache semântico
        from src.infrastructure.semantic_cache import cache_stats
        cache = cache_stats()

        return {
            "total_chunks":   sum(chunks_por_source.values()),
            "sources":        chunks_por_source,
            "index":          idx_info,
            "semantic_cache": cache,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/sources")
async def list_sources(payload: TokenPayload = Depends(require_admin_jwt)):
    """Lista todos os sources com status e permissões."""
    try:
        from src.rag.ingestion import PDF_CONFIG, _ler_manifesto
        from src.infrastructure.redis_client import get_redis, PREFIX_CHUNKS

        r = get_redis()
        _, all_keys = r.scan(0, match=f"{PREFIX_CHUNKS}*", count=2000)
        chunks_por_source: dict[str, int] = {}
        for key in all_keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            parts = key_str.split(":", 3)
            if len(parts) >= 3:
                src = parts[2]
                chunks_por_source[src] = chunks_por_source.get(src, 0) + 1

        manifesto = _ler_manifesto()
        sources = []
        for nome, cfg in PDF_CONFIG.items():
            sources.append({
                "nome":        nome,
                "titulo":      cfg.get("titulo", nome),
                "doc_type":    cfg.get("doc_type", "geral"),
                "chunks":      chunks_por_source.get(nome, 0),
                "indexado":    nome in chunks_por_source,
                "hash":        manifesto.get(nome, {}).get("hash", "")[:8],
                "permissions": _get_source_permissions(nome),
            })

        # Sources que estão no Redis mas não no PDF_CONFIG (ingeridos via upload)
        extras = set(chunks_por_source.keys()) - set(PDF_CONFIG.keys())
        for src in extras:
            sources.append({
                "nome":        src,
                "titulo":      src,
                "doc_type":    "custom",
                "chunks":      chunks_por_source[src],
                "indexado":    True,
                "hash":        "",
                "permissions": _get_source_permissions(src),
            })

        return {"sources": sources, "total": len(sources)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/sources/{source}")
async def delete_source(
    source: str,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Remove todos os chunks de um source do índice Redis."""
    from src.infrastructure.redis_client import deletar_chunks_por_source
    from src.infrastructure.observability.metrics import update_rag_chunks

    n = deletar_chunks_por_source(source)
    update_rag_chunks()
    return {"ok": True, "deleted_chunks": n, "source": source}


@router.post("/sources/{source}/reindex")
async def reindex_source(
    source: str,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Re-ingere um source específico."""
    from src.infrastructure.settings import settings as app_settings
    from src.rag.ingestion import Ingestor
    from src.infrastructure.observability.metrics import update_rag_chunks
    import asyncio

    caminho = os.path.join(app_settings.DATA_DIR, source)
    if not os.path.exists(caminho):
        raise HTTPException(404, f"Arquivo não encontrado: {caminho}")

    t0 = time.monotonic()
    ingestor = Ingestor()
    chunks = await asyncio.to_thread(ingestor._ingerir_ficheiro, caminho)
    ms = int((time.monotonic() - t0) * 1000)
    update_rag_chunks()

    return {"ok": True, "source": source, "chunks": chunks, "ms": ms}


@router.post("/rebuild")
async def rebuild_all(payload: TokenPayload = Depends(require_admin_jwt)):
    """Re-ingere TODOS os sources. Operação pesada."""
    from src.rag.ingestion import Ingestor
    from src.infrastructure.observability.metrics import update_rag_chunks
    import asyncio

    t0 = time.monotonic()
    ingestor = Ingestor()
    await asyncio.to_thread(ingestor.ingerir_tudo)
    ms = int((time.monotonic() - t0) * 1000)
    update_rag_chunks()

    return {"ok": True, "ms": ms}


@router.post("/upload")
async def upload_source(
    file: UploadFile = File(...),
    doc_type: str = Form("geral"),
    allowed_roles: str = Form("guest,student,admin"),
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """
    Faz upload de um documento e ingere imediatamente.
    allowed_roles: roles separados por vírgula que podem consultar este source.
    """
    from src.rag.document_validator import validar_documento, formatar_resultado_para_whatsapp
    from src.rag.ingestion import Ingestor, PDF_CONFIG
    from src.infrastructure.observability.metrics import update_rag_chunks
    import asyncio

    uploads_dir = os.path.join(settings.DATA_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    caminho = os.path.join(uploads_dir, file.filename)
    contents = await file.read()
    with open(caminho, "wb") as f:
        f.write(contents)

    resultado = validar_documento(caminho, file.content_type, file.filename)
    if not resultado.valido:
        os.remove(caminho)
        raise HTTPException(400, resultado.motivo_rejeicao)

    # Auto-config e ingestão
    config = resultado.config_sugerido.copy()
    config["doc_type"] = doc_type
    PDF_CONFIG[file.filename] = config

    t0 = time.monotonic()
    ingestor = Ingestor()
    chunks = await asyncio.to_thread(ingestor._ingerir_ficheiro, caminho)
    ms = int((time.monotonic() - t0) * 1000)

    # Define permissões
    roles = [r.strip() for r in allowed_roles.split(",")]
    _set_source_permissions(file.filename, roles, f"Enviado via upload por {payload.sub}")

    update_rag_chunks()

    return {
        "ok":      True,
        "source":  file.filename,
        "chunks":  chunks,
        "ms":      ms,
        "roles":   roles,
        "config":  config,
    }


@router.post("/search")
async def search_rag(
    req: SearchRequest,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Busca manual no índice RAG (ferramenta de debug)."""
    try:
        from src.rag.embeddings import get_embeddings
        from src.infrastructure.redis_client import busca_hibrida
        import asyncio

        emb = get_embeddings()
        vetor = await asyncio.to_thread(emb.embed_query, req.query)
        resultados = busca_hibrida(
            query_text=req.query,
            query_embedding=vetor,
            source_filter=req.source_filter,
            k_vector=req.k,
            k_text=req.k,
        )
        return {
            "query":    req.query,
            "results":  resultados[:req.k],
            "total":    len(resultados),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/chunks/{source}")
async def list_chunks(
    source: str,
    limit: int = 20,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Lista chunks de um source específico."""
    try:
        from src.infrastructure.redis_client import get_redis, PREFIX_CHUNKS

        r = get_redis()
        pattern = f"{PREFIX_CHUNKS}{source}:*"
        _, keys = r.scan(0, match=pattern, count=500)
        keys = keys[:limit]

        chunks = []
        for key in keys:
            try:
                doc = r.json().get(key, "$")
                if doc:
                    item = doc[0] if isinstance(doc, list) else doc
                    chunks.append({
                        "key":         key.decode() if isinstance(key, bytes) else key,
                        "chunk_index": item.get("chunk_index", 0),
                        "content":     (item.get("content") or "")[:200],
                        "doc_type":    item.get("doc_type", ""),
                    })
            except Exception:
                pass

        chunks.sort(key=lambda x: x["chunk_index"])
        return {"source": source, "chunks": chunks, "total_shown": len(chunks)}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/allowed-sources")
async def get_allowed_sources(payload: TokenPayload = Depends(require_admin_jwt)):
    """Retorna mapa de permissões de todos os sources."""
    try:
        from src.infrastructure.redis_client import get_redis_text
        r = get_redis_text()
        _, keys = r.scan(0, match="rag:permissions:*", count=200)

        permissions = {}
        for key in keys:
            key_str = key if isinstance(key, str) else key.decode()
            source  = key_str.replace("rag:permissions:", "")
            raw     = r.get(key_str)
            if raw:
                data = json.loads(raw if isinstance(raw, str) else raw.decode())
                permissions[source] = data

        return {"permissions": permissions}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.put("/allowed-sources")
async def update_source_permissions(
    perms: SourcePermissions,
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Atualiza permissões de acesso de um source."""
    valid_roles = {"guest", "student", "professor", "servidor", "coordenador", "admin"}
    invalid = set(perms.allowed_roles) - valid_roles
    if invalid:
        raise HTTPException(400, f"Roles inválidos: {invalid}")

    _set_source_permissions(perms.source, perms.allowed_roles, perms.description)
    return {"ok": True, "source": perms.source, "roles": perms.allowed_roles}


@router.post("/cache/flush")
async def flush_cache(
    rota: str = "",
    payload: TokenPayload = Depends(require_admin_jwt),
):
    """Limpa cache semântico (todo ou por rota)."""
    from src.infrastructure.semantic_cache import invalidar_cache_rota
    from src.domain.entities import Rota

    if rota:
        n = invalidar_cache_rota(rota.upper())
    else:
        n = sum(invalidar_cache_rota(r.value) for r in Rota)

    return {"ok": True, "deleted": n, "rota": rota or "all"}