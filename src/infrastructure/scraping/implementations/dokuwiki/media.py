"""
src/infrastructure/scraping/implementations/dokuwiki/media.py
------------------------------------------------------------------
Monta a URL de download de um anexo de mídia do DokuWiki (detectado em
wikitext.py como `{{:arquivo.pdf|Rótulo}}`).

DECISÃO DO PROJETO: os PDFs anexados ao wiki CTIC (até agora) são slides de
apresentação — pouco texto extraível, e o conteúdo procedural relevante já
está na própria página wiki. Por isso este módulo só constrói o link de
download; não baixa nem parseia o PDF. Se um caso de uso real precisar do
conteúdo de um PDF específico (manual denso em texto, não slide), reavaliar.
"""
from __future__ import annotations

from urllib.parse import urljoin


def build_media_url(wiki_base_url: str, media_path: str) -> str:
    """
    Monta a URL de download de um anexo DokuWiki.
    Ex.: base=https://ctic.uema.br/wiki/doku.php, media=modulos-sipac:apresentacao.pdf
      → https://ctic.uema.br/wiki/lib/exe/fetch.php?media=modulos-sipac:apresentacao.pdf
    """
    fetch_url = urljoin(wiki_base_url, "lib/exe/fetch.php")
    media_encoded = media_path.replace(" ", "_")
    return f"{fetch_url}?media={media_encoded}"
