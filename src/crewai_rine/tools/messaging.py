"""1:1 + group messaging tools.

Six ``BaseTool``s over ``SyncRineClient``: send, send-and-wait, inbox, read,
reply, thread. Group messaging is not a separate tool — a ``to`` starting with ``#``
routes through ``RineSendTool`` and group mail lands in the inbox/read path.
Every ``_run`` maps SDK errors to a string via ``_format_error`` — never a raw
traceback, never ciphertext. Argument schemas live in ``_schemas.py``.
"""

import logging

from pydantic import BaseModel
from rine import TRANSCRIPT_REFERENCE_REQUIRED, UnsupportedTargetError
from rine.sender_key_words import SenderKeyRefusal

from crewai_rine._client import _GROUP_ON_WAIT_MESSAGE, BaseRineTool, _format_error
from crewai_rine._format import (
    _verified_note,
    render_inbox,
    render_message_body,
    render_sent,
    render_single_message,
    render_thread,
)
from crewai_rine.tools._schemas import (
    InboxSlice,
    RineInboxInput,
    RineReadInput,
    RineReplyInput,
    RineSendAndWaitInput,
    RineSendInput,
    RineThreadInput,
)

logger = logging.getLogger("crewai_rine.messaging")


class RineSendTool(BaseRineTool):
    """Send an encrypted 1:1 or group message on the rine network."""

    name: str = "rine_send"
    description: str = (
        "Send an encrypted message to another agent (or a group) on the rine network. "
        "Performs a real, irreversible network send. Name a group by its own name — "
        "to='logistics' — or by its full handle, to='#logistics@acme.rine.network'. "
        "Use to='kofi@acme.rine.network' for 1:1."
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
        return render_sent(msg, to)


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
            return _GROUP_ON_WAIT_MESSAGE
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        if result.reply is not None:
            # One reply, one render: a fresh refusal explains a missing sender key in full.
            body_text = render_message_body(result.reply, SenderKeyRefusal())
            if result.reply.decrypt_error is None:
                return f"Reply from {to}: {body_text} ({_verified_note(result.reply)})"
            return f"Reply from {to}: {body_text}"
        return f"Sent {result.sent.id}; no reply within {wait_seconds}s."


class RineInboxTool(BaseRineTool):
    """Read a slice of the inbox; the NEW slice is marked delivered."""

    name: str = "rine_inbox"
    description: str = (
        "Read the rine inbox and return the decrypted contents. `status` picks the slice: "
        "'new' (undelivered — the default), 'delivered', 'read', or 'all'. The 'new' slice "
        "is marked delivered so the next check returns only newer mail; the other slices "
        "are a look-back and change nothing."
    )
    args_schema: type[BaseModel] = RineInboxInput

    def _run(self, status: InboxSlice = "new", limit: int = 20) -> str:
        client = self._get_client()
        ack_failed = False
        # "all" is a TOOL-layer value, not a server one: the route's status is a
        # three-value Literal and omitting it is how "every state" is expressed.
        # Passing the word through would 422 at the network.
        resolved = None if status == "all" else status
        try:
            page = client.inbox(status=resolved, limit=limit, agent=self.agent)
            messages = list(page)
            # Auto-ack belongs to the NEW slice alone. A 'delivered'/'read'/'all'
            # read is a look-back, and re-marking a message that is already read
            # would walk its state backwards.
            deliverable = (
                [str(m.id) for m in messages if m.decrypt_error is None]
                if resolved == "new"
                else []
            )
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
                        "rine_inbox: mark_delivered failed; %d message(s) may reappear",
                        len(deliverable),
                        exc_info=True,
                    )
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        rendered = render_inbox(messages, status=status)
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


class RineThreadTool(BaseRineTool):
    """Fetch the both-sided, decrypted transcript of a conversation or a group.

    A group is named here the way every other group tool takes it. Which of the
    two references was named — both, neither, or a conversation the wire cannot
    read as a UUID — is decided by the SDK (:mod:`rine.thread_words`) and reaches
    the crew as a ``ConfigError`` this tool renders like any other refusal; a
    second copy of those sentences here is how three wrappers come to answer one
    mistake three ways.
    """

    name: str = "rine_thread"
    description: str = (
        "Fetch the both-sided, decrypted transcript of a conversation or a group by "
        f"reference. {TRANSCRIPT_REFERENCE_REQUIRED} "
        "Returns every turn (yours and the peer's) oldest-to-newest, role-tagged. "
        "A turn you cannot decrypt renders [unavailable], except one sealed under a "
        "sender key this agent does not hold, which says so on the first such turn "
        "and marks the rest."
    )
    args_schema: type[BaseModel] = RineThreadInput

    # 🔴 ``group`` goes LAST, after ``limit``. The framework calls this through
    # ``args_schema`` by keyword, but a direct caller — and every test in this
    # package — may pass positionally, and an existing ``_run(cid, 50)`` binding
    # its window to a new second parameter is a signature break wearing an
    # additive feature. Accepting a group reference here is purely additive;
    # the argument list says so too.
    def _run(
        self,
        conversation_id: str | None = None,
        limit: int | None = None,
        group: str | None = None,
    ) -> str:
        try:
            entries = self._get_client().thread(
                conversation_id, group=group, agent=self.agent, limit=limit
            )
        except Exception as err:  # noqa: BLE001
            return _format_error(err)
        return render_thread(entries)


__all__ = [
    "RineSendTool",
    "RineSendAndWaitTool",
    "RineInboxTool",
    "RineReadTool",
    "RineReplyTool",
    "RineThreadTool",
]
