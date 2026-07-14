# tests/unit/agents/sigaa/test_eligibility.py
"""
Testes das regras puras de elegibilidade extraídas de
application/workers/worker_sigaa.py na Fase 5 do
PLANO_REFATORACAO_SUPERVISOR.md. Nenhum mock de Playwright/Redis necessário
— são funções puras.
"""
from src.agents.sigaa.eligibility import (
    calcular_elegiveis_proximo_semestre,
    calcular_faltantes,
    calcular_percentual_integralizacao,
    disciplinas_concluidas,
)

OBRIGATORIAS = [
    {"nome": "CÁLCULO I", "ch": 90, "prerequisitos": []},
    {"nome": "CÁLCULO II", "ch": 90, "prerequisitos": ["CÁLCULO I"]},
    {"nome": "ESTRUTURA DE DADOS", "ch": 60, "prerequisitos": ["ALGORITMOS E PROGRAMAÇÃO"]},
    {"nome": "ALGORITMOS E PROGRAMAÇÃO", "ch": 60, "prerequisitos": []},
]

HISTORICO = [
    {"disciplina": "CÁLCULO I", "situacao": "APROVADO"},
    {"disciplina": "ALGORITMOS E PROGRAMAÇÃO", "situacao": "APROVADO"},
    {"disciplina": "CÁLCULO II", "situacao": "REPROVADO"},
]


def test_disciplinas_concluidas_filtra_so_aprovadas():
    assert disciplinas_concluidas(HISTORICO) == {"CÁLCULO I", "ALGORITMOS E PROGRAMAÇÃO"}


def test_calcular_faltantes_ignora_pre_requisito():
    faltam = calcular_faltantes(HISTORICO, OBRIGATORIAS)
    nomes = {d["nome"] for d in faltam}
    # CÁLCULO II não foi aprovado (REPROVADO != APROVADO) e ESTRUTURA DE DADOS nunca cursada
    assert nomes == {"CÁLCULO II", "ESTRUTURA DE DADOS"}


def test_calcular_elegiveis_exige_pre_requisito_cumprido():
    elegiveis = calcular_elegiveis_proximo_semestre(HISTORICO, OBRIGATORIAS)
    nomes = {d["nome"] for d in elegiveis}
    # ESTRUTURA DE DADOS só é elegível porque seu pré-requisito (ALGORITMOS) foi aprovado.
    # CÁLCULO II não entra: já foi cursada (mesmo que reprovada, calcular_faltantes usa isso,
    # mas elegibilidade usa a MESMA regra de "concluída" — reprovada não conta como concluída,
    # então CÁLCULO II também é elegível aqui, pois seu pré-requisito CÁLCULO I foi aprovado.
    assert nomes == {"CÁLCULO II", "ESTRUTURA DE DADOS"}


def test_calcular_elegiveis_bloqueia_sem_pre_requisito_cumprido():
    historico_vazio = []
    elegiveis = calcular_elegiveis_proximo_semestre(historico_vazio, OBRIGATORIAS)
    nomes = {d["nome"] for d in elegiveis}
    # Só as sem pré-requisito ficam elegíveis quando nada foi cursado ainda
    assert nomes == {"CÁLCULO I", "ALGORITMOS E PROGRAMAÇÃO"}


def test_calcular_percentual_integralizacao():
    assert calcular_percentual_integralizacao(3135, 3915) == 80.1
    assert calcular_percentual_integralizacao(0, 0) == 0.0
