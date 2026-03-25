from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Boolean, JSON

class Base(DeclarativeBase):
    pass

class StudentModel(Base):
    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    phone: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    nome: Mapped[str] = mapped_column(String, nullable=False)
    matricula: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="Ativo")
    
    # O JSON Type salva o nosso llm_context perfeitamente no PostgreSQL
    llm_context: Mapped[dict] = mapped_column(JSON, default=dict)