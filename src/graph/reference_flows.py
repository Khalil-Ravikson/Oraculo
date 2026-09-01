"""
src/graph/reference_flows.py — diagramas do roteamento que já existe (só leitura)
================================================================================
Dados puros, sem I/O. Descrevem, como grafo, o caminho real que uma mensagem
percorre hoje — para o operador enxergar no Graph Studio o que o código faz.

NÃO são topologias executáveis: `classificar`, `buscar_rag`, `planejar` etc. não
são `BaseNode` reais (são etapas de `supervisor.py` / `dispatcher_langgraph.py`).
São pseudo-nós de documentação. `tests/unit/graph/test_reference_flows.py` trava
estes diagramas contra `route_registry` para não derivarem do código.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class FluxoReferencia:
    slug: str
    nome: str
    descricao: str
    fonte: str                       # onde no código isto vive
    nodes: list[dict] = field(default_factory=list)   # {id, label, x, y}
    edges: list[dict] = field(default_factory=list)   # {de, para, rotulo?}
    rota: str | None = None          # rota de `route_registry` que este fluxo detalha


def _n(id_, label, x, y):
    return {"id": id_, "label": label, "x": x, "y": y}


def _e(de, para, rotulo=""):
    return {"de": de, "para": para, "rotulo": rotulo}


FLUXOS: list[FluxoReferencia] = [
    FluxoReferencia(
        slug="classificacao",
        nome="Como o assunto da mensagem é descoberto",
        descricao=(
            "Camadas baratas primeiro, modelo de linguagem só no fim. A primeira "
            "que casa decide a rota."
        ),
        fonte="src/router/supervisor.py::_rotear (L1–L5)",
        nodes=[
            _n("msg", "Mensagem recebida", 0, 120),
            _n("l1", "1 · Regex rápido", 200, 40),
            _n("l2", "2 · Heurística", 200, 120),
            _n("l3", "3 · Regex dinâmico (gatilhos)", 200, 200),
            _n("l4", "4 · Busca por significado", 420, 120),
            _n("l5", "5 · Modelo de linguagem", 640, 120),
            _n("rota", "Rota escolhida", 860, 120),
        ],
        edges=[
            _e("msg", "l1"), _e("msg", "l2"), _e("msg", "l3"),
            _e("l1", "rota", "casou"), _e("l2", "rota", "casou"), _e("l3", "rota", "casou"),
            _e("l3", "l4", "não casou"), _e("l4", "rota", "casou"),
            _e("l4", "l5", "não casou"), _e("l5", "rota"),
        ],
    ),
    FluxoReferencia(
        slug="rota_geral_rag",
        rota="GERAL",
        nome="Rota GERAL — pergunta respondida com documentos",
        descricao="O que acontece depois que o assunto é GERAL/CALENDARIO/EDITAL/…",
        fonte="src/infrastructure/route_registry.py::_DEFAULTS['GERAL']",
        nodes=[
            _n("rota", "Rota GERAL", 0, 80),
            _n("cfg", "Mapa de rotas (agente, cache, k)", 220, 80),
            _n("rag", "Busca nos documentos", 470, 80),
            _n("plan", "Planejador", 690, 80),
            _n("llm", "Modelo de linguagem", 890, 80),
            _n("out", "Resposta ao usuário", 1110, 80),
        ],
        edges=[
            _e("rota", "cfg"), _e("cfg", "rag", "k trechos"),
            _e("rag", "plan"), _e("plan", "llm"), _e("llm", "out"),
        ],
    ),
    FluxoReferencia(
        slug="rota_sigaa",
        rota="SIGAA",
        nome="Rota SIGAA — consulta ao portal do aluno",
        descricao="Assunto identificado como SIGAA (notas, turmas, biblioteca…).",
        fonte="src/infrastructure/route_registry.py::_DEFAULTS['SIGAA']",
        nodes=[
            _n("rota", "Rota SIGAA", 0, 80),
            _n("sigaa", "Integração SIGAA", 240, 80),
            _n("llm", "Modelo de linguagem", 470, 80),
            _n("out", "Resposta ao usuário", 690, 80),
        ],
        edges=[_e("rota", "sigaa"), _e("sigaa", "llm"), _e("llm", "out")],
    ),
    FluxoReferencia(
        slug="rota_ticket_abertura",
        rota="TICKET_ABERTURA",
        nome="Rota TICKET_ABERTURA — abrir chamado",
        descricao="Usuário pede para abrir um chamado de suporte.",
        fonte="src/infrastructure/route_registry.py::_DEFAULTS['TICKET_ABERTURA']",
        nodes=[
            _n("rota", "Rota TICKET_ABERTURA", 0, 80),
            _n("ticket", "Abertura de chamado", 260, 80),
            _n("out", "Confirmação ao usuário", 500, 80),
        ],
        edges=[_e("rota", "ticket"), _e("ticket", "out")],
    ),
    FluxoReferencia(
        slug="rota_media_download",
        rota="MEDIA_DOWNLOAD",
        nome="Rota MEDIA_DOWNLOAD — baixar vídeo/mídia",
        descricao="Link de YouTube/Instagram ou pedido explícito de download.",
        fonte="src/router/supervisor.py::_dag_hint_para_rota + _DEFAULTS['MEDIA_DOWNLOAD']",
        nodes=[
            _n("rota", "Rota MEDIA_DOWNLOAD", 0, 80),
            _n("dl", "Download de mídia", 240, 80),
            _n("send", "Envio pelo canal", 460, 80),
        ],
        edges=[_e("rota", "dl"), _e("dl", "send")],
    ),
    FluxoReferencia(
        slug="rota_greeting",
        rota="GREETING",
        nome="Rota GREETING — saudação",
        descricao="'oi', 'bom dia' — resposta curta, sem modelo de linguagem.",
        fonte="src/infrastructure/route_registry.py::_DEFAULTS['GREETING']",
        nodes=[
            _n("rota", "Rota GREETING", 0, 80),
            _n("greet", "Resposta de saudação", 240, 80),
            _n("out", "Resposta ao usuário", 480, 80),
        ],
        edges=[_e("rota", "greet"), _e("greet", "out")],
    ),
]


def como_json() -> list[dict]:
    return [asdict(f) for f in FLUXOS]
