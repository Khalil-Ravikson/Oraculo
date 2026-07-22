"""
src/infrastructure/scraping/implementations/dokuwiki/wikitext.py
-------------------------------------------------------------------
Conversor de markup DokuWiki (retornado por `do=export_raw`) para Markdown.

Por que não usar BeautifulSoup aqui:
  `do=export_raw` já devolve o texto-fonte da página (sem nav/sidebar/rodapé),
  então não há HTML para limpar — só sintaxe DokuWiki para traduzir 1:1 para
  Markdown (headers, tabelas, negrito/itálico, links, mídia/PDF).

SINTAXE DOKUWIKI RELEVANTE:
  ======Título====== → h1 (mais '=' = nível MAIS alto, oposto do Markdown)
  ^ Cab ^ Cab ^        → linha de cabeçalho de tabela
  | cel | cel |        → linha de dados de tabela
  **negrito**          → já é igual ao Markdown
  //itálico//          → converter para *itálico*
  [[pagina|Rótulo]]    → link interno (rótulo opcional)
  {{:arquivo.pdf|Rótulo}} → anexo de mídia (PDF, imagem, etc.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

_HEADER_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$")
_ITALIC_RE = re.compile(r"(?<!:)//(.+?)//")
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]")
_MEDIA_RE = re.compile(r"\{\{([^|}]+?)(?:\|([^}]*))?\}\}")

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg")


@dataclass
class ConvertedWiki:
    markdown: str
    internal_links: list[str] = field(default_factory=list)
    pdf_attachments: list[dict] = field(default_factory=list)


def _header_level(equals: str) -> int:
    # 6 '=' → h1, 2 '=' → h5 (DokuWiki é invertido em relação ao Markdown)
    return max(1, min(6, 7 - len(equals)))


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("^") or stripped.startswith("|")


def _table_row_to_markdown(line: str) -> list[str]:
    cells = re.split(r"(?<!\\)[|^]", line.strip())
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return [c.strip() for c in cells]


def _convert_table_block(block_lines: list[str]) -> list[str]:
    rows = [_table_row_to_markdown(line) for line in block_lines]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    md = []
    for i, row in enumerate(rows):
        row = row + [""] * (width - len(row))
        md.append("| " + " | ".join(row) + " |")
        if i == 0:
            md.append("|" + "|".join([" --- "] * width) + "|")
    return md


def _extract_media(
    text: str, media_url_builder: Callable[[str], str] | None = None,
) -> tuple[str, list[dict]]:
    """
    Substitui {{...}} por marcadores e retorna anexos PDF encontrados.

    PDFs não são baixados/parseados por padrão (ver decisão do projeto: os
    anexos do wiki CTIC até agora são slides de apresentação, com pouco texto
    extraível e conteúdo já coberto pela própria página) — em vez disso, fica
    só um link direto pro arquivo, para o usuário abrir manualmente. Se
    `media_url_builder` não for informado (ex.: chamada de teste sem URL base
    conhecida), cai no marcador sem link.
    """
    pdf_attachments: list[dict] = []

    def _sub(match: re.Match) -> str:
        media_ref = match.group(1).strip()
        label = (match.group(2) or "").strip()
        media_path = media_ref.split("?", 1)[0].lstrip(":")
        ext = media_path[media_path.rfind("."):].lower() if "." in media_path else ""
        display_label = label or media_path

        if ext == ".pdf":
            url = media_url_builder(media_path) if media_url_builder else None
            pdf_attachments.append({"media": media_path, "label": display_label, "url": url})
            return f"[Anexo PDF: {display_label}]({url})" if url else f"[anexo PDF: {display_label}]"
        if ext in _IMAGE_EXTS:
            return f"[imagem: {display_label}]"
        return f"[mídia: {display_label}]"

    return _MEDIA_RE.sub(_sub, text), pdf_attachments


def _normalize_page_id(target: str) -> str:
    """DokuWiki normaliza nomes de página: minúsculas, espaços viram '_'."""
    return target.strip().lower().replace(" ", "_")


def _extract_links(text: str) -> tuple[str, list[str]]:
    """Substitui [[...]] por link Markdown e retorna page_ids internos referenciados."""
    internal_links: list[str] = []

    def _sub(match: re.Match) -> str:
        target = match.group(1).strip()
        label = (match.group(2) or "").strip() or target

        if target.startswith(("http://", "https://")):
            return f"[{label}]({target})"

        page_id = _normalize_page_id(target.split("#", 1)[0])
        if page_id and page_id not in internal_links:
            internal_links.append(page_id)
        return f"[{label}]({page_id})"

    return _LINK_RE.sub(_sub, text), internal_links


def convert(
    raw_wikitext: str, media_url_builder: Callable[[str], str] | None = None,
) -> ConvertedWiki:
    """Converte wikitext DokuWiki (export_raw) para Markdown + metadados extraídos."""
    text, pdf_attachments = _extract_media(raw_wikitext, media_url_builder)
    text, internal_links = _extract_links(text)
    text = _ITALIC_RE.sub(r"*\1*", text)

    out_lines: list[str] = []
    table_buffer: list[str] = []

    for line in text.splitlines():
        if _is_table_line(line):
            table_buffer.append(line)
            continue
        if table_buffer:
            out_lines.extend(_convert_table_block(table_buffer))
            table_buffer = []

        header_match = _HEADER_RE.match(line.strip())
        if header_match:
            level = _header_level(header_match.group(1))
            out_lines.append(f"{'#' * level} {header_match.group(2).strip()}")
            continue

        out_lines.append(line)

    if table_buffer:
        out_lines.extend(_convert_table_block(table_buffer))

    markdown = "\n".join(out_lines)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()

    return ConvertedWiki(
        markdown=markdown,
        internal_links=internal_links,
        pdf_attachments=pdf_attachments,
    )
