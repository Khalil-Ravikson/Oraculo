# tests/e2e/insert_mock_student.py

import asyncio
import sys
import os
import uuid  # Precisamos gerar um ID único como string

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.infrastructure.database.session import AsyncSessionLocal
# Importando o modelo de banco correto!
from src.infrastructure.database.models import StudentModel

TELEFONE_VIP = "5598777777777" 

async def inserir_aluno():
    print("⏳ Conectando ao PostgreSQL...")
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        
        result = await session.execute(select(StudentModel).where(StudentModel.phone == TELEFONE_VIP))
        student = result.scalar_one_or_none()
        
        if student:
            print(f"⚠️ O aluno VIP ({TELEFONE_VIP}) já está no banco!")
            print(f"🧹 Removendo cadastro antigo do aluno VIP...")
            await session.delete(student)
            await session.commit()
        # Criando o aluno de acordo com o SEU modelo SQLAlchemy
        novo_aluno = StudentModel(
            id=str(uuid.uuid4()),  # Gera um ID único em string
            phone=TELEFONE_VIP,
            nome="Aluno Teste VIP",
            matricula="20240001234",
            is_guest=False,
            status="Ativo",
            llm_context={
                "curso": "Engenharia da Computação",
                "periodo": 11
            }
        )

        session.add(novo_aluno)
        await session.commit()
        print(f"✅ Aluno VIP '{novo_aluno.nome}' (Telefone: {TELEFONE_VIP}) inserido com sucesso na tabela students!")

if __name__ == "__main__":
    asyncio.run(inserir_aluno())