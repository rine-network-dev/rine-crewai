"""x402 payment tools — payer ``rine_pay`` + payee ``rine_fulfill``.

Both are **Mutating** ``BaseTool``s: ``rine_pay`` signs an EIP-3009 stablecoin
authorization and moves the daily spend budget (permanent debit); ``rine_fulfill``
verifies + settles a received authorization on-chain via the payee's facilitator.

Thin adapters — **no** signing/policy/journal or verify→settle logic lives here.
The payer wraps :func:`rine.x402.prepare_payment` (sign + reserve LAST, fail-closed)
and replies in-thread; the payee delegates to the single :func:`rine.x402.fulfill`
orchestrator. rine stays a pure metadata relay: verify/settle is plain external HTTP
to the caller-configured facilitator, never a rine route. Wallet keys never surface.

Refusals are returned as a stable, parseable string — ``status: <status> — <detail>``
— reusing the ``rine_pay`` seven-status vocabulary verbatim, never leaking amounts.
"""

import asyncio
from typing import Any

from pydantic import BaseModel
from rine import RineClient
from rine.x402 import (
    FacilitatorClient,
    X402Error,
    fulfill,
    is_auto_payable,
    load_policy,
    parse_x402_payload,
    prepare_payment,
    read_daily_spent,
    resolve_base_url,
    select_requirement,
    wallet_key_exists,
)

from crewai_rine._client import BaseRineTool, _format_error
from crewai_rine.tools import _x402_status as st
from crewai_rine.tools._schemas import RineFulfillInput, RinePayInput

_PAYMENT_REQUIRED = "rine.v1.x402_payment_required"


class RinePayTool(BaseRineTool):
    """Pay a received x402 payment request in-thread (MUTATING — moves funds)."""

    name: str = "rine_pay"
    description: str = (
        "Pay a received x402 payment request (rine.v1.x402_payment_required) in-thread. "
        "MUTATING: signs an EIP-3009 stablecoin authorization with the agent's wallet under "
        "its local spend policy (deny-by-default, caps enforced), reserves the daily budget "
        "as the LAST act before sending (fail-closed — the debit stands even if the send "
        "fails, and there is no refund), and replies with the signed rine.v1.x402_payment. "
        "Returns a typed `status: <status> — <detail>` string — `payment-submitted` on "
        "success, else `no-wallet` / `not-payment-required` / `policy-refused` / "
        "`above-auto-pay-threshold` / `already-paid` / `wallet-busy`. Does NOT wait for "
        "settlement: the receipt arrives later as an ordinary inbox message."
    )
    args_schema: type[BaseModel] = RinePayInput

    def _run(
        self,
        message_id: str,
        auto_pay: bool = False,
        allow_repay: bool = False,
        emit_marker: bool = True,
    ) -> str:
        try:
            client = self._get_client()
            quote = client.read(message_id, agent=self.agent)
            if quote.type != _PAYMENT_REQUIRED:
                return st.status_line(
                    st.NOT_PAYMENT_REQUIRED,
                    f"Message {message_id} is type '{quote.type}', not {_PAYMENT_REQUIRED}.",
                )
            if quote.plaintext is None:
                return st.status_line(
                    st.NOT_PAYMENT_REQUIRED,
                    f"Message {message_id} did not decrypt to a payment quote.",
                )
            # The payer is definitionally the quote's recipient (mirrors rine_pay MCP).
            agent_id = str(quote.to_agent_id)
            config_dir = client.config_dir
            if not wallet_key_exists(config_dir, agent_id):
                return st.status_line(
                    st.NO_WALLET,
                    f"No wallet for agent {agent_id}; create one before paying.",
                )
            pr = parse_x402_payload(quote.plaintext)
            policy = load_policy(config_dir, agent_id)
            if auto_pay:
                # Gate BEFORE the permanent reserve inside prepare_payment: refuse an
                # above-threshold quote without debiting the budget (invariant 7 + 9).
                requirement = select_requirement(
                    pr, policy, read_daily_spent(config_dir, agent_id)
                )
                if policy is None or not is_auto_payable(policy, requirement):
                    return st.status_line(
                        st.ABOVE_THRESHOLD,
                        "Quote exceeds autoPayThreshold; re-run without auto_pay to authorize.",
                    )
            prepared = prepare_payment(
                config_dir,
                agent_id,
                pr,
                message_id=message_id,
                force=allow_repay,
                add_marker=emit_marker,
            )
            sent = client.reply(
                message_id,
                prepared.message.payload,
                message_type=prepared.message.message_type,
                metadata=prepared.message.metadata,
                content_type=prepared.message.content_type,
                agent=agent_id,
            )
        except X402Error as err:
            return st.map_x402_error(err)
        except Exception as err:  # noqa: BLE001 — surface as a tool message, never crash
            return _format_error(err)
        return st.status_line(
            st.SUBMITTED,
            f"Paid {message_id} on {prepared.requirement.get('network')} "
            f"({prepared.requirement.get('asset')}); sent {sent.id} in conversation "
            f"{sent.conversation_id}. Settlement receipt will arrive as an inbox message.",
        )


