# Base image for the CONTAINED crewai-rine dev/test rig.
# crewai and its heavy dependency tree (chromadb, lancedb, openai, opentelemetry, ...)
# install INSIDE the container only (into a venv on a named volume; see
# ../compose.crewai.yml). NOTHING crewai-related is ever installed on the host — same
# rule as the OpenClaw rig. The local rine-sdk and rine-crewai are bind-mounted and
# installed editable, so the rig consumes UNRELEASED SDK fixes (Phase-0) directly.
FROM python:3.12-slim

# git for any VCS deps; build-essential for native wheels (tokenizers, chromadb extras).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git build-essential ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# uv: fast resolver/installer; populates the named-volume venv on first run.
RUN pip install --no-cache-dir uv

# The venv lives on a NAMED VOLUME (mounted at /opt/venv in compose) so heavy deps are
# cached across runs and never land on the host tree. Activate it for every command.
ENV VENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CI=true

WORKDIR /work/rine-crewai

# Idempotent bootstrap: ensure the named-volume venv exists and the bind-mounted
# rine-sdk + crewai-rine[dev] are installed editable into it, then exec the command.
# `crewai-rine` pulls crewai>=1.14,<2.0 + the full heavy tree; `rine` resolves to the
# bind-mounted ./rine-sdk (editable) — overriding the published floor for dev/tests.
COPY entrypoint.sh /usr/local/bin/rig-entrypoint
RUN chmod +x /usr/local/bin/rig-entrypoint

ENTRYPOINT ["rig-entrypoint"]
CMD ["python", "-c", "import crewai, crewai_rine; print('crewai', crewai.__version__, '/ crewai_rine', crewai_rine.__version__)"]
