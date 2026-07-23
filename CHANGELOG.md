# Changelog

All notable changes to the `crewai-rine` tools package are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-07-23

### Added

- Two group tools — a CrewAI agent can now join a group it was invited to and
  list its pending invites, capabilities that had no tool wrapper before.

### Changed — MLS groups move to a new core; existing groups must be recreated

This release floors on `rine[payments]>=0.9.0`, whose MLS layer is the native
`rine-mls` core (post-quantum X-Wing by default) instead of `ts-mls`. It also
removes the old "Python cannot operate MLS groups" ceiling: a CrewAI agent can
now create, join, send to and read MLS groups.

**The old and new cores do not interoperate — a hard cutover, not a compatible
upgrade.** A group whose members are not all on the new core does not work.

- **Every agent's published KeyPackages are replaced** — the old pool is
  unreadable by the new core. The SDK drains and republishes on the next MLS
  operation; `client.republish_mls_key_packages(agent_id)` does it on demand.
- **Every existing MLS group must be recreated.** A group with one upgraded and
  one not-yet-upgraded member is broken, not degraded. 1:1 messages and
  sender-key groups are unaffected.
