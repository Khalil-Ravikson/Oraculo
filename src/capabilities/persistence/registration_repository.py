"""
src/capabilities/persistence/registration_repository.py
===========================================================
Ex SQL cru embutido em `application/routing/registration_funnel.py` (Fase 6
do PLANO_REFATORACAO_SUPERVISOR.md, seção 2.6). Função async pura, sem
decisão de negócio — só a gravação em `pessoas`.

Sprint 3 (Fase 0) — dois bugs corrigidos aqui:

1. `email` é `NOT NULL`/`UNIQUE` em `pessoas` (migration 004) e o INSERT
   original não preenchia essa coluna — todo cadastro de número novo
   quebrava com `IntegrityError`, engolida pelo chamador
   (`RegistrationFunnel._salvar_usuario`), fazendo o bot confirmar um
   cadastro que nunca foi persistido. Corrigido gerando um e-mail sintético
   determinístico por telefone (`{telefone}@whatsapp.oraculo.local`),
   suficiente para satisfazer a constraint sem exigir e-mail real de quem
   se cadastra só pelo WhatsApp.
2. `ON CONFLICT DO UPDATE` não promovia `status` para `'ativo'` nem
   respeitava um `role` pré-atribuído pela lista de inscrição/whitelist
   administrativa (`status='pendente'`, ex. pré-cadastro da secretaria) —
   quem já estava pré-cadastrado nunca saía do funil de registro. Corrigido:
   o UPDATE sempre promove `status='ativo'` e preserva o `role` existente
   (só rebaixa para `'estudante'` se o valor pré-existente for o default
   `'publico'`) — nunca sobrescreve o e-mail real de um pré-cadastro.
"""
from __future__ import annotations


async def salvar_pessoa(telefone: str, nome: str, curso: str) -> None:
    from src.infrastructure.database.session import AsyncSessionLocal
    from sqlalchemy import text

    email_sintetico = f"{telefone}@whatsapp.oraculo.local"

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO pessoas (telefone, nome, email, curso, role, status)
                VALUES (:tel, :nome, :email, :curso, 'estudante', 'ativo')
                ON CONFLICT (telefone) DO UPDATE
                SET nome   = EXCLUDED.nome,
                    curso  = EXCLUDED.curso,
                    status = 'ativo',
                    role   = CASE WHEN pessoas.role = 'publico' THEN 'estudante' ELSE pessoas.role END
            """),
            {"tel": telefone, "nome": nome, "email": email_sintetico, "curso": curso},
        )
        await db.commit()
