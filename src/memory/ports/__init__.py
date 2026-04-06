"""
src/memory/ports/__init__.py
Portas (interfaces) do sistema de memória.
"""
from .working_memory_port import IWorkingMemory, ConversationTurn, HistoricoCompactado
from .long_term_port import ILongTermMemory, Fato
from .menu_state_port import IMenuStateRepository
from .fact_extractor_port import IFactExtractor

__all__ = [
    "IWorkingMemory",
    "ConversationTurn",
    "HistoricoCompactado",
    "ILongTermMemory",
    "Fato",
    "IMenuStateRepository",
    "IFactExtractor",
]