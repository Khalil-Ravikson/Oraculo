"""
API ChunkViz — endpoints para visualização interativa de chunking.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/hub/chunkviz", tags=["ChunkViz"])

TEMP_DIR = "/tmp/oraculo_cv"
ALLOWED  = {".pdf", ".docx", ".txt", ".md", ".csv", ".html", ".htm"}
MAX_MB   = 50
os.makedirs(TEMP_DIR, exist_ok=True)

PARSER_HINTS = {
    "auto":    "Detecta automaticamente pelo formato",
    "pymupdf": "⚡ Rápido para PDFs com texto nativo",
    "marker":  "🧠 ML para PDFs com tabelas (mais lento)",
    "docling": "📊 IBM Docling — layout-aware para DOCX/PDF",
    "txt":     "📝 Texto puro sem processamento especial",
}


def _auth(request: Request):
    from src.api.hub import _verificar_cookie
    p = _verificar_cookie(request)
    if not p:
        raise HTTPException(401, "Não autorizado")
    return p


def _load_meta(file_id: str) -> dict:
    path = os.path.join(TEMP_DIR, f"{file_id}.json")
    if not os.path.exists(path):
        raise HTTPException(404, f"Arquivo temporário não encontrado: {file_id}")
    with open(path) as f:
        return json.load(f)


def _save_meta(file_id: str, meta: dict):
    with open(os.path.join(TEMP_DIR, f"{file_id}.json"), "w") as f:
        json.dump(meta, f)


# ─────────────────────────────────────────────────────────────────────────────

@router.post("/upload")
async def upload(
    request: Request,
    file: UploadFile = File(...),
    parser: str = Form("auto"),
):
    _auth(request)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Formato '{ext}' não suportado. Aceitos: {', '.join(sorted(ALLOWED))}")

    content = await file.read()
    if len(content) > MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"Arquivo muito grande (máx {MAX_MB} MB)")

    file_id   = hashlib.md5(content[:512] + str(time.time()).encode()).hexdigest()[:16]
    file_path = os.path.join(TEMP_DIR, f"{file_id}{ext}")
    with open(file_path, "wb") as f:
        f.write(content)

    meta = {"file_id": file_id, "name": file.filename, "ext": ext,
            "size_kb": len(content)//1024, "path": file_path, "parser": parser}
    _save_meta(file_id, meta)

    try:
        pages, full_text = _extract_pages(file_path, ext, parser)
    except Exception as e:
        raise HTTPException(500, f"Erro ao extrair texto: {str(e)[:200]}")

    return {
        "file_id":    file_id,
        "name":       file.filename,
        "ext":        ext,
        "size_kb":    len(content) // 1024,
        "page_count": len(pages),
        "pages": [{"index": i, "preview": p[:80], "length": len(p)} for i, p in enumerate(pages)],
        "first_text": pages[0] if pages else full_text[:8000],
        "total_chars": len(full_text),
    }


@router.post("/page")
async def get_page(
    request: Request,
    file_id: str = Form(...),
    page: int = Form(0),
):
    _auth(request)
    meta = _load_meta(file_id)
    pages, full_text = _extract_pages(meta["path"], meta["ext"], meta["parser"])

    # page == -1 means full document
    if page == -1:
        return {"page": -1, "text": full_text, "total_pages": len(pages)}

    if page < 0 or page >= len(pages):
        raise HTTPException(400, f"Página {page} inexistente (total: {len(pages)})")

    return {"page": page, "text": pages[page], "total_pages": len(pages)}


@router.post("/extract-url")
async def extract_url(
    request: Request,
    url: str = Form(...),
):
    _auth(request)
    try:
        from src.infrastructure.scraping.implementations.generic_scraper import GenericHTTPScraper
        from src.infrastructure.scraping.base_scraper import ScrapeRequest

        result = await GenericHTTPScraper().scrape(ScrapeRequest(url=url, doc_type="web"))
        if not result.ok or not result.document:
            raise HTTPException(500, f"Scraping falhou: {result.error}")

        doc = result.document
        file_id   = hashlib.md5(url.encode()).hexdigest()[:16]
        file_path = os.path.join(TEMP_DIR, f"{file_id}.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(doc.content)

        _save_meta(file_id, {
            "file_id": file_id, "name": url, "ext": ".txt",
            "size_kb": len(doc.content)//1024, "path": file_path, "parser": "txt",
        })

        return {
            "file_id":    file_id,
            "title":      doc.title,
            "text":       doc.content[:10000],
            "total_chars": len(doc.content),
            "word_count": doc.word_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Erro no scraping: {str(e)[:200]}")


class SimReq(BaseModel):
    text:     str
    size:     int  = 400
    overlap:  int  = 60
    strategy: str  = "recursive"
    doc_type: str  = "geral"
    file_id:  Optional[str] = None


@router.post("/simulate")
async def simulate(request: Request, body: SimReq):
    _auth(request)
    if not body.text.strip():
        raise HTTPException(400, "Texto vazio")
    if len(body.text) > 150_000:
        raise HTTPException(400, "Texto muito longo (máx 150.000 chars)")

    try:
        from langchain_text_splitters import (
            RecursiveCharacterTextSplitter,
            MarkdownHeaderTextSplitter,
        )

        if body.strategy == "markdown":
            md_split = MarkdownHeaderTextSplitter(
                headers_to_split_on=[("#","h1"),("##","h2"),("###","h3")],
                strip_headers=False,
            )
            char_split = RecursiveCharacterTextSplitter(
                chunk_size=body.size, chunk_overlap=body.overlap,
                add_start_index=True,
            )
            docs = char_split.split_documents(md_split.split_text(body.text))
        else:
            docs = RecursiveCharacterTextSplitter(
                chunk_size=body.size, chunk_overlap=body.overlap,
                add_start_index=True,
            ).create_documents([body.text])

        chunks = []
        for i, doc in enumerate(docs):
            start = doc.metadata.get("start_index", 0)
            text  = doc.page_content
            chunks.append({
                "index": i, "text": text,
                "start_char": start, "end_char": start + len(text),
                "length": len(text),
                "preview": text[:80] + ("…" if len(text) > 80 else ""),
            })

        lens = [c["length"] for c in chunks]
        ovlp = sum(1 for i in range(1, len(chunks)) if chunks[i]["start_char"] < chunks[i-1]["end_char"])

        return {
            "chunks": chunks, "total": len(chunks),
            "total_chars": len(body.text),
            "avg_size": int(sum(lens) / max(len(lens), 1)),
            "min_size": min(lens) if lens else 0,
            "max_size": max(lens) if lens else 0,
            "overlap_regions": ovlp,
            "strategy_used": body.strategy,
        }
    except Exception as e:
        logger.exception("simulate error: %s", e)
        raise HTTPException(500, f"Erro no chunking: {str(e)[:200]}")


class IngestReq(BaseModel):
    file_id:  str
    size:     int  = 400
    overlap:  int  = 60
    strategy: str  = "recursive"
    doc_type: str  = "geral"
    label:    str  = ""
    source:   str  = ""
    parser:   str  = "auto"


@router.post("/ingest")
async def ingest(request: Request, body: IngestReq):
    _auth(request)
    meta   = _load_meta(body.file_id)
    source = body.source or meta.get("name", body.file_id)
    label  = body.label or os.path.splitext(source)[0].upper().replace("-"," ").replace("_"," ")

    try:
        from src.application.tasks.ingestion_tasks import processar_documento
        result = processar_documento.apply_async(
            args=[meta["path"]],
            kwargs={
                "strategy_params": {
                    "size":     body.size,     "overlap":  body.overlap,
                    "strategy": body.strategy, "doc_type": body.doc_type,
                    "label":    label,         "parser":   body.parser or meta.get("parser","auto"),
                },
                "chat_id": "",
            },
            queue="admin",
        )
        return {"ok": True, "task_id": result.id, "source": source}
    except Exception as e:
        raise HTTPException(500, f"Erro ao enfileirar ingestão: {str(e)[:200]}")


@router.get("/task/{task_id}")
async def task_status(request: Request, task_id: str):
    _auth(request)
    try:
        from src.infrastructure.celery_app import celery_app
        r = celery_app.AsyncResult(task_id)
        if r.state == "SUCCESS": return {"state":"SUCCESS","result": r.result}
        if r.state == "FAILURE": return {"state":"FAILURE","error":  str(r.info)}
        return {"state": r.state}
    except Exception as e:
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pages(file_path: str, ext: str, parser: str) -> tuple[list[str], str]:
    if ext == ".pdf":
        return _pdf_pages(file_path, parser)
    from src.rag.ingestion.parser_factory import ParserFactory
    try:
        p = ParserFactory.get(parser) if parser not in ("auto", "") else ParserFactory.auto(file_path)
    except ValueError:
        p = ParserFactory.auto(file_path)
    text = p.parse(file_path)
    # Split non-PDFs into logical "pages" (~3000 chars each)
    paras, pages, cur, cur_len = [s.strip() for s in text.split("\n\n") if s.strip()], [], [], 0
    for para in paras:
        if cur_len + len(para) > 3000 and cur:
            pages.append("\n\n".join(cur)); cur=[]; cur_len=0
        cur.append(para); cur_len += len(para)
    if cur: pages.append("\n\n".join(cur))
    return pages or [text], text


def _pdf_pages(file_path: str, parser: str) -> tuple[list[str], str]:
    if parser in ("auto", "pymupdf", ""):
        try:
            import fitz
            doc   = fitz.open(file_path)
            pages = [p.get_text("text").strip() for p in doc]
            doc.close()
            pages = [p for p in pages if p]
            return pages, "\n\n".join(pages)
        except ImportError:
            pass
    # Fallback: full-text parser, split by double-newline
    from src.rag.ingestion.parser_factory import ParserFactory
    try:
        p = ParserFactory.get(parser)
    except ValueError:
        p = ParserFactory.auto(file_path)
    text = p.parse(file_path)
    chunks = [s.strip() for s in text.split("\n\n\n") if s.strip()]
    return chunks or [text], text