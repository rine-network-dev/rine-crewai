"""The ``rine_pay`` seven-status vocabulary + parseable refusal encoding (SPEC x402 v1.1).

Reused VERBATIM from the MCP ``rine_pay`` tool (``rine-mcp/src/tools/payments.ts:39-47``)
so the agent-facing payment contract is identical on every surface. Payer refusals are
returned as the stable, parseable string ``status: <status> — <detail>``; amounts are
never leaked into a status line.
"""

from __future__ import annotations

from rine.x402 import X402Error, X402ErrorCode

# The seven terminal statuses, verbatim.
SUBMITTED = "payment-submitted"
NO_WALLET = "no-wallet"
NOT_PAYMENT_REQUIRED = "not-payment-required"
POLICY_REFUSED = "policy-refused"
ABOVE_THRESHOLD = "above-auto-pay-threshold"
ALREADY_PAID = "already-paid"
WALLET_BUSY = "wallet-busy"


def status_line(status: str, detail: str) -> str:
    """Encode a typed outcome as the stable ``status: <status> — <detail>`` string."""
    return f"status: {status} — {detail}"


def map_x402_error(err: X402Error) -> str:
    """Map a payer ``X402Error`` code onto the seven-status vocabulary (parity with MCP)."""
    if err.code == X402ErrorCode.ALREADY_PAID:
        return status_line(ALREADY_PAID, err.message)
    if err.code == X402ErrorCode.WALLET_BUSY:
        return status_line(WALLET_BUSY, err.message)
    # Every remaining code is a deny-by-default policy refusal. `select_requirement`
    # wraps the specific per-candidate rejection (no-policy / per-tx-cap-exceeded /
    # asset-not-allowed / …) in `details["reason"]`; surface THAT actionable code, not
    # the generic `no-acceptable-requirement` wrapper (parity with MCP payments.ts:170).
    reason = err.details.get("reason") if isinstance(err.details, dict) else None
    code = reason if isinstance(reason, str) else err.code
    return status_line(POLICY_REFUSED, f"{code}: {err.message}")


__all__ = [
    "SUBMITTED",
    "NO_WALLET",
    "NOT_PAYMENT_REQUIRED",
    "POLICY_REFUSED",
    "ABOVE_THRESHOLD",
    "ALREADY_PAID",
    "WALLET_BUSY",
    "status_line",
    "map_x402_error",
]
