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
  # Resolve uv to its absolute path BEFORE clearing the venv, and resolve it from
  # OUTSIDE the venv. `uv venv --clear` wipes $VENV/bin, which is first on PATH — and
  # crewai's own dependency tree installs `uv` in there, so a plain `command -v uv`
  # finds $VENV/bin/uv and the very next line deletes the binary it just named
  # ("No such file or directory", MEASURED on a re-bootstrap with the marker removed
  # and the volume still populated). The image's uv is the one that must survive the
  # clear, so look for it where the Dockerfile's `pip install uv` put it.
  UV="$(PATH=/usr/local/bin:/usr/bin:/bin command -v uv)"
  "$UV" venv --clear "$VENV"
  # `rine` MUST be the bind-mounted local SDK installed EDITABLE, so the rig always runs the
  # CURRENT SDK source and an unreleased SDK change can actually fail these tests. The local
  # path satisfies crewai-rine's `rine[payments]>=…` floor, so nothing is fetched from PyPI.
  # Two ways to silently lose editability — neither may be reintroduced, because the venv
  # lives on a named volume and a non-editable install freezes an SDK snapshot for the life
  # of that volume:
  #   1. `--override rine @ file:///work/rine-sdk` — an override outranks `-e`, copies, and
  #      STRIPS the `[payments]` extra during resolution. Deliberately absent.
  #   2. `-e "/work/rine-sdk[payments]"` — uv drops editability when a path carries extras.
  #   3. installing both projects in ONE `uv pip install` — crewai-rine[dev] requires
  #      `rine[payments]>=…`, and that dependency edge outranks the sibling `-e`, so rine is
  #      copied.
  # Hence two steps: pin the SDK editable first (NO extra on the path), then crewai-rine[dev]
  # finds `rine` already satisfied and leaves it alone. eth-account (EIP-3009 signing for
  # rine_pay / rine_fulfill) arrives through crewai-rine's own `rine[payments]` dependency
  # edge, which uv expands against the already-installed editable SDK — so the SDK path
  # itself never needs an extra.
  # rine-mls (rine's compiled MLS + ML-KEM core) comes from the prebuilt wheelhouse
  # mounted at /work/wheels, never from source: the SDK's `[tool.uv.sources]` points it
  # at a crate, and building that crate needs a Rust toolchain no rig image carries.
  # `--no-index` is what proves the wheel came from there and not from PyPI, where
  # rine-mls is not published yet.
  "$UV" pip install --python "$VENV/bin/python" \
    --no-index --find-links /work/wheels rine-mls
  # ...so the SDK install ignores that one source and takes the wheel above as
  # satisfying its `rine-mls` dependency. Every other source mapping still applies.
  "$UV" pip install --python "$VENV/bin/python" --no-sources-package rine-mls \
    -e /work/rine-sdk
  "$UV" pip install --python "$VENV/bin/python" -e "/work/rine-crewai[dev]"
  touch "$_RIG_INSTALLED"
  echo ">>> [rig] bootstrap complete" >&2
fi

exec "$@"