class RineFulfillTool(BaseRineTool):
    """Verify + settle a received x402 payment and reply with a receipt (MUTATING)."""

    name: str = "rine_fulfill"
    description: str = (
        "Fulfil a received x402 payment (rine.v1.x402_payment) as the payee. MUTATING: "
        "verifies the signed authorization then settles it ON-CHAIN via the configured "
        "facilitator (plain external HTTP — rine never touches funds), and replies with a "
        "rine.v1.x402_receipt in-thread. A failed verification is a business outcome, not an "
        "error: settle is skipped and a payment-failed receipt is still threaded back. "
        "Configure the facilitator via the tool's `facilitator` / `facilitator_url` fields."
    )
    args_schema: type[BaseModel] = RineFulfillInput

    # Facilitator config is operator wiring (not an LLM per-call decision), so it lives
    # on the tool like config_dir/api_url. `facilitator` is a preset name (cdp/payai/
    # x402-rs); `facilitator_url` overrides with an explicit base URL (self-hosted/mock).
    facilitator: str = "cdp"
    facilitator_url: str | None = None
    facilitator_api_key: str | None = None

    def _run(self, message_id: str, emit_marker: bool = True) -> str:
        try:
            # Validate the facilitator target up front (clear error before any read).
            resolve_base_url(self.facilitator, self.facilitator_url)
            result = asyncio.run(self._fulfill(message_id, emit_marker))
        except X402Error as err:
            return st.status_line(st.NOT_PAYMENT_REQUIRED, f"{err.code}: {err.message}")
        except ValueError as err:  # unknown/incomplete facilitator preset
            return str(err)
        except Exception as err:  # noqa: BLE001 — surface as a tool message, never crash
            return _format_error(err)
        if result.settlement is not None:
            tx = str(result.settlement.get("transaction", "") or "")
            net = str(result.settlement.get("network", "") or "")
            return (
                f"Fulfilled {message_id}: settled on {net} (tx {tx}); receipt "
                f"{result.receipt.id} sent in conversation {result.receipt.conversation_id}."
            )
        reason = str(result.verification.get("invalidReason") or "verification_failed")
        return (
            f"Payment {message_id} failed verification ({reason}); no settlement occurred. "
            f"A payment-failed receipt {result.receipt.id} was sent in conversation "
            f"{result.receipt.conversation_id}."
        )

    async def _fulfill(self, message_id: str, emit_marker: bool) -> Any:
        """Async payee flow: read → verify → settle → receipt via the one orchestrator.

        crewai tools are sync (SyncRineClient) but ``rine.x402.fulfill`` is async and
        needs an async client + facilitator, so this runs inside ``asyncio.run``.
        """
        overrides: dict[str, Any] = {}
        if self.config_dir is not None:
            overrides["config_dir"] = self.config_dir
        if self.api_url is not None:
            overrides["api_url"] = self.api_url
        if self.agent is not None:
            overrides["agent"] = self.agent
        async with RineClient(**overrides) as client:
            payment = await client.read(message_id, agent=self.agent)
            async with FacilitatorClient(
                self.facilitator,
                base_url=self.facilitator_url,
                api_key=self.facilitator_api_key,
            ) as fac:
                return await fulfill(
                    client, payment, facilitator=fac, agent=self.agent, add_marker=emit_marker
                )


__all__ = ["RinePayTool", "RineFulfillTool"]
