import redis
from app.core.config import settings

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def velocity_1h(account_id: str) -> int:
    """Transaction count for account in last 1 hour."""
    r = get_redis()
    val = r.get(f"vel:1h:{account_id}")
    return int(val) if val else 0


def velocity_24h(account_id: str) -> int:
    """Transaction count for account in last 24 hours."""
    r = get_redis()
    val = r.get(f"vel:24h:{account_id}")
    return int(val) if val else 0


def increment_velocity(account_id: str, amount: float) -> None:
    """Increment velocity counters when a transaction is scored."""
    r = get_redis()
    pipe = r.pipeline()
    pipe.incr(f"vel:1h:{account_id}")
    pipe.expire(f"vel:1h:{account_id}", 3600)
    pipe.incr(f"vel:24h:{account_id}")
    pipe.expire(f"vel:24h:{account_id}", 86400)
    pipe.incrbyfloat(f"vol:1h:{account_id}", amount)
    pipe.expire(f"vol:1h:{account_id}", 3600)
    pipe.execute()


def get_graph_features(account_id: str) -> dict:
    """Fetch pre-computed graph features from Redis cache."""
    r = get_redis()
    raw = r.hgetall(f"feat:{account_id}")
    if not raw:
        return {}
    def _cast(v: str):
        if v == "True":
            return True
        if v == "False":
            return False
        if v == "None":
            return None
        try:
            return float(v)
        except ValueError:
            return v  # keep as string (e.g. account_type, kyc_occupation)
    return {k: _cast(v) for k, v in raw.items()}


def set_graph_features(account_id: str, features: dict) -> None:
    """Store pre-computed graph features. Called by nightly batch only."""
    r = get_redis()
    serialized = {k: str(v) for k, v in features.items() if v is not None}
    if serialized:
        pipe = r.pipeline()
        pipe.hset(f"feat:{account_id}", mapping=serialized)
        pipe.expire(f"feat:{account_id}", 86400 + 7200)  # 26h — nightly batch has 2h window
        pipe.execute()
