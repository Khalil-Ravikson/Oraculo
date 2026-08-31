"""
src/graph/graph_executor.py — execução de uma topologia salva (Hub v2 Sprint 8, MVP)
==================================================================================
Pega uma topologia de `graph_topology` (nós posicionados + arestas entre
portas), resolve os nós pelo `NodeRegistry`, calcula a ordem topológica e
executa em sequência, emitindo um evento por etapa.

Dois modos:
  - `dry_run=True` (padrão): valida + calcula o caminho + emite os eventos,
    **sem chamar `node.execute()`**. É o que o botão "Testar" do Graph
    Studio usa — mostra o path no canvas sem tocar em LLM/API real.
  - `dry_run=False`: executa de verdade. Só deve ser chamado quando/se o
    `FEATURE_GRAPH_EXECUTOR_PILOTO` estiver ligado e um trecho piloto for
    ligado ao pipeline real — nada lê essa flag no caminho quente ainda.

Respeita `graph_node_config`: nó desabilitado é PULADO (evento `pulado`),
e as arestas que saem dele não entregam dado a jusante.

Este NÃO é o dispatcher de produção — é o degrau que prova que
`NodeRegistry` + `graph_topology` + `graph_node_config` podem executar um
trecho real de ponta a ponta.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.graph.execution_context import ExecutionContext
from src.graph.node_registry import NodeRegistry, get_registry
from src.graph.topology_validator import validar_topologia

logger = logging.getLogger(__name__)


@dataclass
class ResultadoExecucao:
    ok: bool
    dry_run: bool
    ordem: list[str] = field(default_factory=list)
    eventos: list[dict] = field(default_factory=list)
    saidas: dict[str, Any] = field(default_factory=dict)
    erros: list[str] = field(default_factory=list)
    duracao_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "dry_run": self.dry_run, "ordem": self.ordem,
            "eventos": self.eventos, "erros": self.erros, "duracao_ms": self.duracao_ms,
        }


def _ordem_topologica(nodes: list[str], edges: list[dict]) -> list[str] | None:
    """Kahn. Retorna None se houver ciclo (o validador já pega, mas defensivo)."""
    grau = {n: 0 for n in nodes}
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for e in edges:
        s, t = e.get("source_node"), e.get("target_node")
        if s in grau and t in grau:
            adj[s].append(t)
            grau[t] += 1
    fila = [n for n in nodes if grau[n] == 0]
    ordem = []
    while fila:
        n = fila.pop(0)
        ordem.append(n)
        for viz in adj[n]:
            grau[viz] -= 1
            if grau[viz] == 0:
                fila.append(viz)
    return ordem if len(ordem) == len(nodes) else None


class GraphExecutor:
    def __init__(self, registry: NodeRegistry | None = None, desabilitados: set[str] | None = None):
        self.registry = registry or get_registry()
        self.desabilitados = desabilitados or set()

    async def executar(
        self,
        topology: dict,
        inputs: dict[str, Any] | None = None,
        context: ExecutionContext | None = None,
        *,
        dry_run: bool = True,
    ) -> ResultadoExecucao:
        t0 = time.monotonic()
        inputs = inputs or {}
        context = context or ExecutionContext()
        res = ResultadoExecucao(ok=True, dry_run=dry_run)

        erros_val = validar_topologia(topology, self.registry)
        if erros_val:
            res.ok = False
            res.erros = erros_val
            res.duracao_ms = int((time.monotonic() - t0) * 1000)
            return res

        node_ids = [n["node_id"] for n in topology.get("nodes", [])]
        edges = topology.get("edges", [])
        ordem = _ordem_topologica(node_ids, edges)
        if ordem is None:
            res.ok = False
            res.erros = ["Topologia tem ciclo — não é possível executar."]
            return res
        res.ordem = ordem

        # arestas de entrada por nó: {target_node: [(source_node, source_port, target_port)]}
        entradas: dict[str, list[tuple]] = {n: [] for n in node_ids}
        for e in edges:
            if e.get("target_node") in entradas:
                entradas[e["target_node"]].append((e["source_node"], e["source_port"], e["target_port"]))

        for node_id in ordem:
            if node_id in self.desabilitados:
                res.eventos.append({"tipo": "pulado", "node": node_id, "motivo": "componente desativado"})
                continue

            node = self.registry.get(node_id)
            node_inputs: dict[str, Any] = {}
            # nó-fonte (sem aresta de entrada) recebe os inputs iniciais
            if not entradas[node_id]:
                node_inputs.update(inputs)
            for src, src_port, tgt_port in entradas[node_id]:
                if src in res.saidas and src_port in (res.saidas[src] or {}):
                    node_inputs[tgt_port] = res.saidas[src][src_port]

            res.eventos.append({"tipo": "iniciando", "node": node_id, "inputs": sorted(node_inputs.keys())})

            if dry_run:
                res.eventos.append({"tipo": "simulado", "node": node_id})
                res.saidas[node_id] = {p.name: f"<{p.type_}>" for p in node.output_ports}
                continue

            try:
                n0 = time.monotonic()
                out = await node.execute(node_inputs, context)
                res.saidas[node_id] = out or {}
                res.eventos.append({
                    "tipo": "concluido", "node": node_id,
                    "ms": int((time.monotonic() - n0) * 1000),
                    "outputs": sorted((out or {}).keys()),
                })
            except Exception as exc:  # noqa: BLE001
                res.ok = False
                res.erros.append(f"{node_id}: {exc}")
                res.eventos.append({"tipo": "erro", "node": node_id, "erro": str(exc)[:200]})
                break

        res.duracao_ms = int((time.monotonic() - t0) * 1000)
        return res


async def executar_topologia_salva(nome: str, *, dry_run: bool = True, inputs: dict | None = None) -> ResultadoExecucao:
    """Carrega uma topologia de `graph_topology` pelo nome e executa.
    Aplica o toggle de `graph_node_config` (nós desabilitados são pulados)."""
    from src.graph import node_config, topology_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        topos = await topology_registry.listar(session)
        alvo = next((t for t in topos if t["name"] == nome), None)
        if alvo is None:
            r = ResultadoExecucao(ok=False, dry_run=dry_run)
            r.erros = [f"Topologia '{nome}' não encontrada."]
            return r
        config_rows = await node_config.listar(session)

    desabilitados = {c["node_id"] for c in config_rows if not c["habilitado"]}
    executor = GraphExecutor(desabilitados=desabilitados)
    return await executor.executar(alvo["topology_json"], inputs=inputs, dry_run=dry_run)
