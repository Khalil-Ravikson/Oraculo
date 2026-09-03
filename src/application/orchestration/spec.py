"""
src/application/orchestration/spec.py
====================================
`GraphSpec` — a topologia do grafo de orquestração como DADO (ADR 0008 Fase 5).

Substitui os ~90 `graph.add_node(...)` / `add_edge(...)` / `add_conditional_edges(...)`
hardcoded do `builder.py` por uma definição declarativa (JSON), versionada
como `route_registry` (Postgres + espelho Redis + default embutido).

    NodeSpec  = uma instância de nó no grafo:  {id, type, config, locked}
    EdgeSpec  = uma aresta:
                  simples:      {source, target}
                  condicional:  {source, when, route_value, target}
                (arestas condicionais que saem do MESMO nó devem compartilhar
                 o mesmo `when` — viram um único add_conditional_edges)
    GraphSpec = {version, entrypoint, nodes[], edges[]}

`validate_topology()` roda no POST do Hub ANTES de persistir (mesma disciplina
de `route_registry.validar_campos`). Os funis de ticket/CRUD ficam no default
com `locked=True` — a GUI os mostra mas não deixa editar (a fragilidade deles
— validadores por campo, `interrupt()` por pergunta, bug do checkpointer Redis
com múltiplos interrupts — não cabe num `{source, target}`).
"""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

END_ID = "__end__"


class NodeSpec(BaseModel):
    id: str
    type: str
    config: dict = Field(default_factory=dict)
    locked: bool = False


class EdgeSpec(BaseModel):
    source: str
    target: str
    when: str | None = None          # nome de um router (routers.py) — aresta condicional
    route_value: str | None = None   # valor que o router precisa devolver p/ tomar esta aresta
    locked: bool = False


class GraphSpec(BaseModel):
    version: int = 1
    entrypoint: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]

    # ── consultas ──────────────────────────────────────────────────────────

    def node_ids(self) -> set[str]:
        return {n.id for n in self.nodes}

    def edges_por_source(self) -> dict[str, list[EdgeSpec]]:
        agrupado: dict[str, list[EdgeSpec]] = defaultdict(list)
        for e in self.edges:
            agrupado[e.source].append(e)
        return agrupado

    # ── validação de topologia ────────────────────────────────────────────

    def validate_topology(self) -> list[str]:
        """Erros que o Pydantic não pega. Lista vazia = spec válida."""
        from src.application.orchestration import node_manifest, routers

        erros: list[str] = []
        ids = [n.id for n in self.nodes]

        # ids únicos
        dups = {i for i in ids if ids.count(i) > 1}
        if dups:
            erros.append(f"ids de nó duplicados: {sorted(dups)}")
        ids_set = set(ids)

        if END_ID in ids_set:
            erros.append(f"'{END_ID}' é reservado — não pode ser id de nó")

        # tipos existem no manifesto
        tipos_ok = node_manifest.tipos_registrados()
        for n in self.nodes:
            if n.type not in tipos_ok:
                erros.append(f"nó '{n.id}': tipo '{n.type}' não existe no manifesto")

        # entrypoint é um nó conhecido
        if self.entrypoint not in ids_set:
            erros.append(f"entrypoint '{self.entrypoint}' não é um nó da spec")

        # arestas: source/target válidos, routers existem
        routers_ok = routers.nomes_registrados()
        alvos_validos = ids_set | {END_ID}
        for e in self.edges:
            if e.source not in ids_set:
                erros.append(f"aresta {e.source}->{e.target}: source desconhecido")
            if e.target not in alvos_validos:
                erros.append(f"aresta {e.source}->{e.target}: target desconhecido")
            if e.source == END_ID:
                erros.append(f"aresta a partir de '{END_ID}' é inválida")
            if e.when is not None:
                if e.when not in routers_ok:
                    erros.append(f"aresta {e.source}->{e.target}: router '{e.when}' não registrado")
                if e.route_value is None:
                    erros.append(f"aresta condicional {e.source}->{e.target}: falta `route_value`")

        # arestas condicionais do mesmo source compartilham o mesmo `when`
        for source, grupo in self.edges_por_source().items():
            whens = {e.when for e in grupo if e.when is not None}
            se_tem_condicional = bool(whens)
            se_tem_simples = any(e.when is None for e in grupo)
            if len(whens) > 1:
                erros.append(f"nó '{source}' tem arestas com routers diferentes: {sorted(whens)}")
            if se_tem_condicional and se_tem_simples:
                erros.append(f"nó '{source}' mistura aresta condicional e simples")
            valores = [e.route_value for e in grupo if e.route_value is not None]
            dup_val = {v for v in valores if valores.count(v) > 1}
            if dup_val:
                erros.append(f"nó '{source}': `route_value` repetido {sorted(dup_val)}")

        if erros:
            return erros

        # alcançabilidade a partir do entrypoint + caminho até __end__
        erros += self._checar_alcancabilidade(ids_set)
        return erros

    def _checar_alcancabilidade(self, ids_set: set[str]) -> list[str]:
        adj: dict[str, set[str]] = defaultdict(set)
        for e in self.edges:
            adj[e.source].add(e.target)

        # BFS a partir do entrypoint
        visto = {self.entrypoint}
        fila = [self.entrypoint]
        while fila:
            atual = fila.pop(0)
            for viz in adj.get(atual, ()):
                if viz not in visto:
                    visto.add(viz)
                    fila.append(viz)

        erros: list[str] = []
        inalcancaveis = ids_set - visto
        if inalcancaveis:
            erros.append(f"nós inalcançáveis a partir de '{self.entrypoint}': {sorted(inalcancaveis)}")

        # todo nó precisa ter um caminho até __end__ (senão trava a sessão)
        alcanca_fim: set[str] = set()
        mudou = True
        while mudou:
            mudou = False
            for nid in ids_set:
                if nid in alcanca_fim:
                    continue
                if END_ID in adj.get(nid, ()) or (adj.get(nid, set()) & alcanca_fim):
                    alcanca_fim.add(nid)
                    mudou = True
        sem_saida = (ids_set & visto) - alcanca_fim
        if sem_saida:
            erros.append(f"nós sem caminho até o fim (__end__): {sorted(sem_saida)}")
        return erros


def spec_valida_ou_erro(raw: dict) -> GraphSpec:
    """Parse + validação de topologia num passo. Levanta `ValueError` com a
    lista de erros — usado pelo loader e pelo endpoint de escrita do Hub."""
    spec = GraphSpec.model_validate(raw)
    erros = spec.validate_topology()
    if erros:
        raise ValueError("GraphSpec inválida:\n  - " + "\n  - ".join(erros))
    return spec
