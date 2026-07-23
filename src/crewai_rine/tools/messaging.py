"""1:1 + group messaging tools.

Five ``BaseTool``s over ``SyncRineClient``: send, send-and-wait, check-inbox, read,
reply. Group messaging is not a separate tool — a ``to`` starting with ``#`` routes
through ``RineSendTool`` and group mail lands in the inbox/read path.
Every ``_run`` maps SDK errors to a string via ``_format_error`` — never a raw
traceback, never ciphertext. Argument schemas live in ``_schemas.py``.
"""

import logging

from pydantic import BaseModel
from rine import UnsupportedTargetError

from crewai_rine._client import BaseRineTool, _format_error
from crewai_rine._format import (
    _verified_note,
    render_inbox,
    render_message_body,
    render_single_message,
)
from crewai_rine.tools._schemas import (
    RineCheckInboxInput,
    RineReadInput,
    RineReplyInput,
    RineSendAndWaitInput,
    RineSendInput,
)

logger = logging.getLogger("crewai_rine.messaging")


class RineSendTool(BaseRineTool):
    """Send an encrypted 1:1 or group message on the rine network."""

    name: str = "rine_send"
    description: str = (
        "Send an encrypted message to another agent (or a group) on the rine network. "
        "Performs a real, irreversible network send. Use to='handle@org' for 1:1 or "
        "to='#group@org' for a group."
    )
    args_schema: type[BaseModel] = RineSendInput

    def _run(
        self,
        to: str,
        body: str,
        message_type: str = "rine.v1.task_request",
        idempotency_key: str | None = None,
    ) -> str:
        try:
            msg = self._get_client().send(
                to,
                {"text": body},
                message_type=message_type,
                idempotency_key=idempotency_key,
            )
        except Exception as err:  # noqa: BLE001 — surface as a tool message, never crash
            return _format_error(err)
        return f"Sent message {msg.id} to {to} (conversation {msg.conversation_id})."


class RineSendAndWaitTool(BaseRineTool):
    """Send a message and block until a reply arrives or the timeout elapses."""

    name: str = "rine_send_and_wait"
    description: str = (
        "Send a message to another agent and block until they reply or the timeout "
        "elapses. The delegate-and-await primitive. Performs a real network send. "
        "1:1 only (not groups)."
    )
    args_schema: type[BaseModel] = RineSendAndWaitInput

    def _run(
        self,
        to: str,
        body: str,
        wait_seconds: int = 30,
        message_type: str = "rine.v1.task_request",
    ) -> str:
        try:
            result = self._get_client().send_and_wait(
                to,
                {"text": body},
                timeout=float(wait_seconds),
                message_type=message_type,
            )
        except UnsupportedTargetError:
            return "send_and_wait is 1:1 only; use rine_send for groups."
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        if result.reply is not None:
            body_text = render_message_body(result.reply)
            if result.reply.decrypt_error is None:
                return f"Reply from {to}: {body_text} ({_verified_note(result.reply)})"
            return f"Reply from {to}: {body_text}"
        return f"Sent {result.sent.id}; no reply within {wait_seconds}s."


class RineCheckInboxTool(BaseRineTool):
    """Fetch NEW (undelivered) messages and mark them delivered."""

    name: str = "rine_check_inbox"
    description: str = (
        "Check the rine inbox for NEW (undelivered) messages and return their decrypted "
        "contents. Marks them delivered so the next check returns only newer mail."
    )
    args_schema: type[BaseModel] = RineCheckInboxInput

    def _run(self, limit: int = 20) -> str:
        client = self._get_client()
        ack_failed = False
        try:
            page = client.inbox(status="new", limit=limit, agent=self.agent)
            messages = list(page)
            deliverable = [str(m.id) for m in messages if m.decrypt_error is None]
            if deliverable:
                # mark_delivered resolves the same agent inbox() used (public SDK
                # surface). Best-effort: an ack failure must never lose the read —
                # but it IS surfaced (WARNING log + a note) so the LLM knows these
                # messages may reappear on the next poll.
                try:
                    client.mark_delivered(deliverable, agent=self.agent)
                except Exception:  # noqa: BLE001 — ack is best-effort, keep the messages
                    ack_failed = True
                    logger.warning(
                        "rine_check_inbox: mark_delivered failed; %d message(s) may reappear",
                        len(deliverable),
                        exc_info=True,
                    )
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        rendered = render_inbox(messages)
        if ack_failed:
            rendered += " (note: could not mark delivered; these may reappear)"
        return rendered


class RineReadTool(BaseRineTool):
    """Fetch and decrypt a single rine message by id."""

    name: str = "rine_read"
    description: str = "Fetch and decrypt a single rine message by id."
    args_schema: type[BaseModel] = RineReadInput

    def _run(self, message_id: str) -> str:
        try:
            msg = self._get_client().read(message_id, agent=self.agent)
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return render_single_message(msg)


class RineReplyTool(BaseRineTool):
    """Reply in-thread to a rine message."""

    name: str = "rine_reply"
    description: str = (
        "Reply in-thread to a rine message. Performs a real, irreversible network send."
    )
    args_schema: type[BaseModel] = RineReplyInput

    def _run(
        self,
        message_id: str,
        body: str,
        message_type: str = "rine.v1.task_response",
    ) -> str:
        try:
            reply = self._get_client().reply(
                message_id, {"text": body}, message_type=message_type, agent=self.agent
            )
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return f"Replied to {message_id} -> {reply.id}."


__all__ = [
    "RineSendTool",
    "RineSendAndWaitTool",
    "RineCheckInboxTool",
    "RineReadTool",
    "RineReplyTool",
]
