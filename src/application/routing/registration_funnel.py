"""
RegistrationFunnel — captura nome e curso via conversa.

Estados no Redis (TTL 10min):
  register:mode:{sender}  = "1"
  register:step:{sender}  = "awaiting_name" | "awaiting_course"
  register:name:{sender}  = <nome capturado>
"""
from __future__ import annotations
import logging, re
logger = logging.getLogger(__name__)

_BOAS_VINDAS = (
    "👋 Olá! Para usar o *Oráculo UEMA*, preciso de alguns dados.\n\n"
    "Qual é o seu *nome completo*?"
)
_PERGUNTA_CURSO = "Perfeito, {nome}! Qual é o seu *curso* na UEMA?"
_CADASTRO_OK = (
    "✅ Cadastro realizado! Bem-vindo(a), *{nome}*!\n\n"
    "Agora pode perguntar sobre calendário, editais, contatos ou suporte."
)


class RegistrationFunnel:

    async def process(self, sender: str, text: str, push_name: str, redis) -> str:
        """
        Retorna a próxima mensagem a enviar, ou '' se o fluxo terminou.
        """
        step = redis.get(f"register:step:{sender}") or "start"

        if step == "start":
            redis.setex(f"register:mode:{sender}", 600, "1")
            redis.setex(f"register:step:{sender}", 600, "awaiting_name")
            return _BOAS_VINDAS

        if step == "awaiting_name":
            nome = text.strip().title()
            if len(nome) < 3:
                return "Por favor, informe seu nome completo."
            redis.setex(f"register:name:{sender}", 600, nome)
            redis.setex(f"register:step:{sender}", 600, "awaiting_course")
            return _PERGUNTA_CURSO.format(nome=nome.split()[0])

        if step == "awaiting_course":
            nome  = redis.get(f"register:name:{sender}") or push_name or "Aluno"
            curso = text.strip().title()
            if len(curso) < 3:
                return "Por favor, informe o nome do seu curso."

            await self._salvar_usuario(sender, nome, curso)
            
            # Limpa estado de registro
            for key in (f"register:mode:{sender}",
                        f"register:step:{sender}",
                        f"register:name:{sender}"):
                redis.delete(key)

            return _CADASTRO_OK.format(nome=nome.split()[0])

        return ""

    @staticmethod
    async def _salvar_usuario(telefone: str, nome: str, curso: str) -> None:
        try:
            from src.infrastructure.database.session import AsyncSessionLocal
            from sqlalchemy import text
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("""
                        INSERT INTO pessoas (telefone, nome, curso, role, status)
                        VALUES (:tel, :nome, :curso, 'estudante', 'ativo')
                        ON CONFLICT (telefone) DO UPDATE
                        SET nome=EXCLUDED.nome, curso=EXCLUDED.curso
                    """),
                    {"tel": telefone, "nome": nome, "curso": curso},
                )
                await db.commit()
            logger.info("✅ Usuário cadastrado via funil: %s", telefone[-6:])
        except Exception as e:
            logger.error("❌ RegistrationFunnel._salvar_usuario: %s", e)