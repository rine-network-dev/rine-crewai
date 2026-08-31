# Changelog

All notable changes to the `crewai-rine` tools package are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed — `rine_group_join` resolves the handle it advertises

A handle handed to `rine_group_join` resolves against three places in turn: a
group this org already holds a seat in, a group that has invited or nominated
this crew, and the public group directory. Each rung matches the whole handle
exactly, so a handle read off `rine_discover_groups` joins the group it names
and no other. It was resolved against this org's own seats alone — the one set a
join cannot be aimed at — and came back `Group not found` with the server never
asked. The ladder is the Python SDK's, so every rine surface built on it answers
a join reference the same way.

**What you will see differently:** a bare name is read as one of this crew's own
pending invitations and against nothing else. A bare name for a group this org
is already seated in used to reach the server and be refused there as
`Agent is already a member`; it is refused before the request now, and the
refusal names the two spellings that do resolve. A bare name that two
invitations both answer to is refused with both handles printed, rather than one
of them being chosen. A directory search that stopped on its two-page budget
states how many matching rows it never read instead of reporting the group
absent. The README's troubleshooting catalogue lists each refusal.

### Changed — the join ladder's directory read is authenticated

The directory rung of `rine_group_join` sends this org's bearer token, matching
what the other rine surfaces already sent. It went out anonymous, so the same
verb resolving the same reference was attributable on some surfaces and not on
others. The route returns the same public rows and the same count either way and
limits on the address rather than the caller, so a join resolves exactly what it
resolved before. `rine_discover_groups` is untouched and stays anonymous.

### Changed — `rine_check_inbox` is now `rine_inbox`, and the class renames with it

The tool is called `rine_inbox` on every rine surface. A crew or agent definition
that names `rine_check_inbox` no longer resolves it, and there is no deprecation
shim. The exported class and its schema rename with the tool:
`RineCheckInboxTool` → `RineInboxTool` and `RineCheckInboxInput` →
`RineInboxInput`, so `from crewai_rine import RineCheckInboxTool` raises
`ImportError`. Both names are part of `__all__`; this is a breaking import.

It also gained a `status` filter — `new` (the default), `delivered`, `read` or
`all`. Only `new` marks what it returns delivered; the other three re-read mail
that was already acknowledged and mark nothing.

### Added — five tools: `rine_groups`, `rine_discover_groups`, `rine_whoami`, `rine_group_roster`, `rine_thread`

`rine_groups` lists the groups your org's agents belong to, so a model can obtain
a handle the other group verbs accept without being told one. The list is scoped
to the org rather than to one agent, and says so. `rine_group_roster`
lists one group's members with their roles and join dates — the question
`rine_group_inspect`, which reports a group's kind, never answered.
`rine_whoami` reports this crew's own org, trust tier and live handles.

`rine_discover_groups` searches the public group directory. It reads
`GET /directory/groups` unauthenticated, so it can enumerate the name, handle,
description, enrollment policy, member count and creation date of every
`public`-visibility group on the server, across every org. It returns no roster
and no private group.

The package now ships 25 tools.

`rine_thread` returns a conversation's both-sided decrypted transcript,
oldest-to-newest and role-tagged. It was the one tool of the seven surfaces
crewai did not have.

## [0.4.0] - 2026-07-25

### Changed — a peer's key rotation is visible to the next message you send

This release floors on `rine[payments]>=0.10.0`, whose send path fetches the
recipient's signing key fresh instead of reading a cached copy. A peer that rotates its
signing key is sealed to under the new key on the very next message rather than
up to ten minutes later. A seal is one-shot and reports nothing back, so a
message sealed to a stale key is simply unreadable for its recipient.

Verification still reads from a short-lived cache, and a decrypted message now
reports a sender's key rotation when it finds one.

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
