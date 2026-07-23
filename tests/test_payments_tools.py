"""Behavioral tests for the x402 payment tools.

Both tools are exercised through their public CrewAI interface (``tool.run(...)``);
assertions are on the returned ``status: <status> — <detail>`` string, the seven-status
vocabulary, and observable HTTP. Only the rine HTTP boundary and the facilitator HTTP
boundary are mocked (respx) — the SDK signing/policy/journal + real HPKE round-trip run.

The marker invariant ({status, rail} only, never an amount) is asserted on the actual
metadata a tool puts on the wire; the settle-first ordering is asserted on the recorded
facilitator call order.
"""

from __future__ import annotations

import json

import httpx
import respx
from rine._constants import EncryptionVersion, MessageType
from rine.x402 import build_payment_required, create_wallet, save_policy
from rine.x402.types import X402_QUOTE_REF_KEY, PaymentPolicy

from crewai_rine import RineFulfillTool, RinePayTool

from .conftest import (
    AGENT_ID,
    API_URL,
    MESSAGE_ID,
    REPLY_ID,
    hpke_self_encrypt,
    message_payload,
    mock_agents,
    mock_token,
    sent_message_payload,
)

NETWORK = "eip155:84532"  # base-sepolia (CAIP-2)
EURC_ASSET = "0x808456652fdb597867f38412077a9182bf77359f"
PAY_TO = "0x1111111111111111111111111111111111111111"
PAYER_ADDR = "0x2222222222222222222222222222222222222222"
FAC_URL = "https://facilitator.test"
# The payee-issued quote a received payment references (distinct from the payment id).
QUOTE_ID = "cccccccc-dddd-eeee-ffff-000000000003"


def _self_keys_json(config_dir):
    from rine._crypto.keys import (
        encryption_public_key_to_jwk,
        load_agent_keys,
        signing_public_key_to_jwk,
    )

    keys = load_agent_keys(config_dir, AGENT_ID)
    return {
        "agent_id": AGENT_ID,
        "encryption_public_key": encryption_public_key_to_jwk(keys.encryption.public_key),
        "signing_public_key": signing_public_key_to_jwk(keys.signing.public_key),
    }


def _mock_self_keys(config_dir):
    """AGENT_ID is both quote-sender (verify) and reply-recipient (encrypt) in these tests."""
    respx.get(f"{API_URL}/agents/{AGENT_ID}/keys").mock(
        return_value=httpx.Response(200, json=_self_keys_json(config_dir))
    )


def _payer_policy(config_dir, *, auto_pay_threshold="1.00"):
    create_wallet(config_dir, AGENT_ID)
    save_policy(
        config_dir,
        AGENT_ID,
        PaymentPolicy(
            per_tx_cap="5.00",
            daily_cap="50.00",
            allowed_assets=["EURC"],
            allowed_networks=[NETWORK],
            auto_pay_threshold=auto_pay_threshold,
        ),
    )


def _quote_wire(config_dir, amount="100000"):
    """Build a real x402_payment_required, HPKE-sealed to AGENT_ID (self-readable)."""
    requirement = {
        "scheme": "exact",
        "network": NETWORK,
        "amount": amount,
        "asset": EURC_ASSET,
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 600,
        "extra": {"name": "EURC", "version": "2"},
    }
    msg = build_payment_required(
        accepts=[requirement],
        resource={
            "url": "rine://task/x",
            "description": "paid task",
            "mimeType": "application/json",
        },
        error="payment required",
    )
    return hpke_self_encrypt(config_dir, msg.payload)


def _mock_quote(config_dir, amount="100000"):
    respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
        return_value=httpx.Response(
            200,
            json=message_payload(
                encryption_version=EncryptionVersion.HPKE_V1,
                encrypted_payload=_quote_wire(config_dir, amount),
                msg_type=MessageType.X402_PAYMENT_REQUIRED,
            ),
        )
    )


def _pay_tool(config_dir):
    return RinePayTool(config_dir=config_dir, api_url=API_URL, agent=AGENT_ID)


# =====================================================================
# RinePayTool
# =====================================================================


