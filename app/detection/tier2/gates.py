"""
Tier 2 gate orchestrator.
Runs all 5 gates in order. First gate to fire returns immediately.
Gates return fired/not_fired + evidence only — no scoring decisions here.
"""
import structlog
from sqlalchemy.orm import Session

from app.detection.tier2 import cycle_gate, sink_gate, bipartite_gate, cash_mule_sink_gate, merchant_terminal_gate
from app.core.security import pseudonymize

logger = structlog.get_logger()


def run_all_gates(
    account_id: str,
    terminal_id: str | None,
    db: Session,
) -> dict:
    """
    Run all Tier 2 gates in priority order.
    Returns first gate result that fired, or {'fired': False} if all clear.

    Gate order (most definitive → most novel):
    1. Cycle gate       — round-trip = categorical fraud
    2. Sink gate        — dormant accumulator
    3. Bipartite gate   — mule network
    4. Cash mule sink   — receive → ATM → silence
    5. Merchant terminal — fake merchant / POS cashout
    """
    gates = [
        ("cycle", lambda: cycle_gate.run(account_id)),
        ("sink", lambda: sink_gate.run(account_id)),
        ("bipartite", lambda: bipartite_gate.run(account_id)),
        ("cash_mule_sink", lambda: cash_mule_sink_gate.run(account_id, db)),
        ("merchant_terminal", lambda: merchant_terminal_gate.run(terminal_id, db)),
    ]

    for gate_name, gate_fn in gates:
        try:
            result = gate_fn()
            if result.get("fired"):
                logger.info("Gate fired", gate=gate_name, account=pseudonymize(account_id))
                return result
        except Exception as exc:
            # Gate failure must not crash the pipeline — log and continue
            logger.error("Gate execution error", gate=gate_name, error=str(exc), exc_info=True)

    return {"fired": False}
