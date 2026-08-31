"""Hub v2 — trava contra vazamento de jargão de backend na UI.

Regra dura do redesign (§12 de arquitetura_oraculo.md): nenhuma página do Hub
mostra ao usuário identificador de código, nome de tabela, nome de migration
ou arquivo `.py`. Termos técnicos só em `data-tech=` / tooltip / comentário.

Este teste renderiza cada template de `templates/hub/` e varre os `.js` de
página/componente atrás dos termos proibidos em posição visível.
"""
import glob
import os
import re

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# Termos que NUNCA devem aparecer como texto visível.
PROIBIDOS = [
    "route_registry", "graph_node_config", "agentes_catalogo", "llm_pricing",
    "dispatcher.py", "dispatcher_langgraph", "EvolutionAdapter", "llm_factory",
    "ParserFactory", "AudioService", "capabilities/registry.py", "NodeRegistry",
    "BaseNode", "_REGISTRY", "Configuration Layer", "Camada 1", "Camada 3",
    "adendo de nós", "mcp_lab/", "gateway.pipeworx", "SVS-VAMANA",
    "FEATURE_LANGGRAPH", "DEV_TEST_", "FT.CREATE", "FT.DROPINDEX",
    "settings.STT_PROVIDER", "settings.TTS_PROVIDER",
    # `topology_json` fica de fora: é nome de campo do payload da API
    # (graph-studio.js → POST /hub/graph-studio/save), não texto visível.
]
# Nomes de migration: "migration 010", "migration 016", etc.
_MIGRATION_RE = re.compile(r"migration[s]?\s+0\d\d", re.IGNORECASE)


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(os.path.join(_RAIZ, "templates")),
        autoescape=select_autoescape(["html"]),
    )


def _strip_permitido(html: str) -> str:
    """Remove o que pode legitimamente conter termo técnico: atributos
    data-tech, comentários HTML, e o bloco do glossário (que É o mapa)."""
    html = re.sub(r'data-tech="[^"]*"', "", html)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    return html


_TEMPLATES = sorted(
    "hub/" + os.path.basename(p)
    for p in glob.glob(os.path.join(_RAIZ, "templates", "hub", "*.html"))
    if os.path.basename(p) not in ("_glossario.html",)
)


@pytest.mark.parametrize("nome", _TEMPLATES)
def test_template_sem_jargao(nome):
    ctx = dict(request={}, username="admin", session_id="x", agent_name="sigaa",
               modelo="gemini-2.5-flash", dev_mode=False, erro="")
    html = _strip_permitido(_env().get_template(nome).render(**ctx))
    achados = [t for t in PROIBIDOS if t in html]
    assert not achados, f"{nome}: jargão visível {achados}"
    assert not _MIGRATION_RE.search(html), f"{nome}: cita nome de migration"


_JS_FILES = sorted(
    glob.glob(os.path.join(_RAIZ, "static", "js", "pages", "*.js"))
    + glob.glob(os.path.join(_RAIZ, "static", "js", "components", "*.js"))
)


@pytest.mark.parametrize("caminho", _JS_FILES, ids=lambda p: os.path.basename(p))
def test_js_sem_jargao(caminho):
    linhas_visiveis = []
    for linha in open(caminho, encoding="utf-8"):
        s = linha.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
            continue
        linhas_visiveis.append(linha)
    corpo = "".join(linhas_visiveis)
    # `owner:langgraph` etc. em chave de mapa / data-tech é permitido
    corpo = re.sub(r"'owner:[a-z_]+'|\"owner:[a-z_]+\"", "", corpo)
    corpo = re.sub(r'data-tech="[^"]*"|data-tech=\$\{[^}]*\}', "", corpo)
    achados = [t for t in PROIBIDOS if t in corpo]
    assert not achados, f"{os.path.basename(caminho)}: jargão {achados}"
