"""Formato de armazenamento dos trechos do RAG (achado de 2026-09-11).

O embedding era gravado como array JSON de 3072 números **em texto** — 81 KB
por trecho contra 15,5 KB do mesmo conteúdo em HASH com float32 binário. A
primeira ingestão real da wiki estourou o `maxmemory` do Redis por volta da
página 600 de 1383, o Redis passou a recusar escrita, e **o bot parou**: sem
escrita não há posição de menu, cache nem checkpoint.

Estes testes travam as duas propriedades que impedem a volta do problema:
o vetor vai como bytes, e o índice é declarado como HASH.

Detalhe completo em `docs/architecture/arquitetura_oraculo.md` §8.1.
"""
from __future__ import annotations

import numpy as np
import pytest

from src.infrastructure.redis_client import VECTOR_DIM, _schema_chunks, _vetor_bytes


# ── O vetor precisa virar bytes ──────────────────────────────────────────

def test_lista_de_floats_vira_float32_binario():
    """3072 dimensões × 4 bytes. Qualquer tamanho diferente significa que o
    vetor voltou a ser texto, ou que a dimensão divergiu do índice."""
    vetor = [0.1] * VECTOR_DIM
    assert len(_vetor_bytes(vetor)) == VECTOR_DIM * 4


def test_array_numpy_tambem_vira_bytes():
    vetor = np.random.default_rng(1).random(VECTOR_DIM)
    assert len(_vetor_bytes(vetor)) == VECTOR_DIM * 4


def test_bytes_passam_direto():
    """Chamador que já converteu não paga a conversão de novo."""
    cru = np.zeros(VECTOR_DIM, dtype=np.float32).tobytes()
    assert _vetor_bytes(cru) == cru


def test_float64_e_convertido_para_float32():
    """O RediSearch espera float32. Um array float64 ocuparia o dobro e o
    índice recusaria o documento."""
    vetor = np.ones(VECTOR_DIM, dtype=np.float64)
    assert len(_vetor_bytes(vetor)) == VECTOR_DIM * 4


def test_valores_sobrevivem_a_ida_e_volta():
    """Conversão não pode corromper o vetor — busca vetorial com valores
    errados devolve resultado errado sem erro nenhum."""
    original = np.random.default_rng(2).random(VECTOR_DIM).astype(np.float32)
    devolta = np.frombuffer(_vetor_bytes(original.tolist()), dtype=np.float32)
    assert np.allclose(original, devolta, atol=1e-6)


# ── O índice precisa ser HASH ────────────────────────────────────────────

def test_indice_de_trechos_e_hash():
    """`json` aqui é o que custava 5x a memória. Se alguém reverter para
    JSON, este teste falha antes de a wiki estourar o Redis de novo."""
    schema = _schema_chunks().to_dict()
    assert schema["index"]["storage_type"].lower() == "hash"


def test_campo_de_vetor_declara_float32_com_a_dimensao_do_provedor():
    schema = _schema_chunks().to_dict()
    campo = next(c for c in schema["fields"] if c["name"] == "embedding")
    assert campo["attrs"]["datatype"].lower() == "float32"
    assert int(campo["attrs"]["dims"]) == VECTOR_DIM


@pytest.mark.parametrize("campo", ["doc_type", "sistema", "modulo"])
def test_filtros_do_menu_continuam_no_indice(campo):
    """O menu filtra por assunto e por sistema (SIGAA x SIPAC). Perder um
    desses campos na mudança de formato quebraria a busca filtrada — foi a
    dúvida que motivou comparar os dois formatos antes de migrar."""
    schema = _schema_chunks().to_dict()
    nomes = {c["name"]: c for c in schema["fields"]}
    assert campo in nomes
    assert nomes[campo]["type"] == "tag"
