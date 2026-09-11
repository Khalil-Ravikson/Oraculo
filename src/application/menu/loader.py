"""
src/application/menu/loader.py
==============================
Resolve o menu ATIVO na ordem **Redis → Postgres (`menu_config`) →
`menus/default.json`** embutido — a mesma cadeia da `GraphSpec`
(`orchestration/loader.py`), pelo mesmo motivo: o caminho quente lê do Redis,
a verdade mora no Postgres, e o JSON embutido garante que o bot sobe mesmo
com o banco fora do ar.

Por que o Redis vem primeiro: `carregar_menu()` roda em TODA mensagem, antes
de qualquer decisão. Uma ida ao Postgres por mensagem seria latência somada a
cada tecla que o usuário digita, para ler um documento que quase nunca muda.

Por que o Postgres existe mesmo assim: é onde a edição do painel é durável.
O espelho Redis pode ser perdido (restart sem persistência, `FLUSHDB`); a
tabela, não. Quando o espelho está frio, a primeira mensagem que chega paga
uma consulta e reaquece o Redis para as seguintes.

Nunca levanta por falta de infraestrutura. Um menu velho é melhor que um bot
mudo — mas um menu INVÁLIDO não passa: `validate_menu()` roda em cada camada,
e uma config quebrada no Redis ou no banco é descartada em favor da camada de
baixo, com log.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from src.application.menu.spec import MenuConfig, validate_menu

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent / "menus" / "default.json"

MENU_REDIS_KEY = "menu:config:ativo"


@lru_cache(maxsize=1)
def menu_default() -> MenuConfig:
    """O menu embutido no código. Cacheado por processo — é um arquivo
    estático, reler a cada mensagem seria I/O à toa no caminho quente."""
    config = MenuConfig.model_validate_json(_DEFAULT_PATH.read_text(encoding="utf-8"))
    # Valida na carga, não só na escrita: um default.json quebrado por um
    # merge ruim tem que estourar no boot, não na primeira mensagem de um
    # usuário real.
    validate_menu(config)
    return config


def _do_redis() -> MenuConfig | None:
    from src.infrastructure.redis_client import get_redis_text

    raw = get_redis_text().get(MENU_REDIS_KEY)
    if not raw:
        return None
    texto = raw if isinstance(raw, str) else raw.decode()
    config = MenuConfig.model_validate(json.loads(texto))
    validate_menu(config)
    return config


async def _do_postgres() -> MenuConfig | None:
    from src.infrastructure.database.session import AsyncSessionLocal
    from src.infrastructure.repositories.menu_config_repository import MenuConfigRepository

    async with AsyncSessionLocal() as session:
        atual = await MenuConfigRepository(session).obter()

    if not atual or not atual.get("config"):
        return None

    config = MenuConfig.model_validate(atual["config"])
    validate_menu(config)
    return config


def _aquecer_redis(config: MenuConfig) -> None:
    """Reescreve o espelho depois de uma leitura no Postgres, para as próximas
    mensagens não pagarem a mesma consulta."""
    try:
        from src.infrastructure.redis_client import get_redis_text

        get_redis_text().set(MENU_REDIS_KEY, config.model_dump_json())
    except Exception:  # noqa: BLE001 — espelho é otimização, não requisito
        pass


async def carregar_menu() -> MenuConfig:
    """O menu ativo. Ordem: Redis → Postgres → default embutido."""
    try:
        config = _do_redis()
        if config is not None:
            return config
    except Exception:  # noqa: BLE001
        logger.warning("⚠️  [MENU] espelho Redis ilegível ou inválido — tentando o Postgres", exc_info=True)

    try:
        config = await _do_postgres()
        if config is not None:
            _aquecer_redis(config)
            return config
    except Exception:  # noqa: BLE001
        logger.warning("⚠️  [MENU] menu_config indisponível — usando o menu embutido", exc_info=True)

    return menu_default()


async def hydrate_redis_menu() -> None:
    """Reescreve o espelho a partir do Postgres. Chamado no startup do FastAPI
    e no `worker_process_init` do Celery, igual aos outros registries.

    Sem isto, o primeiro usuário depois de um deploy pagaria a consulta ao
    banco — e, pior, um Redis limpo com Postgres populado só convergiria no
    primeiro acesso."""
    try:
        config = await _do_postgres()
    except Exception:  # noqa: BLE001
        logger.warning("⚠️  [MENU] hydrate falhou — o bot continua com o menu embutido", exc_info=True)
        return

    if config is None:
        return

    _aquecer_redis(config)
    logger.info("📋 [MENU] espelho Redis reidratado a partir de menu_config.")