class TestRinePayTool:
    @respx.mock
    def test_pay_valid_quote_returns_payment_submitted_and_replies(self, config_dir):
        mock_token()
        mock_agents()
        _payer_policy(config_dir)
        _mock_quote(config_dir)
        _mock_self_keys(config_dir)
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        out = _pay_tool(config_dir).run(message_id=MESSAGE_ID)

        assert out.startswith("status: payment-submitted — ")
        assert reply.called
        # The signed rine.v1.x402_payment goes back in-thread.
        assert json.loads(reply.calls[0].request.content)["type"] == MessageType.X402_PAYMENT

    @respx.mock
    def test_pay_emits_marker_with_status_and_rail_never_amount(self, config_dir):
        """Invariant 2: the cleartext marker is {status, rail} only — no amount leaks."""
        mock_token()
        mock_agents()
        _payer_policy(config_dir)
        _mock_quote(config_dir, amount="123456")
        _mock_self_keys(config_dir)
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        _pay_tool(config_dir).run(message_id=MESSAGE_ID)

        marker = json.loads(reply.calls[0].request.content)["metadata"]["rine.x402"]
        assert set(marker) == {"status", "rail"}
        assert marker["status"] == "payment-submitted"
        assert "123456" not in reply.calls[0].request.content.decode()

    @respx.mock
    def test_pay_emit_marker_false_sends_no_marker(self, config_dir):
        mock_token()
        mock_agents()
        _payer_policy(config_dir)
        _mock_quote(config_dir)
        _mock_self_keys(config_dir)
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        _pay_tool(config_dir).run(message_id=MESSAGE_ID, emit_marker=False)

        assert "rine.x402" not in reply.calls[0].request.content.decode()

    @respx.mock
    def test_pay_non_quote_message_returns_not_payment_required(self, config_dir):
        mock_token()
        mock_agents()
        _payer_policy(config_dir)
        _mock_self_keys(config_dir)
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(
                200,
                json=message_payload(
                    encryption_version=EncryptionVersion.HPKE_V1,
                    encrypted_payload=hpke_self_encrypt(config_dir, {"text": "not a quote"}),
                    msg_type=MessageType.TASK_REQUEST,
                ),
            )
        )

        out = _pay_tool(config_dir).run(message_id=MESSAGE_ID)

        assert out.startswith("status: not-payment-required — ")

    @respx.mock
    def test_pay_without_wallet_returns_no_wallet(self, config_dir):
        mock_token()
        mock_agents()
        # policy but NO wallet created → the wallet guard fires.
        save_policy(
            config_dir,
            AGENT_ID,
            PaymentPolicy(
                per_tx_cap="5.00",
                daily_cap="50.00",
                allowed_assets=["EURC"],
                allowed_networks=[NETWORK],
                auto_pay_threshold="1.00",
            ),
        )
        _mock_quote(config_dir)
        _mock_self_keys(config_dir)

        out = _pay_tool(config_dir).run(message_id=MESSAGE_ID)

        assert out.startswith("status: no-wallet — ")

    @respx.mock
    def test_pay_with_no_policy_returns_policy_refused(self, config_dir):
        """Deny-by-default (invariant 5): a wallet but no policy → policy-refused."""
        mock_token()
        mock_agents()
        create_wallet(config_dir, AGENT_ID)  # wallet but NO policy on disk
        _mock_quote(config_dir)
        _mock_self_keys(config_dir)

        out = _pay_tool(config_dir).run(message_id=MESSAGE_ID)

        assert out.startswith("status: policy-refused — ")
        assert "no-policy" in out

    @respx.mock
    def test_pay_second_time_same_message_returns_already_paid(self, config_dir):
        """Once-per-message guard (invariant 8): a repeat pay returns already-paid."""
        mock_token()
        mock_agents()
        _payer_policy(config_dir)
        _mock_quote(config_dir)
        _mock_self_keys(config_dir)
        respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        first = _pay_tool(config_dir).run(message_id=MESSAGE_ID)
        second = _pay_tool(config_dir).run(message_id=MESSAGE_ID)

        assert first.startswith("status: payment-submitted — ")
        assert second.startswith("status: already-paid — ")

    @respx.mock
    def test_pay_auto_pay_above_threshold_refuses_without_reserving(self, config_dir):
        """Invariant 9: auto_pay above threshold refuses and does NOT reserve/send."""
        mock_token()
        mock_agents()
        _payer_policy(config_dir, auto_pay_threshold="0.05")  # 0.05 EURC < 0.10 quote
        _mock_quote(config_dir, amount="100000")
        _mock_self_keys(config_dir)
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        out = _pay_tool(config_dir).run(message_id=MESSAGE_ID, auto_pay=True)

        assert out.startswith("status: above-auto-pay-threshold — ")
        assert not reply.called  # no send
        # And no permanent debit: a subsequent explicit pay still succeeds (not already-paid).
        paid = _pay_tool(config_dir).run(message_id=MESSAGE_ID)
        assert paid.startswith("status: payment-submitted — ")

    @respx.mock
    def test_pay_never_raises_on_error(self, config_dir):
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(404, json={"detail": "message not found"})
        )
        out = _pay_tool(config_dir).run(message_id=MESSAGE_ID)
        assert isinstance(out, str)
        assert "Not found" in out


