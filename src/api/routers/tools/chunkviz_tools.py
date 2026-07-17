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
router = APIRouter(tags=["ChunkViz"])

TEMP_DIR = "./dados/tmp"
ALLOWED  = {".pdf", ".docx", ".txt", ".md", ".csv", ".html", ".htm"}
MAX_MB   = 50
os.makedirs(TEMP_DIR, exist_ok=True)

PARSER_HINTS = {
    "auto":       "Detecta automaticamente pelo tipo de arquivo",
    "llamaparse": "☁️ Cloud ML — Excelente para editais e tabelas (Requer token no .env)", # NOVO
    "pymupdf":    "⚡ Rápido para PDFs com texto nativo — NÃO funciona em PDFs escaneados",
    "marker":     "🧠 ML para PDFs escaneados/complexos — mais lento, requer mais RAM",
    "docling":    "📊 IBM Docling — preserva layout de tabelas e hierarquia",
    "csv":        "📋 Transforma cada linha CSV em frases semânticas ricas",
    "txt":        "📝 Leitura direta de texto puro",
}


def _auth(request: Request):
    from src.api.routers.web.hub import _verificar_cookie
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





class SimReq(BaseModel):
    text:     str
    size:     int  = 400
    overlap:  int  = 60
    strategy: str  = "recursive"
    doc_type: str  = "geral"
    file_id:  Optional[str] = None



class IngestReq(BaseModel):
    file_id:  str
    size:     int  = 400
    overlap:  int  = 60
    strategy: str  = "recursive"
    doc_type: str  = "geral"
    label:    str  = ""
    source:   str  = ""
    parser:   str  = "auto"



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


"""
Ferramentas Internas do ChunkViz.
Apenas lógica de negócio, extração e chunking. Sem rotas web (FastAPI).
"""


# ─────────────────────────────────────────────────────────────────────────────
# 1. Helpers de Salvamento Temporário (Os que o hub.py estava pedindo!)
# ─────────────────────────────────────────────────────────────────────────────

def save_temp_file(filename: str, content: bytes, parser: str) -> dict:
    """Salva o arquivo físico e gera os metadados JSON."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED:
        raise ValueError(f"Formato '{ext}' não suportado. Aceitos: {', '.join(sorted(ALLOWED))}")

    if len(content) > MAX_MB * 1024 * 1024:
        raise ValueError(f"Arquivo muito grande (máx {MAX_MB} MB)")

    file_id   = hashlib.md5(content[:512] + str(time.time()).encode()).hexdigest()[:16]
    file_path = os.path.join(TEMP_DIR, f"{file_id}{ext}")
    
    with open(file_path, "wb") as f:
        f.write(content)

    meta = {
        "file_id": file_id, "name": filename, "ext": ext,
        "size_kb": len(content)//1024, "path": file_path, "parser": parser
    }
    
    with open(os.path.join(TEMP_DIR, f"{file_id}.json"), "w") as f:
        json.dump(meta, f)
        
    return meta

def load_temp_meta(file_id: str) -> dict:
    """Carrega os metadados salvos pelo save_temp_file."""
    path = os.path.join(TEMP_DIR, f"{file_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Arquivo temporário não encontrado: {file_id}")
    with open(path) as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Lógica de Extração e Chunking
# ─────────────────────────────────────────────────────────────────────────────

def extract_document_pages(file_path: str, ext: str, parser: str) -> tuple[list[str], str]:
    if ext == ".pdf":
        return _pdf_pages(file_path, parser)
    from src.rag.ingestion.parser_factory import ParserFactory
    try:
        p = ParserFactory.get(parser) if parser not in ("auto", "") else ParserFactory.auto(file_path)
    except ValueError:
        p = ParserFactory.auto(file_path)
        
    text = p.parse(file_path)
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
    from src.rag.ingestion.parser_factory import ParserFactory
    try:
        p = ParserFactory.get(parser)
    except ValueError:
        p = ParserFactory.auto(file_path)
    text = p.parse(file_path)
    chunks = [s.strip() for s in text.split("\n\n\n") if s.strip()]
    return chunks or [text], text

def simulate_chunks_logic(text: str, size: int, overlap: int, strategy: str):
    from langchain_text_splitters import (
        RecursiveCharacterTextSplitter,
        MarkdownHeaderTextSplitter,
    )

    if strategy == "markdown":
        md_split = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#","h1"),("##","h2"),("###","h3")],
            strip_headers=False,
        )
        char_split = RecursiveCharacterTextSplitter(
            chunk_size=size, chunk_overlap=overlap,
            add_start_index=True,
        )
        docs = char_split.split_documents(md_split.split_text(text))
    else:
        docs = RecursiveCharacterTextSplitter(
            chunk_size=size, chunk_overlap=overlap,
            add_start_index=True,
        ).create_documents([text])

    chunks = []
    for i, doc in enumerate(docs):
        start = doc.metadata.get("start_index", 0)
        chunk_text  = doc.page_content
        chunks.append({
            "index": i, "text": chunk_text,
            "start_char": start, "end_char": start + len(chunk_text),
            "length": len(chunk_text),
            "preview": chunk_text[:80] + ("…" if len(chunk_text) > 80 else ""),
        })

    lens = [c["length"] for c in chunks]
    ovlp = sum(1 for i in range(1, len(chunks)) if chunks[i]["start_char"] < chunks[i-1]["end_char"])

    return {
        "chunks": chunks, "total": len(chunks),
        "total_chars": len(text),
        "avg_size": int(sum(lens) / max(len(lens), 1)),
        "min_size": min(lens) if lens else 0,
        "max_size": max(lens) if lens else 0,
        "overlap_regions": ovlp,
        "strategy_used": strategy,
    }