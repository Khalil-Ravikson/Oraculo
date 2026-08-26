# tests/unit/domain/test_permissions.py
"""
Cobertura de domain/permissions.py — item pendente do ADR 0001
("RBAC completo ainda não está testado corretamente em main") fechado como
parte da Fase 2 do plano de integração LangGraph/REST/MCP. Zero testes
existiam pra este módulo antes desta sessão, apesar de ser lógica de
domínio pura (sem IO) e ser o coração de toda decisão de autorização do
Oráculo — tanto no dispatcher.py/dispatcher_langgraph.py legado quanto nos
nodes nativos do LangGraph (Fase 2d, via agents/tickets/rbac.py).
"""
from __future__ import annotations

import pytest

from src.domain.entities.enums import RoleEnum, StatusMatriculaEnum
from src.domain.permissions import (
    _PERMISSOES,
    _STATUS_BLOQUEADOS,
    ContextoPermissao,
    Recurso,
    calcular_permissoes,
)

_RECURSOS_PUBLICOS = _PERMISSOES[RoleEnum.publico]


# ─────────────────────────────────────────────────────────────────────────────
# calcular_permissoes — matriz role × status
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", list(RoleEnum))
def test_calcular_permissoes_status_ativo_usa_matriz_completa_do_role(role):
    """Com status=ativo, cada role recebe exatamente o conjunto de recursos
    declarado em _PERMISSOES — nenhum a mais, nenhum a menos."""
    ctx = calcular_permissoes(role=role, status=StatusMatriculaEnum.ativo)
    assert ctx.recursos_permitidos == _PERMISSOES[role]


@pytest.mark.parametrize("role", list(RoleEnum))
@pytest.mark.parametrize("status", sorted(_STATUS_BLOQUEADOS, key=lambda s: s.value))
def test_calcular_permissoes_status_bloqueado_reduz_a_recursos_publicos(role, status):
    """inativo/pendente reduzem QUALQUER role (inclusive admin) a só
    recursos públicos — status bloqueado tem precedência sobre role."""
    ctx = calcular_permissoes(role=role, status=status)
    assert ctx.recursos_permitidos == _RECURSOS_PUBLICOS


def test_calcular_permissoes_status_trancado_nao_esta_na_lista_de_bloqueio():
    """Achado ao escrever esta suíte (não uma regressão desta sessão —
    comportamento pré-existente): _STATUS_BLOQUEADOS só contém
    inativo/pendente. `trancado` (matrícula trancada) NÃO reduz o acesso —
    um estudante trancado mantém o mesmo `recursos_permitidos` de um
    estudante ativo. Pode ser intencional (ex.: deixar o aluno trancado
    abrir chamado pra resolver a própria situação) ou um gap não percebido
    — registrado aqui como comportamento atual, não corrigido
    silenciosamente (é uma decisão de regra de negócio, não um bug de
    código óbvio)."""
    ctx = calcular_permissoes(role=RoleEnum.estudante, status=StatusMatriculaEnum.trancado)
    assert ctx.recursos_permitidos == _PERMISSOES[RoleEnum.estudante]
    assert StatusMatriculaEnum.trancado not in _STATUS_BLOQUEADOS


def test_calcular_permissoes_propaga_campos_de_display():
    ctx = calcular_permissoes(
        role=RoleEnum.estudante, status=StatusMatriculaEnum.ativo,
        nome_display="Fulano de Tal", centro="CECEN", curso="Engenharia Civil",
    )
    assert ctx.nome_display == "Fulano de Tal"
    assert ctx.centro == "CECEN"
    assert ctx.curso == "Engenharia Civil"


def test_calcular_permissoes_defaults():
    ctx = calcular_permissoes(role=RoleEnum.publico, status=StatusMatriculaEnum.ativo)
    assert ctx.nome_display == "visitante"
    assert ctx.centro is None
    assert ctx.curso is None


# ─────────────────────────────────────────────────────────────────────────────
# ContextoPermissao.pode()
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("recurso", sorted(_RECURSOS_PUBLICOS, key=lambda r: r.value))
@pytest.mark.parametrize("status", list(StatusMatriculaEnum))
def test_pode_recursos_publicos_sempre_liberados_mesmo_bloqueado(recurso, status):
    """Recursos públicos (INFO_*) são liberados incondicionalmente — mesmo
    pra role=publico com status inativo/pendente. Filosofia documentada no
    módulo: o Oráculo nunca nega informação pública."""
    ctx = ContextoPermissao(role=RoleEnum.publico, status=status, recursos_permitidos=set())
    assert ctx.pode(recurso) is True


