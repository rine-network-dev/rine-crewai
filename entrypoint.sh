#!/usr/bin/env bash
# Idempotent rig bootstrap for the contained crewai-rine test rig.
#
# Ensures the named-volume venv at $VENV exists and holds the bind-mounted local
# rine-sdk (editable) + crewai-rine[dev] (editable, pulling crewai + the heavy tree),
# then exec's whatever command compose/`run` passed. The venv lives on a named volume,
# so the (slow) first install is cached for every later run and never touches the host.
set -euo pipefail

# Private, non-exported sentinel path. Named `_RIG_INSTALLED` (not `MARKER`) so a test
# that passes `-e MARKER=…` into the rig is not clobbered: reassigning an already-exported
# env var here would leak the rig value into the exec'd child process env.
VENV="${VENV:-/opt/venv}"
_RIG_INSTALLED="$VENV/.rig-installed"

if [ ! -f "$_RIG_INSTALLED" ]; then
  echo ">>> [rig] bootstrapping venv at $VENV (first run — installs crewai + deps; slow)" >&2
  # Resolve uv to its absolute path BEFORE clearing the venv. `uv venv --clear` wipes
  # $VENV/bin (which is first on PATH); bash's command-hash would otherwise cache a now
  # -deleted $VENV/bin/uv and fail the next call ("No such file or directory") on a
  # re-bootstrap (marker removed but volume still populated).
  UV="$(command -v uv)"
  "$UV" venv --clear "$VENV"
  # Install the bind-mounted LOCAL rine-sdk editable WITH the `payments` extra — the
  # rine_pay / rine_fulfill tools sign EIP-3009 authorizations (wallet), which needs
  # those deps at runtime. Installing rine editable first satisfies crewai-rine's
  # `rine[payments]>=0.8.0` floor from local source (worktree rine-sdk is 0.8.0), so no
  # PyPI resolution/override is needed. (A `--override rine @ file://…` here would STRIP
  # the `[payments]` extra during resolution — the reason it is deliberately absent.)
  "$UV" pip install --python "$VENV/bin/python" \
    -e "/work/rine-sdk[payments]" \
    -e "/work/rine-crewai[dev]"
  touch "$_RIG_INSTALLED"
  echo ">>> [rig] bootstrap complete" >&2
fi

exec "$@"
