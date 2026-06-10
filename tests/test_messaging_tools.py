"""Behavioral tests for the messaging tools (SPEC §6.1-6.5, §9.1).

Each tool is exercised through its public CrewAI interface (``tool.run(...)``);
assertions are on the human-readable return string and observable HTTP. Only the
rine HTTP boundary is mocked (respx) — the SDK crypto/config/HTTP is real.

The ciphertext-never-leaks invariant (REQ-07/REQ-09/REQ-10) is asserted by
encrypting a marker plaintext with a real HPKE round-trip and checking the raw
ciphertext string never appears in any tool return.
"""

from __future__ import annotations

import json
import logging

import httpx
import respx
from rine._constants import EncryptionVersion, MessageType

from crewai_rine import (
    RineCheckInboxTool,
    RineReadTool,
    RineReplyTool,
    RineSendAndWaitTool,
    RineSendTool,
)

from .conftest import (
    AGENT_ID,
    API_URL,
    CONVERSATION_ID,
    GROUP_HANDLE,
    MESSAGE_ID,
    OTHER_AGENT_ID,
    OTHER_HANDLE,
    REPLY_ID,
    group_payload,
    hpke_self_encrypt,
    message_payload,
    mock_agents,
    mock_recipient_keys,
    mock_sender_keys_unverifiable,
    mock_token,
    mock_webfinger,
    sent_message_payload,
)


def _send_tool(config_dir):
    return RineSendTool(config_dir=config_dir, api_url=API_URL)


# =====================================================================
# RineSendTool
# =====================================================================


class TestRineSendTool:
    @respx.mock
    def test_send_to_agent_when_recipient_exists_returns_confirmation(
        self, config_dir, other_keys
    ):
        mock_token()
        mock_agents()
        mock_webfinger()
        mock_recipient_keys(config_dir)
        route = respx.post(f"{API_URL}/messages").mock(
            return_value=httpx.Response(200, json=sent_message_payload())
        )

        out = _send_tool(config_dir).run(to=OTHER_HANDLE, body="hello there")

        assert route.called
        assert f"Sent message {MESSAGE_ID}" in out
        assert OTHER_HANDLE in out
        assert CONVERSATION_ID in out

    @respx.mock
    def test_send_auto_hpke_encrypts_body_on_the_wire(self, config_dir, other_keys):
        """The posted body carries HPKE ciphertext, not the plaintext 'hello there'."""
        mock_token()
        mock_agents()
        mock_webfinger()
        mock_recipient_keys(config_dir)
        route = respx.post(f"{API_URL}/messages").mock(
            return_value=httpx.Response(200, json=sent_message_payload())
        )

        _send_tool(config_dir).run(to=OTHER_HANDLE, body="hello there")

        body = json.loads(route.calls[0].request.content)
        assert body["encryption_version"] == EncryptionVersion.HPKE_V1
        assert "encrypted_payload" in body
        assert "hello there" not in route.calls[0].request.content.decode()

    @respx.mock
    def test_send_forwards_idempotency_key_and_message_type(self, config_dir, other_keys):
        """L2: idempotency_key → 'Idempotency-Key' header; message_type → body 'type'.

        A refactor that dropped the dedup key would cause duplicate sends on retry;
        one that corrupted the type would mis-route. Both are silently untested
        otherwise — this pins them on the POST /messages request.
        """
        mock_token()
        mock_agents()
        mock_webfinger()
        mock_recipient_keys(config_dir)
        route = respx.post(f"{API_URL}/messages").mock(
            return_value=httpx.Response(200, json=sent_message_payload())
        )

        _send_tool(config_dir).run(
            to=OTHER_HANDLE,
            body="hi",
            idempotency_key="k1",
            message_type="rine.v1.custom",
        )

        request = route.calls[0].request
        assert request.headers.get("Idempotency-Key") == "k1"
        assert json.loads(request.content)["type"] == "rine.v1.custom"

    @respx.mock
    def test_send_to_bare_name_returns_target_not_found_string(self, config_dir):
        """A bare (non-handle, non-UUID) target is rejected by the SDK as not found."""
        mock_token()
        mock_agents()

        out = _send_tool(config_dir).run(to="nonexistent", body="hi")

        assert "Not found" in out
        assert "rine_discover" in out

    @respx.mock
    def test_send_to_missing_agent_returns_not_found_string(self, config_dir):
        """A 404 from the recipient-key fetch maps to the §6.14 not-found string."""
        mock_token()
        mock_agents()
        mock_webfinger()
        respx.get(f"{API_URL}/agents/{OTHER_AGENT_ID}/keys").mock(
            return_value=httpx.Response(404, json={"detail": "agent not found"})
        )

        out = _send_tool(config_dir).run(to=OTHER_HANDLE, body="hi")

        assert "Not found" in out
        assert "agent not found" in out

    @respx.mock
    def test_send_to_mls_group_returns_honest_ceiling_string(self, config_dir):
        """A send to a TS-created MLS group surfaces the MLS ceiling, never silently fails."""
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/groups").mock(
            return_value=httpx.Response(
                200,
                json={"items": [group_payload(mls_group_id="mls-abc", mls_pending=False)]},
            )
        )

        out = _send_tool(config_dir).run(to=GROUP_HANDLE, body="team update")

        assert "MLS" in out
        assert "sender-key" in out

    @respx.mock
    def test_send_never_raises_on_error(self, config_dir):
        """``_run`` must return a string even on error, never raise out of the tool."""
        mock_token()
        mock_agents()
        out = _send_tool(config_dir).run(to="nonexistent", body="hi")
        assert isinstance(out, str)


