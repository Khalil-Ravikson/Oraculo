import logging
from src.infrastructure.database.session import AsyncSessionLocal
from sqlalchemy import text

logger = logging.getLogger(__name__)

class GetUsersListUseCase:
    """
    Consulta os utilizadores diretamente na base de dados PostgreSQL.
    """
    async def executar(self, role_filter: str = "") -> list:
        try:
            async with AsyncSessionLocal() as session:
                # Prepara a query com ou sem filtro
                where_clause = "WHERE role = :role" if role_filter else ""
                query = text(f"""
                    SELECT id, nome, email, telefone, role, status, curso, criado_em 
                    FROM "Pessoas" 
                    {where_clause} 
                    ORDER BY id DESC LIMIT 100
                """)
                
                params = {"role": role_filter} if role_filter else {}
                result = await session.execute(query, params)
                
                # Formata a saída para JSON
                users = []
                for r in result.fetchall():
                    users.append({
                        "id": r.id,
                        "nome": r.nome or "—",
                        "email": r.email or "—",
                        "telefone": r.telefone or "—",
                        "role": r.role,
                        "status": r.status,
                        "curso": r.curso or "—",
                        "criado_em": str(r.criado_em)[:10] if r.criado_em else "—"
                    })
                return users
        except Exception as e:
            logger.error(f"❌ Erro ao buscar Utilizadores no Postgres: {e}")
            return []