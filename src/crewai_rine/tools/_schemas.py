"""Pydantic ``args_schema`` models for the messaging tools (SPEC §6.1–6.5).

Kept separate from ``messaging.py`` so the tool logic stays under the per-file LOC
budget. Each model maps 1:1 to a tool's typed arguments; descriptions are the text
the LLM sees when it inspects the tool.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RineSendInput(BaseModel):
    """Arguments for ``rine_send``."""

    to: str = Field(description="Recipient handle/UUID, or '#group@org' for a group.")
    body: str = Field(description="Message text (sent as the payload's 'text' field).")
    message_type: str = Field(default="rine.v1.task_request", description="Rine message type.")
    idempotency_key: str | None = Field(
        default=None, description="Optional idempotency key to dedupe retried sends."
    )


class RineSendAndWaitInput(BaseModel):
    """Arguments for ``rine_send_and_wait``."""

    to: str = Field(description="Recipient handle/UUID (1:1 only — not a group).")
    body: str = Field(description="Message text (sent as the payload's 'text' field).")
    wait_seconds: int = Field(
        default=30, ge=1, le=300, description="Seconds to block for a reply (1-300)."
    )
    message_type: str = Field(default="rine.v1.task_request", description="Rine message type.")


class RineCheckInboxInput(BaseModel):
    """Arguments for ``rine_check_inbox``."""

    limit: int = Field(default=20, ge=1, le=100, description="Max new messages to return.")


class RineReadInput(BaseModel):
    """Arguments for ``rine_read``."""

    message_id: str = Field(description="UUID of the message to fetch and decrypt.")


class RineReplyInput(BaseModel):
    """Arguments for ``rine_reply``."""

    message_id: str = Field(description="UUID of the message to reply to.")
    body: str = Field(description="Reply text (sent as the payload's 'text' field).")
    message_type: str = Field(default="rine.v1.task_response", description="Rine message type.")


class RinePayInput(BaseModel):
    """Arguments for ``rine_pay`` (SPEC x402 v1.1 D6, payer tool)."""

    message_id: str = Field(
        description="UUID of the received rine.v1.x402_payment_required quote to pay."
    )
    auto_pay: bool = Field(
        default=False,
        description=(
            "If true, pay ONLY when the quote is at/below the policy's autoPayThreshold; "
            "otherwise return `above-auto-pay-threshold` without paying. Default false — "
            "calling the tool is the authorization, still bounded by the spend caps."
        ),
    )
    allow_repay: bool = Field(
        default=False,
        description=(
            "If true, re-pay a quote already paid today, bypassing the once-per-message "
            "guard (a deliberate second, permanent debit). Default false → a repeat returns "
            "`already-paid` without spending."
        ),
    )
    emit_marker: bool = Field(
        default=True,
        description=(
            "Attach the cleartext {status, rail} marker (metadata['rine.x402']). Default "
            "true. The marker never carries amounts/assets/addresses. Set false to seal fully."
        ),
    )


class RineFulfillInput(BaseModel):
    """Arguments for ``rine_fulfill`` (SPEC x402 v1.1 D3/D6, payee tool)."""

    message_id: str = Field(
        description="UUID of the received rine.v1.x402_payment message to verify + settle."
    )
    emit_marker: bool = Field(
        default=True,
        description=(
            "Attach the cleartext {status, rail} marker on the receipt. Default true; "
            "never carries amounts. Set false to seal the receipt fully."
        ),
    )


__all__ = [
    "RineSendInput",
    "RineSendAndWaitInput",
    "RineCheckInboxInput",
    "RineReadInput",
    "RineReplyInput",
    "RinePayInput",
    "RineFulfillInput",
]
