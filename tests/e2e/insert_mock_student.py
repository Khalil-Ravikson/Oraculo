# tests/e2e/insert_mock_student.py
# lembrar de uvicorn src.main:app --reload

import asyncio
import logging
import sys

# Ajusta o path do Python para ele achar a pasta 'src'
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.infrastructure.database.session import AsyncSessionLocal
from src.infrastructure.database.models import StudentModel

# Número de teste que VAMOS liberar
# Diferente do "999999999" (Guest) e do "888888888" (Spam)
TELEFONE_VIP = "5598777777777" 

async def inserir_aluno():
    print("⏳ Conectando ao PostgreSQL...")
    async with AsyncSessionLocal() as session:
        # 1. Verifica se já existe
        from sqlalchemy import select
        result = await session.execute(select(StudentModel).where(StudentModel.telefone == TELEFONE_VIP))
        student = result.scalar_one_or_none()
        
        if student:
            print(f"⚠️ O aluno VIP ({TELEFONE_VIP}) já está no banco!")
            return

        # 2. Cria o Aluno Fake
        novo_aluno = StudentModel(
            telefone=TELEFONE_VIP,
            nome="Aluno Teste VIP",
            matricula="2024123456",  # Formato válido UEMA
            email="alunoteste@aluno.uema.br",
            curso="Engenharia da Computação",
            periodo=11,
            status="Ativo"
        )

        # 3. Salva no banco
        session.add(novo_aluno)
        await session.commit()
        print(f"✅ Aluno VIP '{novo_aluno.nome}' (Telefone: {TELEFONE_VIP}) inserido com sucesso!")

if __name__ == "__main__":
    # Roda a função assíncrona
    asyncio.run(inserir_aluno())