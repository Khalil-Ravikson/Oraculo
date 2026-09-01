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

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.graph.execution_context import ExecutionContext
from src.graph.node_registry import NodeRegistry, get_registry
from src.graph.topology_validator import validar_topologia

logger = logging.getLogger(__name__)

# Limites da execução sandbox (teste manual no Graph Studio). Existem só para
# um clique acidental num fluxo grande não virar uma conta de tokens ou uma
# request pendurada — não são política de produto.
_SANDBOX_MAX_NODES = 8
_SANDBOX_TIMEOUT_S = 30


@dataclass
class ResultadoExecucao:
    ok: bool
    dry_run: bool
    ordem: list[str] = field(default_factory=list)
    eventos: list[dict] = field(default_factory=list)
    saidas: dict[str, Any] = field(default_factory=dict)
    erros: list[str] = field(default_factory=list)
    duracao_ms: int = 0
    resposta: str | None = None

    def _saidas_serializaveis(self) -> dict:
        out: dict[str, dict] = {}
        for node_id, portas in (self.saidas or {}).items():
            if not isinstance(portas, dict):
                continue
            out[node_id] = {p: str(v)[:500] for p, v in portas.items()}
        return out

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "dry_run": self.dry_run, "ordem": self.ordem,
            "eventos": self.eventos, "erros": self.erros, "duracao_ms": self.duracao_ms,
            "resposta": self.resposta, "saidas": self._saidas_serializaveis(),
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


