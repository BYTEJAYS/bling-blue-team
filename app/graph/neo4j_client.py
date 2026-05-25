"""
Neo4j client — READ ONLY. Blue Team never writes to Neo4j.
Graph Engine (teammate) owns all writes.
"""
import structlog
from neo4j import GraphDatabase, Driver
from app.core.config import settings
from app.core.exceptions import GraphQueryError

logger = structlog.get_logger()

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_pool_size=50,  # explicit cap — avoids exhaustion under Celery+API load
        )
    return _driver


def close_driver() -> None:
    global _driver
    if _driver:
        _driver.close()
        _driver = None


def run_query(cypher: str, params: dict) -> list[dict]:
    """
    Execute a read-only Cypher query.
    All callers must pass parameterized queries — never f-strings.
    """
    driver = get_driver()
    try:
        with driver.session() as session:
            result = session.run(cypher, **params)
            return [record.data() for record in result]
    except Exception as exc:
        logger.error("Neo4j query failed", cypher=cypher[:80], error=str(exc))
        raise GraphQueryError(f"Neo4j query failed: {exc}") from exc
