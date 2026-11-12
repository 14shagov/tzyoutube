"""Static guards for the arq worker wiring (no Redis, no Docker).

Catches the "worker starts, registers nothing, dies" class of failure:
the tasks enqueued by the web app must all be registered in
WorkerSettings.functions.
"""

import inspect

from worker import main as worker_main


def _queue_task_names() -> set[str]:
    """Task names the web app enqueues (grep the queue client module)."""
    import re

    from app.worker import queue as queue_mod

    src = inspect.getsource(queue_mod)
    return set(re.findall(r'enqueue_job\(\s*"([a-z_]+)"', src))


def test_worker_registers_all_enqueued_tasks() -> None:
    functions = list(worker_main.WorkerSettings.functions)
    assert functions, "WorkerSettings.functions must not be empty"
    registered = {f.__name__ for f in functions}
    enqueued = _queue_task_names()
    assert enqueued, "no enqueue_job calls found in app.worker.queue"
    missing = enqueued - registered
    assert not missing, f"tasks enqueued but not registered in worker: {missing}"


def test_worker_settings_redis_points_to_config() -> None:
    from arq.connections import RedisSettings

    rs = worker_main.WorkerSettings.redis_settings
    assert isinstance(rs, RedisSettings)


def test_transcode_task_signature_matches_enqueue() -> None:
    # The web app enqueues transcode(str(video_id)). arq passes its own ctx
    # dict as the first argument, so the task must accept ctx + exactly one
    # more positional argument.
    functions = {f.__name__: f for f in worker_main.WorkerSettings.functions}
    assert "transcode" in functions, "transcode task not registered"
    params = list(inspect.signature(functions["transcode"]).parameters.values())
    assert params and params[0].name == "ctx", "arq task must take ctx first"
    beyond_ctx = [
        p
        for p in params[1:]
        if p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    assert len(beyond_ctx) == 1, (
        f"transcode must take ctx + exactly 1 positional arg, got {len(beyond_ctx)}"
    )
