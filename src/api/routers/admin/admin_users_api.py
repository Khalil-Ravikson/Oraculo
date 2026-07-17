"""
Endpoints de Gestão de Usuários (CRUD) — Oráculo UEMA
=======================================================
Router FastAPI para criar, listar, editar e remover usuários.
Usa o padrão Clean Architecture (UseCase -> Repository).

Montar em admin_api.py:
    from src.api.admin_users_api import router as users_router
    app.include_router(users_router, prefix="/api/admin/users", tags=["Usuários"])
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

# Imports de Infraestrutura e Autenticação
from src.infrastructure.database.session import AsyncSessionLocal
from src.infrastructure.database.models import Pessoa
from src.api.middleware.auth_middleware import require_admin_jwt, TokenPayload

# Imports de Domínio/Aplicação
from src.infrastructure.repositories.pessoa_repository import PessoaRepository
from src.application.use_cases.user_use_case import UserUseCase, UserResult
logger = logging.getLogger(__name__)

router = APIRouter()

# ──────────────────────────────────────────────
# Gerenciamento de Dependências (Injeção)
# ──────────────────────────────────────────────

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

def get_use_case(db: AsyncSession = Depends(get_db)) -> UserUseCase:
    return UserUseCase(PessoaRepository(db))

def map_error(result: UserResult):
    """Mapeia os códigos de erro do Domínio para status HTTP do FastAPI."""
    status_map = {
        "INVALID_INPUT": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "CONFLICT": status.HTTP_409_CONFLICT,
        "NOT_FOUND": status.HTTP_404_NOT_FOUND
    }
    http_status = status_map.get(result.error_code, status.HTTP_400_BAD_REQUEST)
    raise HTTPException(status_code=http_status, detail=result.error)

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
    turno:      Optional[str] = None
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
    """Payload para atualização parcial de usuário."""
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
    criado_em:     Optional[datetime]

    model_config = {"from_attributes": True}


class UserListOut(BaseModel):
    total:    int
    pagina:   int
    por_pag:  int
    usuarios: list[UserOut]

# ──────────────────────────────────────────────
# Utilitário de Serialização
# ──────────────────────────────────────────────

def _serialize_to_schema(p: Pessoa) -> dict:
    """Converte o objeto do SQLAlchemy num dict compatível com UserOut."""
    # Como a sua modelagem base usa 'is_active' ou 'verificado', normalizamos para 'ativo'
    ativo = getattr(p, 'ativo', getattr(p, 'is_active', getattr(p, 'verificado', True)))
    
    return {
        "id": p.id,
        "nome": p.nome,
        "telefone": p.telefone,
        "email": p.email,
        "role": p.role.value if hasattr(p.role, 'value') else p.role,
        "curso": p.curso,
        "matricula": p.matricula,
        "turno": getattr(p, 'turno', None),
        "ativo": ativo,
        "criado_em": p.criado_em,
    }

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
    uc: UserUseCase = Depends(get_use_case),
):
    """Lista todos os usuários com paginação e filtros."""
    r = await uc.listar(pagina, por_pag, busca or "", role or "", ativo)
    
    items = r.data["items"]
    logger.info("listar_usuarios: %d encontrados (pág %d)", r.data["total"], pagina)
    
    return UserListOut(
        total=r.data["total"],
        pagina=pagina,
        por_pag=por_pag,
        usuarios=[UserOut.model_validate(_serialize_to_schema(u)) for u in items],
    )

@router.get("/{user_id}", response_model=UserOut, summary="Buscar usuário por ID")
async def buscar_usuario(
    user_id: int,
    _: TokenPayload = Depends(require_admin_jwt),
    uc: UserUseCase = Depends(get_use_case),
):
    """Retorna um usuário pelo ID."""
    r = await uc.buscar(user_id)
    if not r.ok:
        map_error(r)
    return UserOut.model_validate(_serialize_to_schema(r.data))

@router.post("/", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Criar usuário")
async def criar_usuario(
    payload: UserCreate,
    _: TokenPayload = Depends(require_admin_jwt),
    uc: UserUseCase = Depends(get_use_case),
):
    """
    Cria um novo usuário no banco de dados.
    O telefone deve ser único. Role padrão: estudante.
    """
    # Converter o payload Pydantic para dicionário
    r = await uc.criar(payload.model_dump())
    if not r.ok:
        map_error(r)
        
    user = r.data
    logger.info("criar_usuario: ID=%d nome='%s'", user.id, user.nome)
    return UserOut.model_validate(_serialize_to_schema(user))

@router.put("/{user_id}", response_model=UserOut, summary="Atualizar usuário")
async def atualizar_usuario(
    user_id: int,
    payload: UserUpdate,
    _: TokenPayload = Depends(require_admin_jwt),
    uc: UserUseCase = Depends(get_use_case),
):
    """Atualiza campos do usuário (somente os informados no body)."""
    # exclude_unset=True ignora os valores opcionais não passados
    campos = payload.model_dump(exclude_unset=True)
    if not campos:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nenhum campo para atualizar.")

    r = await uc.atualizar(user_id, campos)
    if not r.ok:
        map_error(r)
        
    logger.info("atualizar_usuario: ID=%d campos=%s", user_id, list(campos.keys()))
    return UserOut.model_validate(_serialize_to_schema(r.data))

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remover usuário")
async def remover_usuario(
    user_id: int,
    _: TokenPayload = Depends(require_admin_jwt),
    uc: UserUseCase = Depends(get_use_case),
):
    """Remove um usuário."""
    r = await uc.deletar(user_id)
    if not r.ok:
        map_error(r)
    logger.warning("remover_usuario: ID=%d REMOVIDO", user_id)

@router.patch("/{user_id}/toggle", response_model=UserOut, summary="Ativar/Desativar usuário")
async def toggle_usuario(
    user_id: int,
    _: TokenPayload = Depends(require_admin_jwt),
    uc: UserUseCase = Depends(get_use_case),
):
    """Alterna o status ativo/inativo do usuário."""
    r = await uc.toggle(user_id)
    if not r.ok:
        map_error(r)
        
    ativo = getattr(r.data, 'ativo', getattr(r.data, 'is_active', True))
    acao = "ATIVADO" if ativo else "DESATIVADO"
    logger.info("toggle_usuario: ID=%d %s", user_id, acao)
    
    return UserOut.model_validate(_serialize_to_schema(r.data))