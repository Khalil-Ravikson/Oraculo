"""
src/infrastructure/observability/metrics_server.py
==================================================
Expõe as métricas dos workers Celery para o Prometheus (TD-019).

## O problema que isto resolve

Até 2026-09-11 o `prometheus.yml` tinha três alvos: a API, o `redis_exporter`
e o próprio Prometheus. **Nenhum worker.** Como quase toda métrica do Oráculo
é emitida dentro de um worker — toda chamada de LLM, todo RAG, todo roteamento
— o painel do Grafana media um sistema que não estava sendo observado. A
tabela `metricas_llm` no Postgres era a única telemetria que sobrevivia.

## Por que modo multiprocesso e não um servidor por processo

O Celery usa prefork: o processo principal aceita as tasks e processos filhos
as executam. As métricas são incrementadas **no filho**, e o registro do
`prometheus_client` é por processo. Um servidor HTTP no processo principal
não enxergaria nada; um servidor por filho brigaria pela mesma porta, e os
filhos são reciclados (`--max-tasks-per-child`), levando os contadores junto.

O modo multiprocesso do `prometheus_client` existe exatamente para isso: cada
processo escreve num diretório compartilhado e um coletor agrega na hora de
servir. É a solução da própria biblioteca, não uma invenção nossa.

## Contrato de operação

* `PROMETHEUS_MULTIPROC_DIR` precisa estar definido **antes** de qualquer
  métrica ser criada — por isso vem do ambiente do container, não de código.
* O diretório precisa ser limpo quando o worker sobe: arquivos de processos
  mortos de uma execução anterior seriam somados de novo.
* A API **não** usa este módulo. Ela é um processo só, serve `/metrics` pelo
  registro global de sempre, e definir a variável lá mudaria esse caminho sem
  necessidade.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_MULTIPROC_DIR = "PROMETHEUS_MULTIPROC_DIR"


def multiproc_ativo() -> bool:
    return bool(os.environ.get(ENV_MULTIPROC_DIR))


def garantir_diretorio() -> None:
    """Cria o diretório de métricas ANTES da primeira métrica existir.

    Ordem importa e foi aprendida na prática: `prometheus_client` abre um
    arquivo por métrica no momento em que ela é declarada, e as métricas são
    declaradas durante o import dos módulos. Criar o diretório no
    `worker_ready` é tarde — o worker já morreu com
    `FileNotFoundError: .../histogram_1.db` antes de chegar lá.

    Por isso esta função é chamada no import de `metrics.py`, que é o módulo
    que declara tudo."""
    caminho = os.environ.get(ENV_MULTIPROC_DIR)
    if not caminho:
        return
    try:
        Path(caminho).mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [METRICS] Não foi possível criar %s: %s", caminho, exc)


def limpar_diretorio() -> None:
    """Apaga resíduo de execuções anteriores.

    Sem isto, os contadores de um worker que morreu ontem continuariam sendo
    somados no total de hoje — o Prometheus veria um degrau que nunca
    aconteceu."""
    caminho = os.environ.get(ENV_MULTIPROC_DIR)
    if not caminho:
        return

    destino = Path(caminho)
    try:
        destino.mkdir(parents=True, exist_ok=True)
        removidos = 0
        for arquivo in destino.glob("*.db"):
            try:
                arquivo.unlink()
                removidos += 1
            except OSError:
                pass
        if removidos:
            logger.info("🧹 [METRICS] %d arquivo(s) de métrica antigos removidos.", removidos)
    except Exception as exc:  # noqa: BLE001 — métrica nunca derruba worker
        logger.warning("⚠️  [METRICS] Falha ao limpar %s: %s", caminho, exc)


def iniciar_servidor(porta: int) -> bool:
    """Sobe o endpoint `/metrics` do worker. Devolve se conseguiu.

    Chamado no `worker_ready` (processo principal). Nunca levanta: um worker
    sem endpoint de métrica continua processando mensagem, e é isso que
    importa."""
    if not multiproc_ativo():
        logger.info(
            "ℹ️  [METRICS] %s não definido — worker não expõe métricas. "
            "Defina a variável no container para o Prometheus poder coletar.",
            ENV_MULTIPROC_DIR,
        )
        return False

    try:
        from prometheus_client import CollectorRegistry, multiprocess, start_http_server

        registro = CollectorRegistry()
        multiprocess.MultiProcessCollector(registro)
        start_http_server(porta, registry=registro)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️  [METRICS] Não foi possível expor métricas na porta %d: %s", porta, exc)
        return False

    logger.info("📊 [METRICS] Worker expondo métricas em :%d/metrics (modo multiprocesso).", porta)
    return True
