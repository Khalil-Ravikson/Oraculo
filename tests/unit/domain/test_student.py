import pytest
from pydantic import ValidationError
from src.domain.entities.student import Student

def test_criar_estudante_valido():
    aluno = Student(
        phone="559899999999",
        nome="Khalil",
        matricula="20200036520",
        llm_context={"curso": "Engenharia da Computação", "periodo": 11}
    )
    
    assert aluno.nome == "Khalil"
    assert aluno.matricula == "20200036520"
    assert aluno.status == "Ativo"
    assert aluno.is_guest is False
    assert aluno.llm_context["curso"] == "Engenharia da Computação"

def test_matricula_invalida_deve_falhar():
    with pytest.raises(ValidationError) as exc_info:
        Student(
            phone="559899999999",
            nome="Aluno Teste",
            matricula="19990036520" # Ano inválido/fora do padrão
        )
    
    assert "A matrícula deve conter 11 dígitos" in str(exc_info.value)

def test_matricula_curta_deve_falhar():
    with pytest.raises(ValidationError) as exc_info:
        Student(
            phone="559899999999",
            nome="Aluno Teste",
            matricula="2020123" # Faltam dígitos
        )
    
    assert "A matrícula deve conter 11 dígitos" in str(exc_info.value)

def test_guest_cannot_access_tools():
    guest = Student(
        phone="559888888888",
        nome="Visitante",
        matricula="20240000000",
        is_guest=True
    )
    assert guest.can_access_tools() is False