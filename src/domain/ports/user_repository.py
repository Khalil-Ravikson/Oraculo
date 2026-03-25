from abc import ABC, abstractmethod
from typing import Optional
from src.domain.entities.student import Student

class IUserRepository(ABC):
    
    @abstractmethod
    async def get_by_phone(self, phone: str) -> Optional[Student]:
        """Busca um aluno pelo número de WhatsApp."""
        pass

    @abstractmethod
    async def create_student(self, student: Student) -> Student:
        """Registra um novo aluno (Fluxo de Onboarding)."""
        pass
        
    @abstractmethod
    async def update_student(self, phone: str, update_data: dict) -> Student:
        """Atualiza dados do aluno."""
        pass