# =====================================================================
# RineSendAndWaitTool
# =====================================================================


class TestRineSendAndWaitTool:
    @respx.mock
    def test_send_and_wait_when_reply_arrives_returns_plaintext(self, config_dir, other_keys):
        mock_token()
        mock_agents()
        mock_webfinger()
        mock_recipient_keys(config_dir)
        # Reply is HPKE-encrypted to the caller agent so it decrypts to readable text.
        reply_ct = hpke_self_encrypt(config_dir, {"text": "ack: done"})
        respx.get(f"{API_URL}/agents/{AGENT_ID}/keys").mock(
            return_value=httpx.Response(200, json=_self_keys_json(config_dir))
        )
        respx.post(f"{API_URL}/messages/sync").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": sent_message_payload(),
                    "reply": message_payload(
                        encryption_version=EncryptionVersion.HPKE_V1,
                        encrypted_payload=reply_ct,
                        msg_id=REPLY_ID,
                        from_agent_id=AGENT_ID,
                        msg_type=MessageType.TASK_RESPONSE,
                    ),
                    "conversation_id": CONVERSATION_ID,
                    "status": "completed",
                },
            )
        )

        out = RineSendAndWaitTool(config_dir=config_dir, api_url=API_URL).run(
            to=OTHER_HANDLE, body="do it", wait_seconds=5
        )

        assert f"Reply from {OTHER_HANDLE}" in out
        assert "ack: done" in out
        # Exact verification note (M5): a fetchable sender key → 'signature verified'.
        assert "(signature verified)" in out
        # ciphertext never leaks into the return
        assert reply_ct not in out

    @respx.mock
    def test_send_and_wait_unverifiable_reply_renders_signature_unverifiable(
        self, config_dir, other_keys
    ):
        """M5: an unverifiable reply renders '(signature unverifiable)', not 'verified'.

        The reply body still decrypts (recipient key local); the sender SIGNING key
        fetch 503s → verified=False, status='unverifiable'. Pins the exact rendered
        note so a hardcoded 'verified' suffix is caught.
        """
        mock_token()
        mock_agents()
        mock_webfinger()
        mock_recipient_keys(config_dir)
        reply_ct = hpke_self_encrypt(config_dir, {"text": "maybe done"})
        # Sender signing-key fetch fails → reply is unverifiable (but still decrypts).
        mock_sender_keys_unverifiable()  # GET /agents/{AGENT_ID}/keys → 503
        respx.post(f"{API_URL}/messages/sync").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": sent_message_payload(),
                    "reply": message_payload(
                        encryption_version=EncryptionVersion.HPKE_V1,
                        encrypted_payload=reply_ct,
                        msg_id=REPLY_ID,
                        from_agent_id=AGENT_ID,
                        msg_type=MessageType.TASK_RESPONSE,
                    ),
                    "conversation_id": CONVERSATION_ID,
                    "status": "completed",
                },
            )
        )

        out = RineSendAndWaitTool(config_dir=config_dir, api_url=API_URL).run(
            to=OTHER_HANDLE, body="do it", wait_seconds=5
        )

        assert "maybe done" in out
        assert "(signature unverifiable)" in out
        assert "signature verified" not in out
        assert reply_ct not in out

    @respx.mock
    def test_send_and_wait_when_no_reply_returns_timeout_string(self, config_dir, other_keys):
        mock_token()
        mock_agents()
        mock_webfinger()
        mock_recipient_keys(config_dir)
        respx.post(f"{API_URL}/messages/sync").mock(
            return_value=httpx.Response(
                200,
                json={
                    "message": sent_message_payload(),
                    "reply": None,
                    "conversation_id": CONVERSATION_ID,
                    "status": "timeout",
                },
            )
        )

        out = RineSendAndWaitTool(config_dir=config_dir, api_url=API_URL).run(
            to=OTHER_HANDLE, body="do it", wait_seconds=5
        )

        assert f"Sent {MESSAGE_ID}" in out
        assert "no reply within 5s" in out

    def test_send_and_wait_to_group_returns_one_to_one_only_string(self, config_dir):
        """Group handle trips the SDK's typed UnsupportedTargetError (caught pre-request)."""
        out = RineSendAndWaitTool(config_dir=config_dir, api_url=API_URL).run(
            to=GROUP_HANDLE, body="nope"
        )
        assert out == "send_and_wait is 1:1 only; use rine_send for groups."

    def test_send_and_wait_to_group_never_raises(self, config_dir):
        out = RineSendAndWaitTool(config_dir=config_dir, api_url=API_URL).run(
            to="#team@acme.rine.network", body="nope"
        )
        assert isinstance(out, str)


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


