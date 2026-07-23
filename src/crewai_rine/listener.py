"""Opt-in crew-lifecycle event listener.

``RineNotificationListener`` hooks CrewAI's event bus and sends a rine message on
crew kickoff / completion / failure. It is the proof that *native* beats *MCP*: a
listener wires into the Python process; an MCP server physically cannot.

Activation is opt-in by the CrewAI rule — the developer must **instantiate** the
listener (``RineNotificationListener(to="ops@acme")``). ``BaseEventListener.__init__``
registers the handlers immediately, but the ``SyncRineClient`` is built lazily on the
first fired event (never at import/``__init__``), keeping import side-effect-free.

A notification failure must NEVER crash a crew: every handler swallows its own
exceptions and logs at debug.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from crewai.events import BaseEventListener
from crewai.events.types.crew_events import (
    CrewKickoffCompletedEvent,
    CrewKickoffFailedEvent,
    CrewKickoffStartedEvent,
)

from crewai_rine._client import build_client

if TYPE_CHECKING:
    from crewai.events.event_bus import CrewAIEventsBus
    from rine import SyncRineClient

logger = logging.getLogger("crewai_rine.listener")

_MAX_SUMMARY = 500


class RineNotificationListener(BaseEventListener):
    """Send a rine message when a crew starts, completes, or fails.

    Args:
        to: rine handle/UUID to notify.
        config_dir: Override the SDK config dir (else the SDK resolution chain).
        api_url: Override the API base URL.
        agent: Agent name/handle/UUID to send as (multi-agent orgs).
        on: Which lifecycle events fire — any of ``"started"``, ``"completed"``,
            ``"failed"``. Defaults to completion + failure.
    """

    def __init__(
        self,
        to: str,
        *,
        config_dir: str | None = None,
        api_url: str | None = None,
        agent: str | None = None,
        on: tuple[str, ...] = ("completed", "failed"),
    ) -> None:
        """Configure the listener and register its handlers on the event bus."""
        self._to = to
        self._config_dir = config_dir
        self._api_url = api_url
        self._agent = agent
        self._on = on
        self._client: SyncRineClient | None = None
        super().__init__()

    def _get_client(self) -> SyncRineClient:
        """Build (once) the shared ``SyncRineClient`` from the same chain as tools."""
        if self._client is None:
            self._client = build_client(
                config_dir=self._config_dir, api_url=self._api_url, agent=self._agent
            )
        return self._client

    def _notify(self, text: str) -> None:
        """Best-effort send. A notification failure must not crash the crew."""
        try:
            self._get_client().send(self._to, {"text": text}, message_type="rine.v1.text")
        except Exception:  # noqa: BLE001 — never propagate out of an event handler
            logger.debug("rine notification send failed", exc_info=True)

    def setup_listeners(self, crewai_event_bus: CrewAIEventsBus) -> None:  # noqa: D102
        if "started" in self._on:

            @crewai_event_bus.on(CrewKickoffStartedEvent)
            def _on_started(source: Any, event: CrewKickoffStartedEvent) -> None:
                crew = event.crew_name or "crew"
                self._notify(f"{crew} started.")

        if "completed" in self._on:

            @crewai_event_bus.on(CrewKickoffCompletedEvent)
            def _on_completed(source: Any, event: CrewKickoffCompletedEvent) -> None:
                crew = event.crew_name or "crew"
                summary = str(event.output)[:_MAX_SUMMARY] if event.output is not None else ""
                self._notify(f"{crew} completed: {summary}")

        if "failed" in self._on:

            @crewai_event_bus.on(CrewKickoffFailedEvent)
            def _on_failed(source: Any, event: CrewKickoffFailedEvent) -> None:
                crew = event.crew_name or "crew"
                self._notify(f"{crew} failed: {event.error}")


__all__ = ["RineNotificationListener"]
