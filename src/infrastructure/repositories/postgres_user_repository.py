import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.domain.entities.student import Student
from src.domain.ports.user_repository import IUserRepository
from src.infrastructure.database.models import StudentModel

class PostgresUserRepository(IUserRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_phone(self, phone: str) -> Optional[Student]:
        stmt = select(StudentModel).where(StudentModel.phone == phone)
        result = await self.session.execute(stmt)
        db_student = result.scalar_one_or_none()
        
        if not db_student:
            return None
            
        # Magia da Clean Architecture: Convertendo Infra para Domínio
        return Student(
            id=db_student.id,
            phone=db_student.phone,
            nome=db_student.nome,
            matricula=db_student.matricula,
            is_guest=db_student.is_guest,
            status=db_student.status,
            llm_context=db_student.llm_context
        )

    async def create_student(self, student: Student) -> Student:
        new_id = str(uuid.uuid4())
        db_student = StudentModel(
            id=new_id,
            phone=student.phone,
            nome=student.nome,
            matricula=student.matricula,
            is_guest=student.is_guest,
            status=student.status,
            llm_context=student.llm_context
        )
        self.session.add(db_student)
        await self.session.commit()
        await self.session.refresh(db_student)
        
        student.id = db_student.id
        return student

    async def update_student(self, phone: str, update_data: dict) -> Student:
        # Implementação futura do CRUD via dicionário
        pass