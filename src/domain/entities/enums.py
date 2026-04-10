# src/domain/entities/enums.py
import enum

class RoleEnum(str, enum.Enum):
    publico     = "publico"
    estudante   = "estudante"
    servidor    = "servidor"
    professor   = "professor"
    coordenador = "coordenador"
    admin       = "admin"

class CentroEnum(str, enum.Enum):
    CECEN  = "CECEN"
    CESB   = "CESB"
    CESC   = "CESC"
    CCSA   = "CCSA"
    CEEA   = "CEEA"
    CCS    = "CCS"
    CCT    = "CCT"
    CESBA  = "CESBA"
    OUTRO  = "OUTRO"

class StatusMatriculaEnum(str, enum.Enum):
    ativo    = "ativo"
    inativo  = "inativo"
    trancado = "trancado"
    pendente = "pendente"