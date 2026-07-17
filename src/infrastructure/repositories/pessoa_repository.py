from __future__ import annotations

import re
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Importamos o modelo do banco (Infra) e o DTO (Domínio)
from src.infrastructure.database.models import Pessoa
from src.domain.entities.identidade import IdentidadeRica

class PessoaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── FLUXO CHATBOT / WEBHOOK ──────────────────────────────────────────────
    
    async def obter_identidade_por_telefone(self, telefone: str, chat_id: str) -> IdentidadeRica | None:
        """
        Busca a Pessoa no banco e converte IMEDIATAMENTE para IdentidadeRica.
        Usado exclusivamente pelo fluxo de atendimento do Celery/Router.
        """
        telefone_limpo = self._normalizar_telefone(telefone)
        result = await self._session.execute(
            select(Pessoa).where(Pessoa.telefone == telefone_limpo, Pessoa.deleted_at.is_(None))
        )
        pessoa_db = result.scalar_one_or_none()

        if not pessoa_db:
            return None

        return IdentidadeRica(
            user_id=pessoa_db.telefone,
            chat_id=chat_id,
            body="", # Injetado no webhook
            nome=pessoa_db.nome,
            role=pessoa_db.role.value if pessoa_db.role else None,
            status=pessoa_db.status.value if pessoa_db.status else None,
            is_admin=(pessoa_db.role.value == "admin") if pessoa_db.role else False,
            curso=pessoa_db.curso,
            periodo=pessoa_db.semestre_ingresso,
            matricula=pessoa_db.matricula,
            centro=pessoa_db.centro.value if getattr(pessoa_db, 'centro', None) else None
        )

    # ── FLUXO ADMIN / GESTÃO (Novos Métodos) ──────────────────────────────────

    async def get_by_id(self, pessoa_id: int) -> Pessoa | None:
        """Recupera uma pessoa específica pelo ID (ignora deletados)."""
        stmt = select(Pessoa).where(Pessoa.id == pessoa_id, Pessoa.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_paginated(
        self, 
        pagina: int = 1, 
        por_pag: int = 10, 
        busca: str = "", 
        role: str = "", 
        ativo: bool = None
    ) -> tuple[list[Pessoa], int]:
        """
        Lista pessoas com filtros dinâmicos e paginação.
        Resolve o erro de '6 positional arguments given'.
        """
        skip = (pagina - 1) * por_pag
        
        # 1. Query Base (ignora deletados)
        stmt = select(Pessoa).where(Pessoa.deleted_at.is_(None))

        # 2. Filtros Dinâmicos
        if busca:
            stmt = stmt.where(
                (Pessoa.nome.ilike(f"%{busca}%")) | 
                (Pessoa.telefone.contains(busca)) |
                (Pessoa.email.ilike(f"%{busca}%"))
            )
        
        if role:
            # Garante que compare com o valor do Enum se necessário
            stmt = stmt.where(Pessoa.role == role)
            
        if ativo is not None:
            stmt = stmt.where(Pessoa.is_active == ativo)

        # 3. Contagem Total (para paginação no frontend)
        from sqlalchemy import func
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_res = await self._session.execute(count_stmt)
        total = total_res.scalar_one()

        # 4. Execução com Limite e Offset
        stmt = stmt.offset(skip).limit(por_pag).order_by(Pessoa.nome.asc())
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        return rows, total

    async def criar_pessoa(self, dados: dict) -> Pessoa:
        """Cria um novo usuário (usado no onboarding ou painel Admin)."""
        if "email" in dados and dados["email"]:
            dados["email"] = dados["email"].lower().strip()
        if "telefone" in dados and dados["telefone"]:
            dados["telefone"] = self._normalizar_telefone(dados["telefone"])

        pessoa = Pessoa(**dados)
        self._session.add(pessoa)
        await self._session.flush() # Flush em vez de commit para suportar Unit of Work
        return pessoa

    async def update(self, pessoa: Pessoa) -> Pessoa:
        """Atualiza os dados de uma pessoa (a sessão SQLAlchemy rastreia o objeto)."""
        await self._session.flush()
        return pessoa

    async def delete_soft(self, pessoa: Pessoa) -> None:
        """Desativa a pessoa sem apagar do banco (Soft Delete)."""
        pessoa.deleted_at = datetime.now(timezone.utc)
        # Assumindo que você tenha um campo status ou is_active no seu modelo
        if hasattr(pessoa, 'is_active'):
            pessoa.is_active = False 
        await self._session.flush()

    async def telefone_existe(self, telefone: str) -> bool:
        """Verifica existência sem carregar o objeto completo (Otimizado)."""
        result = await self._session.execute(
            select(Pessoa.id).where(Pessoa.telefone == self._normalizar_telefone(telefone))
        )
        return result.scalar_one_or_none() is not None

    # ─────────────────────────────────────────────────────────────────────────────
    # Utilitários internos
    # ─────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _normalizar_telefone(telefone: str) -> str:
        """Normaliza número de telefone para formato padrão."""
        if not telefone:
            return telefone
        return re.sub(r"\D", "", telefone)