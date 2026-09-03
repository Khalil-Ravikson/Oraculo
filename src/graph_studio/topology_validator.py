"""
Validação de topologia de grafo (Camada 3 — composição visual, adendo de
nós declarativos). Função pura, sem I/O — reusada tanto pelo backend
(antes de persistir) quanto testável isoladamente.

Uma topologia é um dict:
    {
      "nodes": [{"node_id": "stt_default", "x": 100, "y": 50}, ...],
      "edges": [{"source_node": "stt_default", "source_port": "text",
                 "target_node": "llm_default", "target_port": "prompt"}, ...]
    }

Validações (todas acumuladas — retorna lista de erros, não para na
primeira, pra admin corrigir tudo de uma vez no canvas):
1. Todo node_id em `edges` precisa estar em `nodes` desta topologia (não
   basta existir no NodeRegistry global — precisa estar de fato no canvas).
2. Todo node_id em `nodes` precisa existir no NodeRegistry (nó real).
3. Toda edge precisa passar por NodeRegistry.validate_connection (tipos de
   porta batem) — reaproveita a validação já testada da Camada 1.
4. O grafo formado pelas edges precisa ser acíclico (DAG) — um grafo de
   execução com ciclo trava para sempre.
"""
from __future__ import annotations

from typing import Any, Dict, List
from src.graph_studio.node_registry import NodeRegistry


def validar_topologia(topology: Dict[str, Any], registry: NodeRegistry) -> List[str]:
    """Retorna lista de mensagens de erro. Lista vazia = topologia válida."""
    erros: List[str] = []

    nodes = topology.get("nodes", [])
    edges = topology.get("edges", [])

    node_ids_no_canvas = {n.get("node_id") for n in nodes if n.get("node_id")}

    if not nodes:
        erros.append("Topologia sem nenhum nó.")

    for n in nodes:
        node_id = n.get("node_id")
        if not node_id:
            erros.append("Nó no canvas sem 'node_id'.")
            continue
        node = registry.get(node_id)
        if node is None:
            erros.append(f"Nó '{node_id}' não existe no NodeRegistry.")
            continue

        cfg = n.get("config")
        if isinstance(cfg, dict) and cfg:
            props = (node.config_schema or {}).get("properties", {})
            if props:
                desconhecidas = set(cfg) - set(props)
                if desconhecidas:
                    erros.append(
                        f"Nó '{node_id}': configuração desconhecida "
                        f"{sorted(desconhecidas)} (aceita: {sorted(props)})."
                    )
            elif cfg:
                erros.append(f"Nó '{node_id}' não aceita configuração.")

    grafo_adjacencia: Dict[str, List[str]] = {nid: [] for nid in node_ids_no_canvas}

    for i, edge in enumerate(edges):
        source_node = edge.get("source_node")
        source_port = edge.get("source_port")
        target_node = edge.get("target_node")
        target_port = edge.get("target_port")

        if not all([source_node, source_port, target_node, target_port]):
            erros.append(f"Edge #{i} incompleta (faltam campos).")
            continue

        if source_node not in node_ids_no_canvas:
            erros.append(f"Edge #{i}: nó de origem '{source_node}' não está no canvas.")
        if target_node not in node_ids_no_canvas:
            erros.append(f"Edge #{i}: nó de destino '{target_node}' não está no canvas.")
        if source_node not in node_ids_no_canvas or target_node not in node_ids_no_canvas:
            continue  # sem os dois nós no canvas, não dá pra validar tipo nem ciclo

        is_valid, erro_tipo = registry.validate_connection(
            source_node, source_port, target_node, target_port
        )
        if not is_valid:
            erros.append(f"Edge #{i} ({source_node}→{target_node}): {erro_tipo}")

        grafo_adjacencia.setdefault(source_node, []).append(target_node)

    ciclo = _detectar_ciclo(grafo_adjacencia)
    if ciclo:
        erros.append(f"Topologia tem ciclo: {' → '.join(ciclo)}")

    return erros


def _detectar_ciclo(adjacencia: Dict[str, List[str]]) -> List[str] | None:
    """DFS com 3 cores (branco/cinza/preto). Retorna o caminho do ciclo
    encontrado, ou None se o grafo é acíclico."""
    BRANCO, CINZA, PRETO = 0, 1, 2
    cor = {no: BRANCO for no in adjacencia}
    caminho: List[str] = []

    def dfs(no: str) -> List[str] | None:
        cor[no] = CINZA
        caminho.append(no)
        for vizinho in adjacencia.get(no, []):
            if cor.get(vizinho, BRANCO) == CINZA:
                return caminho[caminho.index(vizinho):] + [vizinho]
            if cor.get(vizinho, BRANCO) == BRANCO:
                resultado = dfs(vizinho)
                if resultado:
                    return resultado
        caminho.pop()
        cor[no] = PRETO
        return None

    for no in list(adjacencia.keys()):
        if cor[no] == BRANCO:
            resultado = dfs(no)
            if resultado:
                return resultado
    return None