# =====================================================================
# RineFulfillTool
# =====================================================================


def _payment_wire(config_dir, *, quote_ref=QUOTE_ID):
    """A real HPKE-sealed rine.v1.x402_payment carrying a parseable PaymentPayload.

    The signed body stamps the answered quote's id into
    ``extensions[X402_QUOTE_REF_KEY]``: the payee settles against THAT quote's terms,
    never this echoed ``accepted`` block. Pass ``quote_ref=None`` for a payment that
    binds to no quote.
    """
    payload = {
        "x402Version": 1,
        "scheme": "exact",
        "network": NETWORK,
        "accepted": {
            "scheme": "exact",
            "network": NETWORK,
            "amount": "100000",
            "asset": EURC_ASSET,
            "payTo": PAY_TO,
        },
        # The SIGNED value must cover the quoted tier (payee-side underpayment guard).
        "payload": {"authorization": {"from": PAYER_ADDR, "to": PAY_TO, "value": "100000"}},
        "extensions": {X402_QUOTE_REF_KEY: quote_ref} if quote_ref else {},
    }
    return hpke_self_encrypt(config_dir, payload)


def _mock_payment(config_dir, *, quote_ref=QUOTE_ID):
    respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
        return_value=httpx.Response(
            200,
            json=message_payload(
                encryption_version=EncryptionVersion.HPKE_V1,
                encrypted_payload=_payment_wire(config_dir, quote_ref=quote_ref),
                msg_type=MessageType.X402_PAYMENT,
            ),
        )
    )


def _mock_referenced_quote(config_dir):
    """Serve the payee's OWN issued quote at QUOTE_ID — what the payment's ref binds to.

    A payee re-reads its own send, so ``GET /messages/{id}`` returns the row tagged
    ``direction: "sent"`` with the self-sealed copy; the recipient-sealed
    ``encrypted_payload`` is opaque to the payee, so the quote can only resolve
    through the self-seal.
    """
    respx.get(f"{API_URL}/messages/{QUOTE_ID}").mock(
        return_value=httpx.Response(
            200,
            json={
                **message_payload(
                    encryption_version=EncryptionVersion.HPKE_V1,
                    encrypted_payload="ZHVtbXk=",  # payer's copy — unreadable to the payee
                    msg_id=QUOTE_ID,
                    msg_type=MessageType.X402_PAYMENT_REQUIRED,
                ),
                "direction": "sent",
                "self_encrypted_payload": _quote_wire(config_dir),
            },
        )
    )


def _fulfill_tool(config_dir):
    return RineFulfillTool(
        config_dir=config_dir, api_url=API_URL, agent=AGENT_ID, facilitator_url=FAC_URL
    )