# =====================================================================
# RineCheckInboxTool
# =====================================================================


class TestRineCheckInboxTool:
    @respx.mock
    def test_check_inbox_with_new_messages_returns_decrypted_list(self, config_dir):
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/agents/{AGENT_ID}/keys").mock(
            return_value=httpx.Response(200, json=_self_keys_json(config_dir))
        )
        ciphertext = hpke_self_encrypt(config_dir, {"text": "inbox hello"})
        inbox_route = respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        message_payload(
                            encryption_version=EncryptionVersion.HPKE_V1,
                            encrypted_payload=ciphertext,
                        )
                    ],
                    "total": 1,
                },
            )
        )
        respx.post(f"{API_URL}/agents/{AGENT_ID}/messages/mark-delivered").mock(
            return_value=httpx.Response(200, json={"marked": 1})
        )

        out = RineCheckInboxTool(config_dir=config_dir, api_url=API_URL).run()

        assert "inbox hello" in out
        assert "1 new message" in out
        # status=new must be forwarded (P0-1)
        assert b"status=new" in inbox_route.calls[0].request.url.query
        # ciphertext invariant
        assert ciphertext not in out

    @respx.mock
    def test_check_inbox_marks_decryptable_messages_delivered(self, config_dir):
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/agents/{AGENT_ID}/keys").mock(
            return_value=httpx.Response(200, json=_self_keys_json(config_dir))
        )
        ciphertext = hpke_self_encrypt(config_dir, {"text": "ack me"})
        respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        message_payload(
                            encryption_version=EncryptionVersion.HPKE_V1,
                            encrypted_payload=ciphertext,
                        )
                    ],
                    "total": 1,
                },
            )
        )
        ack = respx.post(f"{API_URL}/agents/{AGENT_ID}/messages/mark-delivered").mock(
            return_value=httpx.Response(200, json={"marked": 1})
        )

        RineCheckInboxTool(config_dir=config_dir, api_url=API_URL).run()

        assert ack.called
        body = json.loads(ack.calls[0].request.content)
        assert body == {"message_ids": [MESSAGE_ID]}

    @respx.mock
    def test_check_inbox_ack_failure_warns_and_notes_without_losing_read(self, config_dir, caplog):
        """L5: a transient ack (mark_delivered) failure must NOT lose the read.

        The message is still returned, a WARNING is logged, and a note is appended so
        the LLM knows the mail may reappear (SPEC §6.3 best-effort promise).
        """
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/agents/{AGENT_ID}/keys").mock(
            return_value=httpx.Response(200, json=_self_keys_json(config_dir))
        )
        ciphertext = hpke_self_encrypt(config_dir, {"text": "still here"})
        respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        message_payload(
                            encryption_version=EncryptionVersion.HPKE_V1,
                            encrypted_payload=ciphertext,
                        )
                    ],
                    "total": 1,
                },
            )
        )
        # The ack endpoint 500s → mark_delivered raises; the tool must swallow + note.
        respx.post(f"{API_URL}/agents/{AGENT_ID}/messages/mark-delivered").mock(
            return_value=httpx.Response(500, json={"detail": "ack down"})
        )

        with caplog.at_level(logging.WARNING, logger="crewai_rine.messaging"):
            out = RineCheckInboxTool(config_dir=config_dir, api_url=API_URL).run()

        # The read is NOT lost despite the failed ack.
        assert "still here" in out
        # The LLM is told these may reappear (the §6.3 honesty note).
        assert "could not mark delivered" in out
        # And the failure is observable at WARNING, not silently swallowed.
        assert any("mark_delivered failed" in r.message for r in caplog.records), (
            "expected a WARNING when the ack fails"
        )

    @respx.mock
    def test_check_inbox_with_no_messages_returns_empty_string(self, config_dir):
        mock_token()
        mock_agents()
        ack = respx.post(f"{API_URL}/agents/{AGENT_ID}/messages/mark-delivered").mock(
            return_value=httpx.Response(200, json={"marked": 0})
        )
        respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(200, json={"items": [], "total": 0})
        )

        out = RineCheckInboxTool(config_dir=config_dir, api_url=API_URL).run()

        assert out == "No new messages."
        # nothing to ack → mark-delivered not called
        assert not ack.called

    @respx.mock
    def test_check_inbox_does_not_ack_undecryptable_message(self, config_dir):
        """An MLS message surfaces its decrypt_error and is NOT marked delivered."""
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        message_payload(
                            encryption_version=EncryptionVersion.MLS_V1,
                            encrypted_payload="Q0lQSEVSVEVYVA==",
                        )
                    ],
                    "total": 1,
                },
            )
        )
        ack = respx.post(f"{API_URL}/agents/{AGENT_ID}/messages/mark-delivered").mock(
            return_value=httpx.Response(200, json={"marked": 0})
        )

        out = RineCheckInboxTool(config_dir=config_dir, api_url=API_URL).run()

        assert "MLS" in out
        assert "Q0lQSEVSVEVYVA==" not in out  # ciphertext never leaked
        assert not ack.called  # undecryptable → not acked (would re-read next time)


