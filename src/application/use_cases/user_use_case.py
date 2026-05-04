from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Any
import re

# Importa o repositório unificado
from src.infrastructure.repositories.pessoa_repository import PessoaRepository

@dataclass
class UserResult:
    ok: bool
    data: Any = None
    error: str = ""
    # Códigos de Domínio para facilitar o tratamento no Frontend/API
    error_code: str = "SUCCESS" 

class UserUseCase:
    def __init__(self, repo: PessoaRepository):
        self._repo = repo

    async def criar(self, dados: dict) -> UserResult:
        tel = dados.get("telefone", "")
        tel = re.sub(r"\D", "", tel)
        if len(tel) < 10:
            return UserResult(ok=False, error="Telefone inválido.", error_code="INVALID_INPUT")
        
        dados["telefone"] = tel
        
        # O repositório já tem o método otimizado telefone_existe
        if await self._repo.telefone_existe(tel):
            return UserResult(ok=False, error=f"Telefone {tel} já cadastrado.", error_code="CONFLICT")
        
        # === TRADUÇÃO DE CONTRATO (FRONTEND -> BANCO) ===
        # O frontend envia 'ativo' (checkbox), mas a entidade do banco espera 'is_active'
        if "ativo" in dados:
            dados["is_active"] = dados.pop("ativo")
        # ================================================

        # Agora passamos os dados limpos e traduzidos para o repositório
        p = await self._repo.criar_pessoa(dados)
        
        # O Use Case comanda a transação
        await self._repo._session.commit()
        return UserResult(ok=True, data=p)

    async def listar(self, pagina: int, por_pag: int, busca="", role="", ativo=None) -> UserResult:
        # Resolve o erro anterior de múltiplos argumentos posicionais
        rows, total = await self._repo.list_paginated(pagina, por_pag, busca, role, ativo)
        return UserResult(ok=True, data={"items": rows, "total": total})

    async def buscar(self, id: int) -> UserResult:
        p = await self._repo.get_by_id(id)
        if not p:
            return UserResult(ok=False, error="Usuário não encontrado.", error_code="NOT_FOUND")
        return UserResult(ok=True, data=p)

    async def atualizar(self, id: int, dados: dict) -> UserResult:
        p = await self._repo.get_by_id(id)
        if not p:
            return UserResult(ok=False, error="Usuário não encontrado.", error_code="NOT_FOUND")
        
        # Validação de segurança para troca de telefone
        if "telefone" in dados:
            tel = re.sub(r"\D", "", dados["telefone"])
            if len(tel) < 10:
                return UserResult(ok=False, error="Telefone inválido.", error_code="INVALID_INPUT")
            
            if tel != p.telefone and await self._repo.telefone_existe(tel):
                return UserResult(ok=False, error=f"Telefone {tel} já pertence a outro usuário.", error_code="CONFLICT")
            dados["telefone"] = tel

        # Aplicação dinâmica dos dados na entidade SQLAlchemy
        for k, v in dados.items():
            setattr(p, k, v)
            
        p_atualizado = await self._repo.update(p)
        await self._repo._session.commit()
        
        return UserResult(ok=True, data=p_atualizado)

    async def deletar(self, id: int) -> UserResult:
        p = await self._repo.get_by_id(id)
        if not p:
            return UserResult(ok=False, error="Usuário não encontrado.", error_code="NOT_FOUND")
            
        # Executa o Soft Delete definido no repositório
        await self._repo.delete_soft(p)
        await self._repo._session.commit()
        
        return UserResult(ok=True)

    async def toggle(self, id: int) -> UserResult:
        """Inverte o estado de ativação do usuário."""
        p = await self._repo.get_by_id(id)
        if not p:
            return UserResult(ok=False, error="Usuário não encontrado.", error_code="NOT_FOUND")
            
        if hasattr(p, 'is_active'):
            p.is_active = not p.is_active
            
        p_atualizado = await self._repo.update(p)
        await self._repo._session.commit()
        
        return UserResult(ok=True, data=p_atualizado)