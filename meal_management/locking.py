"""Local catalog-cache locking; no orchestration dependency."""
from contextlib import contextmanager

class Held(RuntimeError):
    pass

@contextmanager
def cache_lock(cache):
    lock = cache.with_name(cache.name + ".sync-lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise Held("another sync owns this cache lock; do not remove its lock") from exc
    try:
        yield
    finally:
        lock.rmdir()