# =====================================================================
# RineReadTool
# =====================================================================


class TestRineReadTool:
    @respx.mock
    def test_read_hpke_message_returns_plaintext_and_verification(self, config_dir):
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/agents/{AGENT_ID}/keys").mock(
            return_value=httpx.Response(200, json=_self_keys_json(config_dir))
        )
        ciphertext = hpke_self_encrypt(config_dir, {"text": "read me"})
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(
                200,
                json=message_payload(
                    encryption_version=EncryptionVersion.HPKE_V1,
                    encrypted_payload=ciphertext,
                ),
            )
        )

        out = RineReadTool(config_dir=config_dir, api_url=API_URL).run(message_id=MESSAGE_ID)

        assert "read me" in out
        # Exact rendered signal — not just the 'verification:' label (M5): a valid
        # round-trip with a fetchable sender key renders 'signature verified'.
        assert "verification: signature verified" in out
        assert ciphertext not in out  # ciphertext invariant

    @respx.mock
    def test_read_unverifiable_message_renders_signature_unverifiable(self, config_dir):
        """M5: an unfetchable sender key renders 'signature unverifiable', not 'verified'.

        The message body still decrypts (recipient key is local); only the sender's
        SIGNING key fetch fails (503), so the SDK yields verified=False,
        verification_status='unverifiable'. This is the security-honesty signal the
        LLM consumes — pinning the exact string catches a hardcoded 'verified' note.
        """
        mock_token()
        mock_agents()
        mock_sender_keys_unverifiable()  # GET /agents/{AGENT_ID}/keys → 503
        ciphertext = hpke_self_encrypt(config_dir, {"text": "trust but verify"})
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(
                200,
                json=message_payload(
                    encryption_version=EncryptionVersion.HPKE_V1,
                    encrypted_payload=ciphertext,
                ),
            )
        )

        out = RineReadTool(config_dir=config_dir, api_url=API_URL).run(message_id=MESSAGE_ID)

        assert "trust but verify" in out
        assert "verification: signature unverifiable" in out
        assert "signature verified" not in out
        assert ciphertext not in out

    @respx.mock
    def test_read_mls_message_surfaces_decrypt_error_not_ciphertext(self, config_dir):
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(
                200,
                json=message_payload(
                    encryption_version=EncryptionVersion.MLS_V1,
                    encrypted_payload="Q0lQSEVSVEVYVA==",
                ),
            )
        )

        out = RineReadTool(config_dir=config_dir, api_url=API_URL).run(message_id=MESSAGE_ID)

        assert "MLS" in out
        assert "[unreadable]" in out
        assert "Q0lQSEVSVEVYVA==" not in out

    @respx.mock
    def test_read_missing_message_returns_not_found_string(self, config_dir):
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(404, json={"detail": "message not found"})
        )

        out = RineReadTool(config_dir=config_dir, api_url=API_URL).run(message_id=MESSAGE_ID)

        assert "Not found" in out
        assert "message not found" in out


