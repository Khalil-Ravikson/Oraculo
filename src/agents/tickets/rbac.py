"""
src/agents/tickets/rbac.py
============================
Checagem de permissão compartilhada entre o funil de abertura de chamado
(ticket_flow.py) e a CRUD tool de teste (crud_tool.py) — item 4 da rodada de
testes: antes de qualquer operação de tickets/CRUD, checar
`ContextoPermissao.pode(Recurso.CHAMADO_GLPI)` e `pessoa.pode_abrir_chamado`,
mesmo quando o efeito final é só gravar um JSON local.
"""
from __future__ import annotations


async def checar_permissao_chamado(session_id: str) -> tuple[bool, str, dict | None]:
    """Retorna (autorizado, mensagem_se_bloqueado, pessoa_dict)."""
    from src.capabilities.persistence.pessoa_lookup import buscar_pessoa_por_telefone
    from src.domain.permissions import calcular_permissoes, Recurso
    from src.domain.entities.enums import RoleEnum, StatusMatriculaEnum
    from src.infrastructure.settings import settings

    pessoa = await buscar_pessoa_por_telefone(session_id)
    if not pessoa:
        if settings.DEV_TEST_SKIP_REGISTRATION:
            # Rodada de testes: sem cadastro real em `pessoas` (mesma flag que
            # libera o gatekeeper, ver process_message_task.py), então
            # sintetiza um usuário de teste permissivo em vez de bloquear —
            # o checklist do ticket/CRUD só vai perguntar tudo que falta.
            pessoa = {
                "nome": None, "email": None, "telefone": session_id,
                "matricula": None, "centro": None, "curso": None,
                "role": "estudante", "status": "ativo", "pode_abrir_chamado": True,
            }
        else:
            return False, "Não encontrei seu cadastro. Faça o cadastro primeiro. 📝", None

    ctx = calcular_permissoes(
        role=RoleEnum(pessoa["role"]),
        status=StatusMatriculaEnum(pessoa["status"]),
        nome_display=pessoa.get("nome") or "",
        centro=pessoa.get("centro"),
        curso=pessoa.get("curso"),
    )

    if not ctx.pode(Recurso.CHAMADO_GLPI):
        return False, ctx.mensagem_sem_permissao(Recurso.CHAMADO_GLPI), pessoa

    if not pessoa.get("pode_abrir_chamado", True):
        return False, (
            "Seu perfil está com a abertura de chamados/atualizações bloqueada pela administração. "
            "Se acredita ser um engano, fale com o CTIC. 😊"
        ), pessoa

    return True, "", pessoa
