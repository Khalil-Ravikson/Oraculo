"""
src/application/orchestration/spec_editor.py
===========================================
Edições de alto nível na `GraphSpec` — o que a GUI do Graph Studio faz por
baixo dos panos (ADR 0008 Fase 4).

Uma "rota nova" no grafo = 1 nó terminal + a aresta `classify --route_value-->
nó` + a aresta `nó --> __end__`. O `route_value` é o `entrypoint_node` da
rota no `route_registry` (o `by_state_route` devolve `state.route`, que é
justamente esse valor). Por isso criar um fluxo novo mexe nos DOIS registros
— este módulo cuida só da parte `graph_spec`; o endpoint coordena com o
`route_registry`.
"""
from __future__ import annotations

import re

from src.application.orchestration.spec import GraphSpec

_RE_NODE_ID = re.compile(r"^[a-z][a-z0-9_]{1,38}$")

# Só estes tipos fazem sentido como rota terminal nova pela GUI (os `fixo`
# são esqueleto do pipeline; os funis são compostos e travados).
TIPOS_ADICIONAVEIS = ("rag", "check_status", "greeting", "media_download", "sigaa", "human_handoff")


class EdicaoInvalida(ValueError):
    pass


def node_id_de_rota(rota: str) -> str:
    """`"FAQ"` → `"faq"`; `"NOTAS SIGAA"` → `"notas_sigaa"`. Sufixo `_flow`
    só se colidir com um id reservado do core."""
    base = re.sub(r"[^a-z0-9]+", "_", rota.strip().lower()).strip("_")[:38] or "rota"
    if not _RE_NODE_ID.match(base):
        base = "rota_" + base
    return base


def adicionar_rota(spec: GraphSpec, *, node_id: str, node_type: str, config: dict | None = None) -> dict:
    """Devolve um NOVO dict de spec com o nó + as 2 arestas. Levanta
    `EdicaoInvalida` se o id colidir, o tipo não for adicionável, ou a
    topologia resultante não validar."""
    if node_type not in TIPOS_ADICIONAVEIS:
        raise EdicaoInvalida(
            f"tipo '{node_type}' não pode ser adicionado pela GUI "
            f"(disponíveis: {list(TIPOS_ADICIONAVEIS)})"
        )
    if not _RE_NODE_ID.match(node_id):
        raise EdicaoInvalida("id do nó: 2–39 caracteres, minúsculas/dígitos/_, começando por letra")
    if node_id in spec.node_ids():
        raise EdicaoInvalida(f"já existe um nó '{node_id}' no grafo")

    raw = spec.model_dump()
    raw["nodes"].append({"id": node_id, "type": node_type, "config": config or {}, "locked": False})
    raw["edges"].append({
        "source": "classify", "when": "by_state_route",
        "route_value": node_id, "target": node_id, "locked": False,
    })
    raw["edges"].append({"source": node_id, "target": "__end__", "locked": False})

    nova = GraphSpec.model_validate(raw)
    erros = nova.validate_topology()
    if erros:
        raise EdicaoInvalida("topologia inválida após adicionar: " + "; ".join(erros))
    return raw


def remover_rota(spec: GraphSpec, node_id: str) -> dict:
    """Remove um nó NÃO travado e todas as arestas que o tocam."""
    alvo = next((n for n in spec.nodes if n.id == node_id), None)
    if alvo is None:
        raise EdicaoInvalida(f"nó '{node_id}' não existe")
    if alvo.locked:
        raise EdicaoInvalida(f"nó '{node_id}' é do esqueleto do grafo — não pode ser removido")

    raw = spec.model_dump()
    raw["nodes"] = [n for n in raw["nodes"] if n["id"] != node_id]
    raw["edges"] = [e for e in raw["edges"] if e["source"] != node_id and e["target"] != node_id]

    nova = GraphSpec.model_validate(raw)
    erros = nova.validate_topology()
    if erros:
        raise EdicaoInvalida("topologia inválida após remover: " + "; ".join(erros))
    return raw


def rotas_editaveis(spec: GraphSpec) -> list[dict]:
    """As arestas `classify --route_value--> nó` que NÃO são travadas — o que
    a GUI lista como 'fluxos que você pode remover'."""
    por_id = {n.id: n for n in spec.nodes}
    out = []
    for e in spec.edges:
        if e.source == "classify" and e.when == "by_state_route" and not e.locked:
            no = por_id.get(e.target)
            out.append({
                "route_value": e.route_value,
                "node_id": e.target,
                "node_type": no.type if no else "?",
                "config": no.config if no else {},
            })
    return out
