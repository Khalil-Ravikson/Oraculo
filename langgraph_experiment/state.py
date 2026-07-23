from __future__ import annotations

from typing import TypedDict


class OraculoState(TypedDict, total=False):
    session_id: str
    message: str
    route: str          # "rag" | "ticket"
    answer: str
    ticket_data: dict
