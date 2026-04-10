"""
src/infrastructure/repositories/pessoa_repository.py
=========================================================================
Repositório unificado de acesso ao banco (PostgreSQL).
Aplica a Regra 7: O banco devolve 'IdentidadeRica' (DTO em RAM), 
blindando o LangGraph de instâncias bloqueantes do SQLAlchemy.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Importamos o modelo do banco (Infra) e o DTO (Domínio)
from src.infrastructure.database.models import Pessoa
from src.domain.entities.identidade import IdentidadeRica

class PessoaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── MÁGICA DA CLEAN ARCHITECTURE (A Ponte) ───────────────────────────────

    async def obter_identidade_por_telefone(self, telefone: str, chat_id: str) -> IdentidadeRica | None:
        """
        Busca a Pessoa no banco e a converte IMEDIATAMENTE para IdentidadeRica.
        É esse objeto que viaja pelo Celery e LangGraph.
        """
        telefone_limpo = _normalizar_telefone(telefone)
        result = await self._session.execute(
            select(Pessoa).where(Pessoa.telefone == telefone_limpo)
        )
        pessoa_db = result.scalar_one_or_none()

        if not pessoa_db:
            return None

        return IdentidadeRica(
            user_id=pessoa_db.telefone,
            chat_id=chat_id,
            body="", # Injetado no webhook
            nome=pessoa_db.nome,
            role=pessoa_db.role.value,       # Enum para String
            status=pessoa_db.status.value,   # Enum para String
            is_admin=pessoa_db.role.value == "admin",
            curso=pessoa_db.curso,
            periodo=pessoa_db.semestre_ingresso,
            matricula=pessoa_db.matricula,
            centro=pessoa_db.centro.value if pessoa_db.centro else None
        )

    # ── Escrita e Consultas Base ──────────────────────────────────────────────

    async def criar_pessoa(self, dados: dict) -> Pessoa:
        """Cria um novo usuário (usado no final do fluxo de onboarding)."""
        if "email" in dados:
            dados["email"] = dados["email"].lower().strip()
        if "telefone" in dados:
            dados["telefone"] = _normalizar_telefone(dados["telefone"])

        pessoa = Pessoa(**dados)
        self._session.add(pessoa)
        await self._session.commit()
        await self._session.refresh(pessoa)
        return pessoa

    async def telefone_existe(self, telefone: str) -> bool:
        """Verifica existência sem carregar o objeto completo (Otimizado)."""
        result = await self._session.execute(
            select(Pessoa.id).where(Pessoa.telefone == _normalizar_telefone(telefone))
        )
        return result.scalar_one_or_none() is not None

# ─────────────────────────────────────────────────────────────────────────────
# Utilitários internos
# ─────────────────────────────────────────────────────────────────────────────
def _normalizar_telefone(telefone: str) -> str:
    """Normaliza número de telefone para formato padrão (ex: 5598989123456)"""
    if not telefone:
        return telefone
    import re
    return re.sub(r"\D", "", telefone)