def _extrair_resposta(res: ResultadoExecucao) -> str | None:
    """Melhor-esforço para o texto que o painel de teste mostra como resposta
    final: o último nó (na ordem de execução) que produziu algo utilizável."""
    for node_id in reversed(res.ordem):
        saida = res.saidas.get(node_id)
        if not isinstance(saida, dict):
            continue
        for chave in ("response", "text", "structured", "result", "ok"):
            if chave in saida and saida[chave] is not None:
                return str(saida[chave])
    return None


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
        sandbox: bool = False,
    ) -> ResultadoExecucao:
        t0 = time.monotonic()
        inputs = inputs or {}
        real = sandbox and not dry_run
        if context is None:
            context = (
                ExecutionContext(tenant_id=None, metadata={"sandbox": True})
                if real else ExecutionContext()
            )
        res = ResultadoExecucao(ok=True, dry_run=dry_run)

        erros_val = validar_topologia(topology, self.registry)
        if erros_val:
            res.ok = False
            res.erros = erros_val
            res.duracao_ms = int((time.monotonic() - t0) * 1000)
            return res

        node_ids = [n["node_id"] for n in topology.get("nodes", [])]
        edges = topology.get("edges", [])

        if real and len(node_ids) > _SANDBOX_MAX_NODES:
            res.ok = False
            res.erros = [f"O teste real aceita no máximo {_SANDBOX_MAX_NODES} componentes."]
            res.duracao_ms = int((time.monotonic() - t0) * 1000)
            return res

        ordem = _ordem_topologica(node_ids, edges)
        if ordem is None:
            res.ok = False
            res.erros = ["Topologia tem ciclo — não é possível executar."]
            res.duracao_ms = int((time.monotonic() - t0) * 1000)
            return res
        res.ordem = ordem

        # config por nó (painel de propriedades do Studio): {node_id: {chave: valor}}
        node_cfg: dict[str, dict] = {
            n["node_id"]: (n.get("config") or {})
            for n in topology.get("nodes", []) if isinstance(n.get("config"), dict)
        }

        # arestas de entrada por nó: {target_node: [(source_node, source_port, target_port)]}
        entradas: dict[str, list[tuple]] = {n: [] for n in node_ids}
        for e in edges:
            if e.get("target_node") in entradas:
                entradas[e["target_node"]].append((e["source_node"], e["source_port"], e["target_port"]))

        # nós que não vão produzir saída utilizável: desabilitados, com erro,
        # ou que dependem (mesmo transitivamente) de um desses. Um nó a jusante
        # de um pulado NÃO deve executar com input faltando — é pulado também.
        bloqueados: set[str] = set()

        async def _rodar_nos() -> None:
            for node_id in ordem:
                if node_id in self.desabilitados:
                    res.eventos.append({"tipo": "pulado", "node": node_id, "motivo": "componente desativado"})
                    bloqueados.add(node_id)
                    continue

                fontes = {src for src, _sp, _tp in entradas[node_id]}
                fontes_bloqueadas = fontes & bloqueados
                if fontes_bloqueadas:
                    res.eventos.append({
                        "tipo": "pulado", "node": node_id,
                        "motivo": f"depende de componente pulado ({', '.join(sorted(fontes_bloqueadas))})",
                    })
                    bloqueados.add(node_id)
                    continue

                node = self.registry.get(node_id)
                node_inputs: dict[str, Any] = {}
                # nó-fonte (sem aresta de entrada) recebe os inputs iniciais
                if not entradas[node_id]:
                    node_inputs.update(inputs)
                for src, src_port, tgt_port in entradas[node_id]:
                    if src in res.saidas and src_port in (res.saidas[src] or {}):
                        node_inputs[tgt_port] = res.saidas[src][src_port]
                # config do painel de propriedades sobrepõe o que veio por aresta
                node_inputs.update(node_cfg.get(node_id, {}))
                # no teste real, todo nó herda o rótulo de rota (telemetria isolada)
                if real and inputs.get("rota"):
                    node_inputs.setdefault("rota", inputs["rota"])

                res.eventos.append({"tipo": "iniciando", "node": node_id, "inputs": sorted(node_inputs.keys())})

                if dry_run:
                    res.eventos.append({"tipo": "simulado", "node": node_id})
                    res.saidas[node_id] = {p.name: f"<{p.type_}>" for p in node.output_ports}
                    continue

                try:
                    n0 = time.monotonic()
                    out = await node.execute(node_inputs, context)
                    res.saidas[node_id] = out or {}
                    evento = {
                        "tipo": "concluido", "node": node_id,
                        "ms": int((time.monotonic() - n0) * 1000),
                        "outputs": sorted((out or {}).keys()),
                    }
                    tokens = (out or {}).get("tokens_used")
                    if isinstance(tokens, (list, tuple)) and len(tokens) == 2:
                        evento["tokens"] = [tokens[0], tokens[1]]
                    res.eventos.append(evento)
                except Exception as exc:  # noqa: BLE001
                    res.ok = False
                    res.erros.append(f"{node_id}: {exc}")
                    res.eventos.append({"tipo": "erro", "node": node_id, "erro": str(exc)[:200]})
                    # não aborta o fluxo todo: marca como bloqueado pra os nós
                    # a jusante serem pulados, mas deixa ramos paralelos rodarem.
                    bloqueados.add(node_id)
                    continue

        try:
            if real:
                await asyncio.wait_for(_rodar_nos(), timeout=_SANDBOX_TIMEOUT_S)
            else:
                await _rodar_nos()
        except asyncio.TimeoutError:
            res.ok = False
            res.erros.append(f"Teste interrompido — passou de {_SANDBOX_TIMEOUT_S}s.")
            res.eventos.append({"tipo": "erro", "node": "-", "erro": "timeout"})

        if not dry_run:
            res.resposta = _extrair_resposta(res)
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


async def executar_topologia_sandbox(
    nome: str, mensagem_teste: str, *, rota: str = "SANDBOX",
) -> ResultadoExecucao:
    """Teste manual do Graph Studio: roda a topologia salva DE VERDADE
    (`dry_run=False`), porém isolada — `tenant_id=None`, sem persistência,
    limite de nós e timeout duro (ver `_SANDBOX_*`). Só é chamada quando o
    operador clica em "Rodar teste real".

    `inputs` cobre tanto o `TriggerNode` (`mensagem_teste`) quanto um `LLMNode`
    sozinho no canvas (`prompt`)."""
    from src.graph import node_config, topology_registry
    from src.infrastructure.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        topos = await topology_registry.listar(session)
        alvo = next((t for t in topos if t["name"] == nome), None)
        if alvo is None:
            r = ResultadoExecucao(ok=False, dry_run=False)
            r.erros = [f"Topologia '{nome}' não encontrada."]
            return r
        config_rows = await node_config.listar(session)

    desabilitados = {c["node_id"] for c in config_rows if not c["habilitado"]}
    executor = GraphExecutor(desabilitados=desabilitados)
    inputs = {"mensagem_teste": mensagem_teste, "prompt": mensagem_teste, "rota": rota}
    return await executor.executar(
        alvo["topology_json"], inputs=inputs, dry_run=False, sandbox=True,
    )
