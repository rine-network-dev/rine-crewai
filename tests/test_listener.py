"""Behavioral tests for RineNotificationListener.

The listener is driven through its real public surface: instantiate it (which
registers handlers on the crewai event bus), then ``emit`` real crew lifecycle
events and assert the observable effect — a rine ``send`` on the HTTP boundary.
Handlers are registered inside ``crewai_event_bus.scoped_handlers()`` so each
test's registration is torn down afterwards (independent tests).

Determinism (M1): crewai's event bus is a process-wide singleton whose sync
handlers run on a shared ``ThreadPoolExecutor``. A handler future leaked from one
test could still be executing (and calling into respx) when the next test asserts
on a shared route flag. The autouse ``_drain_event_bus`` fixture flushes the bus
before AND after every test in this module, and ``_emit_and_wait`` flushes after
waiting on the emit future, so no in-flight handler crosses a test boundary. The
content tests also decrypt the *actual wire body* (not a shared boolean), so an
assertion can never be satisfied by a leaked sibling send.

A notification failure must NEVER crash a crew: the failure-path tests
point the listener at a failing send and assert ``emit`` does not raise AND that a
send was actually attempted (the failing route was called) — so the swallow is
proven, not vacuously passed by a handler that never fired.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
import respx
from crewai.events import crewai_event_bus
from crewai.events.types.crew_events import (
    CrewKickoffCompletedEvent,
    CrewKickoffFailedEvent,
)

from crewai_rine import RineNotificationListener

from .conftest import (
    AGENT_ID,
    API_URL,
    OTHER_AGENT_ID,
    decrypt_self_wire,
    mock_agents,
    mock_group_miss,
    mock_recipient_keys,
    mock_token,
    mock_webfinger,
    sent_message_payload,
)

NOTIFY_HANDLE = "ops@acme.rine.network"


@pytest.fixture(autouse=True)
def _drain_event_bus():
    """Drain the shared event-bus executor before AND after every test (M1).

    crewai's bus singleton runs sync handlers on a shared ThreadPoolExecutor and
    tracks their futures in ``_pending_futures``. Flushing before the test clears
    any future leaked from a prior test; flushing after the test guarantees this
    test's handler futures complete before its respx mock tears down and before
    the next test begins — eliminating the cross-test races that made this suite
    non-deterministic. ``flush()`` is a no-op when nothing is pending.
    """
    crewai_event_bus.flush(timeout=10)
    yield
    crewai_event_bus.flush(timeout=10)


def _mock_send(config_dir):
    """Mock the full send path the listener uses (token, resolve, encrypt, POST)."""
    mock_token()
    mock_agents()
    mock_webfinger()
    mock_recipient_keys()
    return respx.post(f"{API_URL}/messages").mock(
        return_value=httpx.Response(200, json=sent_message_payload())
    )


def _mock_send_to_self(config_dir):
    """Mock a send addressed to AGENT_ID so the wire ciphertext is self-decryptable.

    The listener is pointed at the caller agent's own UUID, so no WebFinger is
    needed and the posted ``encrypted_payload`` is HPKE-sealed to a key we hold —
    letting a test decrypt the *actual notification text* off the wire.
    """
    mock_token()
    mock_agents()
    # The recipient here is a bare UUID, and a bare UUID reads the group route
    # before the agent one — see mock_group_miss for why that ordering exists.
    mock_group_miss(AGENT_ID)
    mock_recipient_keys(AGENT_ID)
    return respx.post(f"{API_URL}/messages").mock(
        return_value=httpx.Response(200, json=sent_message_payload())
    )


def _emit_and_wait(event) -> None:
    """Emit a crew event and block until the handler thread finishes.

    crewai runs sync event handlers on a ThreadPoolExecutor and returns a Future;
    a test must wait on it before asserting the handler's observable effect. The
    handler swallows its own exceptions, so the future resolving without raising
    is itself the proof that a notification failure cannot crash a crew.
    A final ``flush()`` ensures no in-flight future leaks past this call.
    """
    future = crewai_event_bus.emit("crew", event)
    if future is not None:
        future.result(timeout=10)
    crewai_event_bus.flush(timeout=10)


class TestRineNotificationListener:
    @respx.mock
    def test_listener_sends_on_completed_event(self, config_dir, other_keys):
        route = _mock_send(config_dir)

        with crewai_event_bus.scoped_handlers():
            RineNotificationListener(
                NOTIFY_HANDLE, config_dir=config_dir, api_url=API_URL, on=("completed",)
            )
            _emit_and_wait(CrewKickoffCompletedEvent(crew_name="demo", output="all done"))

        assert route.called
        body = json.loads(route.calls[0].request.content)
        assert body["type"] == "rine.v1.text"

    @respx.mock
    def test_listener_completed_notification_body_carries_crew_and_verb(self, config_dir):
        """The decrypted wire body contains the crew name + 'completed:' verb + summary."""
        route = _mock_send_to_self(config_dir)

        with crewai_event_bus.scoped_handlers():
            RineNotificationListener(
                AGENT_ID, config_dir=config_dir, api_url=API_URL, on=("completed",)
            )
            _emit_and_wait(CrewKickoffCompletedEvent(crew_name="demo", output="all done"))

        assert route.called
        wire = json.loads(route.calls[0].request.content)["encrypted_payload"]
        plaintext = decrypt_self_wire(config_dir, wire)
        # The real notification content — not just that *a* send happened.
        assert json.loads(plaintext)["text"] == "demo completed: all done"

    @respx.mock
    def test_listener_failed_notification_body_carries_crew_and_error(self, config_dir):
        """The decrypted wire body contains the crew name + 'failed:' verb + error."""
        route = _mock_send_to_self(config_dir)

        with crewai_event_bus.scoped_handlers():
            RineNotificationListener(
                AGENT_ID, config_dir=config_dir, api_url=API_URL, on=("failed",)
            )
            _emit_and_wait(CrewKickoffFailedEvent(crew_name="demo", error="boom"))

        assert route.called
        wire = json.loads(route.calls[0].request.content)["encrypted_payload"]
        plaintext = decrypt_self_wire(config_dir, wire)
        assert json.loads(plaintext)["text"] == "demo failed: boom"

    @respx.mock
    def test_listener_sends_on_failed_event(self, config_dir, other_keys):
        route = _mock_send(config_dir)

        with crewai_event_bus.scoped_handlers():
            RineNotificationListener(
                NOTIFY_HANDLE, config_dir=config_dir, api_url=API_URL, on=("failed",)
            )
            _emit_and_wait(CrewKickoffFailedEvent(crew_name="demo", error="boom"))

        assert route.called

    @respx.mock
    def test_listener_does_not_send_for_unselected_event(self, config_dir, other_keys):
        route = _mock_send(config_dir)

        with crewai_event_bus.scoped_handlers():
            RineNotificationListener(
                NOTIFY_HANDLE, config_dir=config_dir, api_url=API_URL, on=("completed",)
            )
            # only 'completed' selected → a failed event must NOT notify
            _emit_and_wait(CrewKickoffFailedEvent(crew_name="demo", error="boom"))

        assert not route.called

    @respx.mock
    def test_listener_swallows_send_failure_without_crashing_crew(self, config_dir):
        """A send failure inside the handler must NOT propagate, but it must fire.

        The recipient-key fetch 500s → send() raises internally; the handler
        swallows it. Asserting the failing route WAS called proves a send was
        actually attempted (a handler that silently never fired would also "not
        raise" — that weaker pass is what this guards against).
        """
        mock_token()
        mock_agents()
        mock_webfinger()
        keys_route = respx.get(f"{API_URL}/agents/{OTHER_AGENT_ID}/keys").mock(
            return_value=httpx.Response(500, json={"detail": "kaboom"})
        )

        with crewai_event_bus.scoped_handlers():
            RineNotificationListener(
                NOTIFY_HANDLE, config_dir=config_dir, api_url=API_URL, on=("completed",)
            )
            # Must not raise even though the send fails.
            _emit_and_wait(CrewKickoffCompletedEvent(crew_name="demo", output="done"))

        # reaching here == no exception propagated; the route call proves a send was tried.
        assert keys_route.called

    def test_listener_swallows_config_failure_without_crashing_crew(self, tmp_path, caplog):
        """A listener with no credentials at all still swallows the event cleanly.

        This variant fails before any HTTP (no creds → client build / send
        raises locally), so there is no route to assert. Instead assert the
        handler's debug-log swallow actually fired — proving the handler ran and
        caught the failure rather than silently not registering.
        """
        empty_dir = str(tmp_path / "empty")
        with (
            crewai_event_bus.scoped_handlers(),
            caplog.at_level(logging.DEBUG, logger="crewai_rine.listener"),
        ):
            RineNotificationListener(
                NOTIFY_HANDLE, config_dir=empty_dir, api_url=API_URL, on=("completed", "failed")
            )
            _emit_and_wait(CrewKickoffCompletedEvent(crew_name="demo", output="done"))
            _emit_and_wait(CrewKickoffFailedEvent(crew_name="demo", error="boom"))

        # no exception; the debug log proves the swallow path executed.
        assert any("rine notification send failed" in r.message for r in caplog.records), (
            "expected the listener's swallow-path debug log to fire"
        )
