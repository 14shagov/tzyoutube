"""arq worker entrypoint (stub until stage 2).

The module path `worker.main.WorkerSettings` is referenced by
docker-compose.yml; keeping it importable now makes the compose command
valid from the very first deploy.
"""

from typing import ClassVar

from arq.connections import RedisSettings

from app.core.config import get_settings


class WorkerSettings:
    """arq worker settings; tasks are added in stage 2."""

    functions: ClassVar[list] = []
    on_startup = None
    on_shutdown = None

    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
