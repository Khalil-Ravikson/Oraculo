"""
Endpoints de Gestão de Usuários (CRUD) — Oráculo UEMA
=======================================================
Router FastAPI para criar, listar, editar e remover usuários no PostgreSQL.

Montar em admin_api.py:
    from src.api.admin_users_api import router as users_router
    app.include_router(users_router, prefix="/api/admin/users", tags=["Usuários"])
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

# Adapte esses imports conforme sua estrutura de infra
from src.infrastructure.database import get_async_session
from src.infrastructure.models.user import UserModel          # modelo SQLAlchemy
from src.api.middleware.auth_middleware import require_admin_jwt, TokenPayload

logger = logging.getLogger(__name__)

router = APIRouter()


# ──────────────────────────────────────────────
# Schemas Pydantic
# ──────────────────────────────────────────────

class UserCreate(BaseModel):
    """Payload para criação de usuário."""
    nome:       str
    telefone:   str
    email:      Optional[str] = None
    role:       str = "estudante"
    curso:      Optional[str] = None
    matricula:  Optional[str] = None
    turno:      Optional[str] = None   # matutino | vespertino | noturno
    ativo:      bool = True

    @field_validator("role")
    @classmethod
    def role_valida(cls, v: str) -> str:
        roles = {"publico", "estudante", "professor", "servidor", "coordenador", "admin"}
        if v not in roles:
            raise ValueError(f"Role inválida. Use: {', '.join(sorted(roles))}")
        return v

    @field_validator("telefone")
    @classmethod
    def telefone_formato(cls, v: str) -> str:
        v = "".join(c for c in v if c.isdigit())
        if len(v) < 10:
            raise ValueError("Telefone deve ter pelo menos 10 dígitos.")
        return v

    @field_validator("turno")
    @classmethod
    def turno_valido(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        turnos = {"matutino", "vespertino", "noturno", "ead"}
        if v not in turnos:
            raise ValueError(f"Turno inválido. Use: {', '.join(sorted(turnos))}")
        return v


class UserUpdate(BaseModel):
    """Payload para atualização parcial de usuário (todos os campos opcionais)."""
    nome:       Optional[str]  = None
    email:      Optional[str]  = None
    role:       Optional[str]  = None
    curso:      Optional[str]  = None
    matricula:  Optional[str]  = None
    turno:      Optional[str]  = None
    ativo:      Optional[bool] = None

    @field_validator("role")
    @classmethod
    def role_valida(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        roles = {"publico", "estudante", "professor", "servidor", "coordenador", "admin"}
        if v not in roles:
            raise ValueError(f"Role inválida. Use: {', '.join(sorted(roles))}")
        return v


class UserOut(BaseModel):
    """Resposta de usuário (sem dados sensíveis)."""
    id:            int
    nome:          str
    telefone:      str
    email:         Optional[str]
    role:          str
    curso:         Optional[str]
    matricula:     Optional[str]
    turno:         Optional[str]
    ativo:         bool
    criado_em:     datetime
    atualizado_em: Optional[datetime]

    model_config = {"from_attributes": True}


class UserListOut(BaseModel):
    total:    int
    pagina:   int
    por_pag:  int
    usuarios: list[UserOut]


# ──────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────

@router.get("/", response_model=UserListOut, summary="Listar usuários")
async def listar_usuarios(
    pagina:   int = Query(1, ge=1, description="Número da página"),
    por_pag:  int = Query(20, ge=1, le=100, description="Usuários por página"),
    busca:    Optional[str]  = Query(None, description="Filtrar por nome, telefone ou email"),
    role:     Optional[str]  = Query(None, description="Filtrar por role"),
    ativo:    Optional[bool] = Query(None, description="Filtrar por status"),
    _: TokenPayload = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_async_session),
) -> UserListOut:
    """Lista todos os usuários com paginação e filtros."""
    q = select(UserModel)

    if busca:
        like = f"%{busca}%"
        q = q.where(
            UserModel.nome.ilike(like)
            | UserModel.telefone.ilike(like)
            | UserModel.email.ilike(like)
        )
    if role:
        q = q.where(UserModel.role == role)
    if ativo is not None:
        q = q.where(UserModel.ativo == ativo)

    # Contagem total
    count_q = select(func.count()).select_from(q.subquery())
    total   = (await db.execute(count_q)).scalar_one()

    # Paginação
    offset   = (pagina - 1) * por_pag
    result   = await db.execute(q.offset(offset).limit(por_pag).order_by(UserModel.id))
    usuarios = result.scalars().all()

    logger.info("listar_usuarios: %d encontrados (pág %d)", total, pagina)
    return UserListOut(
        total=total,
        pagina=pagina,
        por_pag=por_pag,
        usuarios=[UserOut.model_validate(u) for u in usuarios],
    )


@router.get("/{user_id}", response_model=UserOut, summary="Buscar usuário por ID")
async def buscar_usuario(
    user_id: int,
    _: TokenPayload = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_async_session),
) -> UserOut:
    """Retorna um usuário pelo ID."""
    user = await db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    return UserOut.model_validate(user)


@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Criar usuário")
async def criar_usuario(
    payload: UserCreate,
    _: TokenPayload = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_async_session),
) -> UserOut:
    """
    Cria um novo usuário no banco de dados.
    O telefone deve ser único. Role padrão: estudante.
    """
    # Verificar se telefone já existe
    existing = await db.execute(
        select(UserModel).where(UserModel.telefone == payload.telefone)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Telefone {payload.telefone} já cadastrado.",
        )

    user = UserModel(
        nome=payload.nome,
        telefone=payload.telefone,
        email=payload.email,
        role=payload.role,
        curso=payload.curso,
        matricula=payload.matricula,
        turno=payload.turno,
        ativo=payload.ativo,
        criado_em=datetime.utcnow(),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info("criar_usuario: ID=%d nome='%s' role=%s", user.id, user.nome, user.role)
    return UserOut.model_validate(user)


@router.put("/{user_id}", response_model=UserOut, summary="Atualizar usuário")
async def atualizar_usuario(
    user_id: int,
    payload: UserUpdate,
    _: TokenPayload = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_async_session),
) -> UserOut:
    """Atualiza campos do usuário (somente os informados no body)."""
    user = await db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    campos = payload.model_dump(exclude_none=True)
    if not campos:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nenhum campo para atualizar.")

    campos["atualizado_em"] = datetime.utcnow()

    await db.execute(
        update(UserModel).where(UserModel.id == user_id).values(**campos)
    )
    await db.commit()
    await db.refresh(user)

    logger.info("atualizar_usuario: ID=%d campos=%s", user_id, list(campos.keys()))
    return UserOut.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remover usuário")
async def remover_usuario(
    user_id: int,
    _: TokenPayload = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_async_session),
) -> None:
    """Remove permanentemente um usuário. Use com cautela."""
    user = await db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    await db.execute(delete(UserModel).where(UserModel.id == user_id))
    await db.commit()
    logger.warning("remover_usuario: ID=%d nome='%s' REMOVIDO", user_id, user.nome)


@router.patch("/{user_id}/toggle", response_model=UserOut, summary="Ativar/Desativar usuário")
async def toggle_usuario(
    user_id: int,
    _: TokenPayload = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_async_session),
) -> UserOut:
    """Alterna o status ativo/inativo do usuário sem removê-lo."""
    user = await db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    novo_status = not user.ativo
    await db.execute(
        update(UserModel)
        .where(UserModel.id == user_id)
        .values(ativo=novo_status, atualizado_em=datetime.utcnow())
    )
    await db.commit()
    await db.refresh(user)

    acao = "ATIVADO" if novo_status else "DESATIVADO"
    logger.info("toggle_usuario: ID=%d %s", user_id, acao)
    return UserOut.model_validate(user)