class TestRineFulfillTool:
    @respx.mock
    def test_fulfill_valid_payment_settles_and_sends_receipt(self, config_dir):
        mock_token()
        mock_agents()
        _mock_payment(config_dir)
        _mock_referenced_quote(config_dir)
        _mock_self_keys(config_dir)
        verify = respx.post(f"{FAC_URL}/verify").mock(
            return_value=httpx.Response(200, json={"isValid": True})
        )
        settle = respx.post(f"{FAC_URL}/settle").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "transaction": "0xdeadbeef",
                    "network": "base-sepolia",  # v1 slug echoed — must survive verbatim
                    "payer": PAYER_ADDR,
                },
            )
        )
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        out = _fulfill_tool(config_dir).run(message_id=MESSAGE_ID)

        assert verify.called and settle.called and reply.called
        assert "settled on base-sepolia" in out
        assert "0xdeadbeef" in out
        # settle-first ordering: verify precedes settle.
        paths = [
            c.request.url.path for c in respx.calls if c.request.url.host == "facilitator.test"
        ]
        assert paths == ["/verify", "/settle"]

    @respx.mock
    def test_fulfill_receipt_marker_is_status_and_rail_only(self, config_dir):
        """Invariant 2: the receipt marker carries {status, rail} only, no economics."""
        mock_token()
        mock_agents()
        _mock_payment(config_dir)
        _mock_referenced_quote(config_dir)
        _mock_self_keys(config_dir)
        respx.post(f"{FAC_URL}/verify").mock(
            return_value=httpx.Response(200, json={"isValid": True})
        )
        respx.post(f"{FAC_URL}/settle").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "transaction": "0xabc",
                    "network": "base-sepolia",
                    "payer": PAYER_ADDR,
                    "amount": "100000",  # economics in the body must NOT reach the marker
                },
            )
        )
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        _fulfill_tool(config_dir).run(message_id=MESSAGE_ID)

        marker = json.loads(reply.calls[0].request.content)["metadata"]["rine.x402"]
        assert set(marker) == {"status", "rail"}
        assert marker["status"] == "payment-completed"

    @respx.mock
    def test_fulfill_failed_verification_skips_settle_and_sends_failure_receipt(self, config_dir):
        mock_token()
        mock_agents()
        _mock_payment(config_dir)
        _mock_referenced_quote(config_dir)
        _mock_self_keys(config_dir)
        respx.post(f"{FAC_URL}/verify").mock(
            return_value=httpx.Response(
                200, json={"isValid": False, "invalidReason": "insufficient_funds"}
            )
        )
        settle = respx.post(f"{FAC_URL}/settle").mock(return_value=httpx.Response(200, json={}))
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        out = _fulfill_tool(config_dir).run(message_id=MESSAGE_ID)

        assert not settle.called  # settle skipped on failed verification
        assert reply.called  # payment-failed receipt still threaded back
        assert "failed verification" in out
        assert "insufficient_funds" in out
        marker = json.loads(reply.calls[0].request.content)["metadata"]["rine.x402"]
        assert marker["status"] == "payment-failed"

    @respx.mock
    def test_fulfill_payment_without_quote_ref_refuses_before_facilitator(self, config_dir):
        """Fail closed: a payment bound to no payee-issued quote never reaches settle."""
        mock_token()
        mock_agents()
        _mock_payment(config_dir, quote_ref=None)
        _mock_self_keys(config_dir)
        verify = respx.post(f"{FAC_URL}/verify")
        settle = respx.post(f"{FAC_URL}/settle")
        reply = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        out = _fulfill_tool(config_dir).run(message_id=MESSAGE_ID)

        assert not verify.called and not settle.called  # facilitator never contacted
        assert "no-quote" in out
        marker = json.loads(reply.calls[0].request.content)["metadata"]["rine.x402"]
        assert marker["status"] == "payment-failed"

    @respx.mock
    def test_fulfill_wrong_type_message_returns_not_payment_required(self, config_dir):
        mock_token()
        mock_agents()
        _mock_self_keys(config_dir)
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(
                200,
                json=message_payload(
                    encryption_version=EncryptionVersion.HPKE_V1,
                    encrypted_payload=hpke_self_encrypt(config_dir, {"text": "hi"}),
                    msg_type=MessageType.TASK_REQUEST,
                ),
            )
        )

        out = _fulfill_tool(config_dir).run(message_id=MESSAGE_ID)

        assert out.startswith("status: not-payment-required — ")

    def test_fulfill_unknown_facilitator_preset_returns_error_string(self, config_dir):
        """A preset needing a base_url (x402-rs) with none set → clear error, no raise."""
        tool = RineFulfillTool(
            config_dir=config_dir, api_url=API_URL, agent=AGENT_ID, facilitator="x402-rs"
        )
        out = tool.run(message_id=MESSAGE_ID)
        assert isinstance(out, str)
        assert "base_url" in out
