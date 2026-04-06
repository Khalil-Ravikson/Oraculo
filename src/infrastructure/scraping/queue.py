"""
src/infrastructure/scraping/queue.py
--------------------------------------
Integração com RabbitMQ para fila de scraping assíncrona.

ARQUITETURA:
  Producer (API /scrape) → Exchange → Queue → Consumer (worker)
  
  Exchange: oraculo.scraping (topic)
  Queues:
    scraping.high    → prioridade alta (admin, HITL)
    scraping.normal  → prioridade normal (usuário)
    scraping.batch   → re-ingestão em lote (agendado)

MENSAGEM:
  JSON serializado do ScrapeRequest
  Headers: priority, source_name, request_id

USO COMO PRODUCER:
    producer = ScrapeQueueProducer(amqp_url)
    await producer.publish(ScrapeRequest(url="https://..."))

USO COMO CONSUMER (worker Celery ou asyncio):
    consumer = ScrapeQueueConsumer(amqp_url, scraping_service)
    await consumer.start()  # loop infinito
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from .base_scraper import ScrapeRequest

logger = logging.getLogger(__name__)

_EXCHANGE = "oraculo.scraping"
_QUEUE_HIGH = "scraping.high"
_QUEUE_NORMAL = "scraping.normal"
_QUEUE_BATCH = "scraping.batch"


def _priority_to_queue(priority: int) -> str:
    if priority <= 2:
        return _QUEUE_HIGH
    if priority <= 6:
        return _QUEUE_NORMAL
    return _QUEUE_BATCH


class ScrapeQueueProducer:
    """
    Publica ScrapeRequests no RabbitMQ.
    Usa aio-pika para I/O não-bloqueante.
    """

    def __init__(self, amqp_url: str):
        self._url = amqp_url
        self._connection = None
        self._channel = None

    async def connect(self) -> None:
        try:
            import aio_pika
            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel()
            exchange = await self._channel.declare_exchange(
                _EXCHANGE, "topic", durable=True
            )
            for queue_name in (_QUEUE_HIGH, _QUEUE_NORMAL, _QUEUE_BATCH):
                queue = await self._channel.declare_queue(queue_name, durable=True)
                await queue.bind(exchange, routing_key=queue_name)
            self._exchange = exchange
            logger.info("✅ RabbitMQ producer conectado: %s", self._url)
        except ImportError:
            raise ImportError("aio-pika não instalado: pip install aio-pika")

    async def publish(self, request: ScrapeRequest) -> None:
        """Publica um ScrapeRequest na fila adequada por prioridade."""
        if not self._channel:
            await self.connect()
        try:
            import aio_pika
            queue_name = _priority_to_queue(request.priority)
            body = json.dumps({
                "url": request.url,
                "source_name": request.source_name,
                "doc_type": request.doc_type,
                "force_refresh": request.force_refresh,
                "priority": request.priority,
                "metadata": request.metadata,
                "request_id": request.request_id,
            }, ensure_ascii=False).encode()

            await self._exchange.publish(
                aio_pika.Message(
                    body=body,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    headers={"priority": request.priority, "request_id": request.request_id},
                ),
                routing_key=queue_name,
            )
            logger.info("📤 Publicado na fila [%s]: %s", queue_name, request.url)
        except Exception as e:
            logger.error("❌ Queue.publish: %s", e)
            raise

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()


class ScrapeQueueConsumer:
    """
    Consome ScrapeRequests do RabbitMQ e executa via ScrapingService.
    Roda em loop infinito como worker separado.
    """

    def __init__(self, amqp_url: str, scraping_service: Any, concurrency: int = 3):
        self._url = amqp_url
        self._service = scraping_service
        self._concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)

    async def start(self, queues: list[str] | None = None) -> None:
        """Inicia o consumer. Bloqueia até cancelamento."""
        queues = queues or [_QUEUE_HIGH, _QUEUE_NORMAL, _QUEUE_BATCH]
        try:
            import aio_pika
            connection = await aio_pika.connect_robust(self._url)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=self._concurrency)

                for queue_name in queues:
                    queue = await channel.declare_queue(queue_name, durable=True)
                    await queue.consume(self._handle_message)
                    logger.info("👂 Consumindo fila: %s", queue_name)

                logger.info("✅ Consumer pronto (concurrency=%d)", self._concurrency)
                await asyncio.Future()  # roda para sempre
        except ImportError:
            raise ImportError("aio-pika não instalado: pip install aio-pika")

    async def _handle_message(self, message: Any) -> None:
        """Processa uma mensagem da fila com semáforo de concorrência."""
        async with self._semaphore:
            try:
                async with message.process():
                    data = json.loads(message.body.decode())
                    request = ScrapeRequest(**{k: v for k, v in data.items()
                                               if k in ScrapeRequest.__dataclass_fields__})
                    result = await self._service.scrape(request)
                    if result.ok:
                        logger.info("✅ Consumer processou: %s", request.url)
                    else:
                        logger.warning("⚠️  Consumer falhou: %s — %s", request.url, result.error)
            except Exception as e:
                logger.error("❌ Consumer erro inesperado: %s", e)


class InMemoryQueue:
    """
    Fila em memória para testes e desenvolvimento sem RabbitMQ.
    Thread-safe via asyncio.Queue.
    """

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._published: list[ScrapeRequest] = []

    async def publish(self, request: ScrapeRequest) -> None:
        await self._queue.put(request)
        self._published.append(request)
        logger.debug("📤 [InMemory] Publicado: %s", request.url)

    async def get(self) -> ScrapeRequest:
        return await self._queue.get()

    @property
    def published(self) -> list[ScrapeRequest]:
        return list(self._published)

    def qsize(self) -> int:
        return self._queue.qsize()