@pytest.mark.parametrize("status", sorted(_STATUS_BLOQUEADOS, key=lambda s: s.value))
def test_pode_recurso_institucional_negado_com_status_bloqueado_mesmo_se_no_set(status):
    """pode() rechecka o status por conta própria, não confia só em
    recursos_permitidos — um ContextoPermissao construído manualmente com
    CHAMADO_GLPI no set ainda é negado se o status está bloqueado."""
    ctx = ContextoPermissao(
        role=RoleEnum.estudante, status=status,
        recursos_permitidos={Recurso.CHAMADO_GLPI},
    )
    assert ctx.pode(Recurso.CHAMADO_GLPI) is False


def test_pode_recurso_institucional_liberado_pro_role_certo():
    ctx = calcular_permissoes(role=RoleEnum.estudante, status=StatusMatriculaEnum.ativo)
    assert ctx.pode(Recurso.CHAMADO_GLPI) is True
    assert ctx.pode(Recurso.HISTORICO_ACADEMICO) is True


def test_pode_recurso_administrativo_negado_pra_estudante():
    ctx = calcular_permissoes(role=RoleEnum.estudante, status=StatusMatriculaEnum.ativo)
    assert ctx.pode(Recurso.GESTAO_USUARIOS) is False
    assert ctx.pode(Recurso.DASHBOARD_MONITOR) is False


def test_pode_admin_tem_todos_os_recursos():
    ctx = calcular_permissoes(role=RoleEnum.admin, status=StatusMatriculaEnum.ativo)
    for recurso in Recurso:
        assert ctx.pode(recurso) is True, f"admin deveria poder {recurso}"


# ─────────────────────────────────────────────────────────────────────────────
# ContextoPermissao.mensagem_sem_permissao()
# ─────────────────────────────────────────────────────────────────────────────

def test_mensagem_status_pendente_convida_pro_cadastro():
    ctx = calcular_permissoes(role=RoleEnum.estudante, status=StatusMatriculaEnum.pendente)
    msg = ctx.mensagem_sem_permissao(Recurso.CHAMADO_GLPI)
    assert "cadastro" in msg.lower()


def test_mensagem_status_inativo_orienta_ctic():
    ctx = calcular_permissoes(role=RoleEnum.estudante, status=StatusMatriculaEnum.inativo)
    msg = ctx.mensagem_sem_permissao(Recurso.CHAMADO_GLPI)
    assert "inativo" in msg.lower()
    assert "ctic" in msg.lower()


def test_mensagem_role_publico_convida_pra_comunidade_uema():
    ctx = calcular_permissoes(role=RoleEnum.publico, status=StatusMatriculaEnum.ativo)
    msg = ctx.mensagem_sem_permissao(Recurso.CHAMADO_GLPI)
    assert "comunidade uema" in msg.lower()


def test_mensagem_generica_pra_role_valido_sem_permissao_especifica():
    """estudante ativo pedindo um recurso administrativo: nenhum dos 3
    motivos específicos (pendente/inativo/publico) se aplica → mensagem
    genérica."""
    ctx = calcular_permissoes(role=RoleEnum.estudante, status=StatusMatriculaEnum.ativo)
    msg = ctx.mensagem_sem_permissao(Recurso.GESTAO_USUARIOS)
    assert msg == "Você não tem permissão para acessar este recurso."


# ─────────────────────────────────────────────────────────────────────────────
# ContextoPermissao.lista_tools_permitidas()
# ─────────────────────────────────────────────────────────────────────────────

def test_lista_tools_estudante_nao_inclui_tools_administrativas():
    ctx = calcular_permissoes(role=RoleEnum.estudante, status=StatusMatriculaEnum.ativo)
    tools = ctx.lista_tools_permitidas()
    assert "abrir_chamado_glpi" in tools
    assert "consultar_calendario_academico" in tools
    assert "admin_limpar_cache" not in tools
    assert "admin_status_sistema" not in tools


def test_lista_tools_admin_inclui_tools_administrativas():
    ctx = calcular_permissoes(role=RoleEnum.admin, status=StatusMatriculaEnum.ativo)
    tools = ctx.lista_tools_permitidas()
    assert "admin_limpar_cache" in tools
    assert "admin_status_sistema" in tools


def test_lista_tools_publico_so_info_publica():
    ctx = calcular_permissoes(role=RoleEnum.publico, status=StatusMatriculaEnum.ativo)
    tools = ctx.lista_tools_permitidas()
    assert "abrir_chamado_glpi" not in tools
    assert set(tools) <= {
        "consultar_calendario_academico", "consultar_edital_paes_2026",
        "consultar_contatos_uema", "consultar_wiki_ctic",
    }


def test_lista_tools_sem_duplicatas():
    ctx = calcular_permissoes(role=RoleEnum.admin, status=StatusMatriculaEnum.ativo)
    tools = ctx.lista_tools_permitidas()
    assert len(tools) == len(set(tools))
