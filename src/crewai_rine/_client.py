"""Shared config/auth resolver and tool base for crewai-rine.

One place every tool gets its ``SyncRineClient`` from. The resolution chain is the
SDK's, untouched (SPEC §7): ``RINE_CLIENT_ID``+``RINE_CLIENT_SECRET`` env →
``RINE_CONFIG_DIR`` env → ``~/.config/rine`` → ``cwd/.rine``. We do **not** reimplement
that chain — the SDK's ``SyncRineClient`` resolves it when ``config_dir``/``api_url``/
``agent`` are left at their defaults. This module only:

- declares the chain to CrewAI via ``RINE_ENV_VARS`` (metadata, not resolution);
- builds a ``SyncRineClient`` lazily on first ``_run`` (never at import/``__init__``);
- maps typed ``RineError``s to clear LLM-facing strings (``_format_error``);
- gives every tool a ``BaseRineTool`` base carrying the override fields + lazy client.

Side-effect-free import: nothing here touches the network, reads credentials, or
constructs a client at import time.
"""

from __future__ import annotations

from typing import Any

from crewai.tools import BaseTool
from crewai.tools.base_tool import EnvVar
from pydantic import PrivateAttr
from rine import (
    AuthenticationError,
    ConfigError,
    MlsUnsupportedError,
    NotFoundError,
    RateLimitError,
    RineApiError,
    RineError,
    SyncRineClient,
)

# Declared to CrewAI so operators see which vars feed the SDK's resolution chain.
# All optional — credentials may come from any rung of the chain (env creds → config dir).
# NO RINE_TOKEN: the Python SDK has no such concept (Node/MCP-only); never accept it.
RINE_ENV_VARS: list[EnvVar] = [
    EnvVar(
        name="RINE_CLIENT_ID",
        description="Rine OAuth client id (hosted / secrets-manager auth).",
        required=False,
    ),
    EnvVar(
        name="RINE_CLIENT_SECRET",
        description="Rine OAuth client secret.",
        required=False,
    ),
    EnvVar(
        name="RINE_CONFIG_DIR",
        description="Override the Rine config dir (default ~/.config/rine then ./.rine).",
        required=False,
    ),
    EnvVar(
        name="RINE_API_URL",
        description="Rine API base URL (default https://rine.network).",
        required=False,
    ),
]


def build_client(
    *, config_dir: str | None, api_url: str | None, agent: str | None
) -> SyncRineClient:
    """Build a ``SyncRineClient``, passing only the non-``None`` overrides.

    Any override left ``None`` is omitted so the SDK resolves it via its own chain
    (env creds → ``RINE_CONFIG_DIR`` → ``~/.config/rine`` → ``cwd/.rine``). Shared by
    ``BaseRineTool._get_client`` and the listener's ``_get_client`` (same contract).
    """
    kwargs: dict[str, Any] = {}
    if config_dir is not None:
        kwargs["config_dir"] = config_dir
    if api_url is not None:
        kwargs["api_url"] = api_url
    if agent is not None:
        kwargs["agent"] = agent
    return SyncRineClient(**kwargs)


def _format_error(err: Exception) -> str:
    """Map a Rine error to a clear, LLM-facing string (SPEC §6.14).

    Tool ``_run`` bodies return these strings; CrewAI surfaces them to the agent so it
    retries/continues rather than crashing. Never leak a raw traceback to a tool return.
    """
    if isinstance(err, MlsUnsupportedError):
        return (
            "This group uses MLS encryption, which the Python side can't post to. "
            "Create a sender-key group or have the TypeScript side disable MLS."
        )
    if isinstance(err, AuthenticationError):
        return (
            "Rine auth failed — set RINE_CLIENT_ID/RINE_CLIENT_SECRET or onboard "
            "(`python -m crewai_rine.onboard`)."
        )
    if isinstance(err, NotFoundError):
        return f"Not found: {err.detail}. Try rine_discover to find the right handle."
    if isinstance(err, RateLimitError):
        return f"Rate-limited; retry after {err.retry_after}s."
    if isinstance(err, ConfigError):
        return str(err)
    if isinstance(err, RineApiError):
        return f"{err.status}: {err.detail}"
    return str(err)


class BaseRineTool(BaseTool):
    """Shared base for every crewai-rine tool.

    Declares the SDK config overrides as constructor fields (per-tool, optional) and
    lazily builds a single ``SyncRineClient`` on first use. Declares ``RINE_ENV_VARS``
    so CrewAI surfaces the config-chain vars. Building the client is deferred to
    ``_get_client`` (called from ``_run``) so import and ``__init__`` stay side-effect-free.
    """

    config_dir: str | None = None
    api_url: str | None = None
    agent: str | None = None
    env_vars: list[EnvVar] = RINE_ENV_VARS

    _client: SyncRineClient | None = PrivateAttr(default=None)

    def _get_client(self) -> SyncRineClient:
        """Build (once) and return the shared ``SyncRineClient``.

        Passes only the override fields through; when they are ``None`` the SDK resolves
        them via its own chain (env creds → ``RINE_CONFIG_DIR`` → ``~/.config/rine`` →
        ``cwd/.rine``). We never reimplement that resolution here.
        """
        if self._client is None:
            self._client = build_client(
                config_dir=self.config_dir, api_url=self.api_url, agent=self.agent
            )
        return self._client


__all__ = ["BaseRineTool", "RINE_ENV_VARS", "RineError", "_format_error", "build_client"]