# =====================================================================
# RineReplyTool
# =====================================================================


class TestRineReplyTool:
    @respx.mock
    def test_reply_to_message_returns_reply_id(self, config_dir, other_keys):
        mock_token()
        mock_agents()
        # reply() GETs the original to find the sender, then fetches their key.
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(
                200,
                json=message_payload(
                    encryption_version=EncryptionVersion.HPKE_V1,
                    encrypted_payload="ZHVtbXk=",
                    from_agent_id=OTHER_AGENT_ID,
                    to_agent_id=AGENT_ID,
                ),
            )
        )
        mock_recipient_keys(config_dir)
        route = respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(200, json={**sent_message_payload(), "id": REPLY_ID})
        )

        out = RineReplyTool(config_dir=config_dir, api_url=API_URL).run(
            message_id=MESSAGE_ID, body="on it"
        )

        assert route.called
        assert f"Replied to {MESSAGE_ID} -> {REPLY_ID}." == out

    @respx.mock
    def test_reply_never_returns_ciphertext(self, config_dir, other_keys):
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/messages/{MESSAGE_ID}").mock(
            return_value=httpx.Response(
                200,
                json=message_payload(
                    encryption_version=EncryptionVersion.HPKE_V1,
                    encrypted_payload="ZHVtbXk=",
                    from_agent_id=OTHER_AGENT_ID,
                    to_agent_id=AGENT_ID,
                ),
            )
        )
        mock_recipient_keys(config_dir)
        respx.post(f"{API_URL}/messages/{MESSAGE_ID}/reply").mock(
            return_value=httpx.Response(
                200,
                json={**sent_message_payload(), "id": REPLY_ID, "encrypted_payload": "U0VDUkVU"},
            )
        )

        out = RineReplyTool(config_dir=config_dir, api_url=API_URL).run(
            message_id=MESSAGE_ID, body="on it"
        )

        assert "U0VDUkVU" not in out
        assert "encrypted_payload" not in out
