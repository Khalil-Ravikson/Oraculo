"""
src/agents/sigaa/eligibility.py
==================================
Regras puras de elegibilidade acadêmica — sem Playwright, sem Redis, sem
formatação de mensagem. Testável isoladamente com fixtures de histórico e
estrutura curricular.

Achado da Fase 5 (PLANO_REFATORACAO_SUPERVISOR.md, seção 2.3): esta lógica
NÃO estava em `sigaa_agent.py` como o levantamento original supunha — vivia
duplicada dentro de `application/workers/worker_sigaa.py`, entre
`_run_historico` (calculava "disciplinas faltantes") e `_run_turmas`
(calculava "disciplinas elegíveis p/ próximo semestre"). São perguntas de
negócio DIFERENTES que compartilhavam o mesmo bloco de "quais disciplinas já
foram concluídas" — esse bloco compartilhado é `disciplinas_concluidas()`
abaixo.
"""
from __future__ import annotations


def disciplinas_concluidas(disciplinas_historico: list[dict]) -> set[str]:
    """Nomes (upper-case) das disciplinas já aprovadas no histórico."""
    return {
        d["disciplina"].upper()
        for d in disciplinas_historico
        if d.get("situacao") == "APROVADO"
    }


def calcular_faltantes(disciplinas_historico: list[dict], obrigatorias: list[dict]) -> list[dict]:
    """
    Disciplinas obrigatórias ainda NÃO concluídas, independente de
    pré-requisito estar cumprido ou não (usado por "quais disciplinas faltam").
    """
    concluidas = disciplinas_concluidas(disciplinas_historico)
    return [disc for disc in obrigatorias if disc["nome"].upper() not in concluidas]


def calcular_elegiveis_proximo_semestre(disciplinas_historico: list[dict], obrigatorias: list[dict]) -> list[dict]:
    """
    Disciplinas obrigatórias ainda não concluídas E cujos pré-requisitos já
    foram todos cumpridos (usado por "o que posso cursar no próximo
    semestre").
    """
    concluidas = disciplinas_concluidas(disciplinas_historico)
    elegiveis = []
    for disc in obrigatorias:
        nome = disc["nome"].upper()
        if nome in concluidas:
            continue
        reqs_cumpridos = [req.upper() for req in disc["prerequisitos"]]
        if all(req in concluidas for req in reqs_cumpridos):
            elegiveis.append(disc)
    return elegiveis


def calcular_percentual_integralizacao(ch_concluida: int, ch_exigida: int) -> float:
    """Percentual de carga horária integralizada, arredondado a 1 casa decimal."""
    if not ch_exigida:
        return 0.0
    return round((ch_concluida / ch_exigida) * 100, 1)
