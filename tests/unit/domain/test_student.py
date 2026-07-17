# tests/unit/domain/test_student.py
import pytest
from pydantic import ValidationError
from src.domain.entities.identidade import IdentidadeRica

def test_criar_identidade_rica_valida():
    aluno = IdentidadeRica(
        user_id="559899999999",
        chat_id="559899999999",
        nome="Khalil",
        role="student",
        status="ativo",
        matricula="20200036520",
        curso="Engenharia da Computação",
        periodo="11"
    )
    
    assert aluno.nome == "Khalil"
    assert aluno.matricula == "20200036520"
    assert aluno.status == "ativo"
    assert aluno.role == "student"
    assert aluno.contexto_llm["curso"] == "Engenharia da Computação"

def test_matricula_invalida_deve_falhar():
    with pytest.raises(ValidationError) as exc_info:
        IdentidadeRica(
            user_id="559899999999",
            chat_id="559899999999",
            nome="Aluno Teste",
            role="student",
            status="ativo",
            matricula="19990036520"  # Ano inválido/fora do padrão (não começa com 20 e 11 digitos)
        )
    
    assert "A matrícula deve conter 11 dígitos" in str(exc_info.value)

def test_matricula_curta_deve_falhar():
    with pytest.raises(ValidationError) as exc_info:
        IdentidadeRica(
            user_id="559899999999",
            chat_id="559899999999",
            nome="Aluno Teste",
            role="student",
            status="ativo",
            matricula="2020123"  # Faltam dígitos
        )
    
    assert "A matrícula deve conter 11 dígitos" in str(exc_info.value)

def test_guest_cannot_access_tools():
    guest = IdentidadeRica(
        user_id="559888888888",
        chat_id="559888888888",
        nome="Visitante",
        role="guest",
        status="ativo",
        matricula="20240000000"
    )
    assert guest.pode_usar_tools is False