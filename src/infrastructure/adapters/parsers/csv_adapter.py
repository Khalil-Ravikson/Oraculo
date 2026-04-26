"""
src/infrastructure/adapters/parsers/csv_adapter.py
Adaptador semântico para CSV. Transforma linhas em blocos de texto ricos para RAG.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from typing import BinaryIO

from src.domain.ports.document_parser import IDocumentParser

logger = logging.getLogger(__name__)

class CsvAdapter(IDocumentParser):
    """
    Transforma dados tabulares brutos em sentenças semânticas.
    Exemplo:
    Ao invés de: PROG,Pró-Reitor de Graduação,Prof. Dr. Fulano Silva
    Gera:
    - SETOR: PROG
    - CARGO: Pró-Reitor de Graduação
    - NOME: Prof. Dr. Fulano Silva
    """

    def extract_text(self, file_stream: BinaryIO, **kwargs) -> str:
        # Tenta ler com UTF-8, faz fallback para latin-1 (muito comum em planilhas BR)
        raw_bytes = file_stream.read()
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content = raw_bytes.decode("latin-1")

        # Lê o CSV interpretando a primeira linha como as Chaves (Headers)
        reader = csv.DictReader(io.StringIO(content))
        
        blocks = []
        for row_idx, row in enumerate(reader, start=1):
            parts = [f"### Registro {row_idx}"]
            for header, value in row.items():
                # Ignora colunas vazias
                if header and value and value.strip():
                    parts.append(f"- **{header.strip().upper()}**: {value.strip()}")
            
            # Se a linha teve dados, junta tudo e adiciona aos blocos
            if len(parts) > 1:
                blocks.append("\n".join(parts))

        logger.info("✅ CsvAdapter formatou %d registros semânticos.", len(blocks))
        
        # O separador de duas quebras de linha garante que o RecursiveCharacterTextSplitter
        # tente manter cada registro inteiro no mesmo chunk
        return "\n\n".join(blocks)

    def parse(self, file_path: str, instruction: str = "") -> str:
        if not os.path.exists(file_path):
            logger.error("❌ Arquivo não encontrado: %s", file_path)
            return ""
            
        with open(file_path, "rb") as f:
            return self.extract_text(f)