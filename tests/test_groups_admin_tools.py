"""Behavioral tests for the group-administration tools.

Covers `rine_group_requests`, `rine_group_vote`, `rine_group_leave`,
`rine_group_sync` and `rine_group_reclaim` — the verbs a crew needs once it is
inside a group. Only
the rine HTTP boundary is mocked; the SDK itself is real, so a body that stopped
calling the right SDK method fails here rather than passing against a double.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from crewai_rine import (
    RineGroupLeaveTool,
    RineGroupReclaimTool,
    RineGroupRequestsTool,
    RineGroupSyncTool,
    RineGroupVoteTool,
)

from .conftest import (
    AGENT_ID,
    API_URL,
    GROUP_ID,
    OTHER_AGENT_ID,
    group_payload,
    mock_agents,
    mock_group_row,
    mock_token,
)

REQUEST_ID = "ffffffff-0000-1111-2222-333333333333"


def _request_row(*, status: str = "pending", expires_at: str | None = None, **over) -> dict:
    """One row as the server sends it, with the whole voting picture on it."""
    return {
        "id": REQUEST_ID,
        "group_id": GROUP_ID,
        "agent_id": OTHER_AGENT_ID,
        "invited_by": None,
        "message": None,
        "status": status,
        "expires_at": expires_at,
        "created_at": "2024-01-01T00:00:00Z",
        "resolved_at": None,
        "quorum_total_members": 9,
        "electorate_size": 3,
        "approvals": 1,
        "denials": 0,
        "approvals_needed": 1,
        "denials_needed": 2,
        "your_vote": None,
        "you_may_vote": True,
        "you_may_vote_reason": "in_electorate",
        **over,
    }


class TestRineGroupRequestsTool:
    @respx.mock
    def test_requests_renders_a_line_per_row_and_names_the_filter(self, config_dir):
        mock_token()
        mock_agents()
        mock_group_row()
        route = respx.get(f"{API_URL}/groups/{GROUP_ID}/requests").mock(
            return_value=httpx.Response(200, json={"items": [_request_row()], "total": 1})
        )

        out = RineGroupRequestsTool(config_dir=config_dir, api_url=API_URL).run(
            group=GROUP_ID, outstanding="live"
        )

        assert route.calls.last.request.url.params["outstanding"] == "live"
        assert f"Outstanding in `{GROUP_ID}` (live): 1" in out
        assert REQUEST_ID in out
        assert OTHER_AGENT_ID in out

    @respx.mock
    def test_requests_defaults_to_the_vote_queue(self, config_dir):
        """`pending` is the only population a vote can act on, so it is the default."""
        mock_token()
        mock_agents()
        mock_group_row()
        route = respx.get(f"{API_URL}/groups/{GROUP_ID}/requests").mock(
            return_value=httpx.Response(200, json={"items": [], "total": 0})
        )

        out = RineGroupRequestsTool(config_dir=config_dir, api_url=API_URL).run(group=GROUP_ID)

        assert route.calls.last.request.url.params["outstanding"] == "pending"
        assert out == f"Nothing outstanding in `{GROUP_ID}` under filter `pending`."


class TestRineGroupVoteTool:
    @respx.mock
    def test_vote_deny_reports_the_choice_and_the_new_status(self, config_dir):
        mock_token()
        mock_agents()
        mock_group_row()
        route = respx.post(f"{API_URL}/groups/{GROUP_ID}/requests/{REQUEST_ID}/vote").mock(
            return_value=httpx.Response(
                200, json={"request_id": REQUEST_ID, "your_vote": "deny", "status": "denied"}
            )
        )

        out = RineGroupVoteTool(config_dir=config_dir, api_url=API_URL).run(
            group=GROUP_ID, request_id=REQUEST_ID, vote="deny"
        )

        assert route.calls.last.request.content == b'{"vote":"deny"}'
        assert out == (
            f"Vote `deny` recorded on request `{REQUEST_ID}` in `{GROUP_ID}`. "
            "Request status: `denied`."
        )

    @respx.mock
    def test_vote_that_admits_the_applicant_does_not_claim_a_leaf_it_never_minted(
        self, config_dir
    ):
        """The sentence follows the SDK's ``mls_seated``, not the server's status.

        This group runs sender keys, so there is no ratchet tree and the seat is
        correctly skipped — and on the status alone the tool asserted that a leaf
        and a Welcome had been minted anyway.
        """
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/groups/{GROUP_ID}/requests").mock(
            return_value=httpx.Response(200, json={"items": [_request_row()], "total": 1})
        )
        respx.get(f"{API_URL}/groups/{GROUP_ID}").mock(
            return_value=httpx.Response(200, json=group_payload())
        )
        respx.post(f"{API_URL}/groups/{GROUP_ID}/requests/{REQUEST_ID}/vote").mock(
            return_value=httpx.Response(
                200, json={"request_id": REQUEST_ID, "your_vote": "approve", "status": "approved"}
            )
        )

        out = RineGroupVoteTool(config_dir=config_dir, api_url=API_URL).run(
            group=GROUP_ID, request_id=REQUEST_ID, vote="approve"
        )

        assert "Request status: `approved`." in out
        assert "The applicant is now a member." in out
        assert "resume-admission" in out
        assert "were minted as part of this vote" not in out

    @respx.mock
    def test_vote_refusal_is_surfaced_as_a_tool_message(self, config_dir):
        mock_token()
        mock_agents()
        mock_group_row()
        respx.post(f"{API_URL}/groups/{GROUP_ID}/requests/{REQUEST_ID}/vote").mock(
            return_value=httpx.Response(409, json={"detail": "Already voted"})
        )

        out = RineGroupVoteTool(config_dir=config_dir, api_url=API_URL).run(
            group=GROUP_ID, request_id=REQUEST_ID, vote="deny"
        )

        assert "Already voted" in out


class TestRineGroupLeaveTool:
    @respx.mock
    def test_leave_removes_the_acting_agent_and_prints_the_sdk_sentence(self, config_dir):
        """A leave names THIS agent, and the sentence about it is the SDK's."""
        mock_token()
        mock_agents()
        mock_group_row()
        route = respx.delete(f"{API_URL}/groups/{GROUP_ID}/members/{AGENT_ID}").mock(
            return_value=httpx.Response(204)
        )

        out = RineGroupLeaveTool(config_dir=config_dir, api_url=API_URL).run(group=GROUP_ID)

        assert route.called
        assert "left" in out.lower()


class TestRineGroupSyncTool:
    @respx.mock
    def test_sync_recovers_a_sender_key_group_rather_than_refusing_it(self, config_dir):
        """A sender-key group has no epoch chain, so the repair is its waiting keys.

        The scan is this crew's whole distribution inbox, and an empty one is the
        ordinary answer for a member whose peers have all already sent — not an
        error and not a refusal.
        """
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/groups/{GROUP_ID}").mock(
            return_value=httpx.Response(200, json=group_payload())
        )
        respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(200, json={"items": []})
        )

        out = RineGroupSyncTool(config_dir=config_dir, api_url=API_URL).run(group=GROUP_ID)

        assert "nothing new was installed" in out
        assert "nothing was committed and this cost reads only" in out
        # The old refusal is gone: the verb acts here rather than sending the
        # crew to an admin.
        assert "There is nothing to sync here." not in out

    @respx.mock
    def test_sync_scans_a_group_whose_tree_was_never_founded_and_names_this_crews_verb(
        self, config_dir
    ):
        """🔴 Closed, meant to run MLS, never founded — and it reads as open.

        The four MLS columns of this row are an open group's, so the latch alone
        ran a distribution scan and reported an ordinary sender-key catch-up,
        saying nothing about a group that never got the encryption it was
        created for.
        The fix is the warning, not a refusal: this group is not frozen, its
        sends take the sender-key branch and are accepted, so keys for it really
        are waiting and the scan must still be paid for. The remedy this crew
        can actually reach is ``rine_group_reclaim``; ``resume_admission`` is
        not a tool it has.
        """
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/groups/{GROUP_ID}").mock(
            return_value=httpx.Response(200, json=group_payload(mls_enabled=True))
        )
        inbox = respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(200, json={"items": []})
        )

        out = RineGroupSyncTool(config_dir=config_dir, api_url=API_URL).run(group=GROUP_ID)

        head, note = out.split("\n")
        # The head is the ordinary sender-key answer, byte for byte.
        assert "nothing new was installed" in head
        assert "ratchet tree was never founded" in note
        assert "Run rine_group_reclaim on it" in note
        # The scan really ran — refusing ahead of it cost the crew those keys.
        assert inbox.called

    @respx.mock
    def test_sync_recovers_a_legitimately_open_group_rather_than_refusing_it(self, config_dir):
        """🔴 The regression the never-founded arm most plausibly causes.

        An open group carries the SAME four MLS columns as the never-founded
        closed one — null latch, no init in flight — and it also carries
        ``mls_enabled=True``, because the server column defaults true for every
        group. Only ``enrollment_policy`` tells them apart, and this is the row
        that proves the arm reads it. The other sender-key test here cannot: its
        ``mls_enabled=False`` row is one no shipped client creates.
        """
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/groups/{GROUP_ID}").mock(
            return_value=httpx.Response(
                200, json=group_payload(enrollment_policy="open", mls_enabled=True)
            )
        )
        inbox = respx.get(f"{API_URL}/agents/{AGENT_ID}/messages").mock(
            return_value=httpx.Response(200, json={"items": []})
        )

        out = RineGroupSyncTool(config_dir=config_dir, api_url=API_URL).run(group=GROUP_ID)

        assert "nothing new was installed" in out
        assert "ratchet tree was never founded" not in out
        # The scan is the recovery here, so it must have been paid for.
        assert inbox.called


class TestRineGroupReclaimTool:
    @respx.mock
    def test_reclaim_refuses_a_sender_key_group_in_the_sdk_words(self, config_dir):
        """An open group runs sender keys, so it has no ratchet tree to clear."""
        mock_token()
        mock_agents()
        respx.get(f"{API_URL}/groups/{GROUP_ID}").mock(
            return_value=httpx.Response(200, json={**group_payload(), "enrollment_policy": "open"})
        )

        out = RineGroupReclaimTool(config_dir=config_dir, api_url=API_URL).run(group=GROUP_ID)

        assert "no ratchet tree to seat anyone in" in out


class TestRequestRowReportsEligibility:
    """The row says whether this crew's own vote counts, in the shared words."""

    @respx.mock
    def _row_for(self, config_dir, **over) -> str:
        mock_token()
        mock_agents()
        mock_group_row()
        respx.get(f"{API_URL}/groups/{GROUP_ID}/requests").mock(
            return_value=httpx.Response(200, json={"items": [_request_row(**over)], "total": 1})
        )
        return RineGroupRequestsTool(config_dir=config_dir, api_url=API_URL).run(group=GROUP_ID)

    def test_requests_when_the_caller_is_in_the_electorate_then_the_row_says_so(self, config_dir):
        out = self._row_for(config_dir)

        assert "1 approved, 1 more needed of 3 eligible" in out
        assert "you can vote" in out

    def test_requests_when_the_server_reports_a_denial_bar_then_the_row_prints_it(
        self, config_dir
    ):
        """The denial half is the SDK's, so this pins that it is INHERITED, not written here.

        This surface renders through ``rine.render_request_row``; a new wire
        field therefore reaches it for free, and this asserts that it did. The
        denial bar moves on the same shrinking electorate the approval bar does,
        so a request can be refused by a denial that fell short when it was cast
        — an outcome a row showing approvals alone cannot account for.
        """
        out = self._row_for(config_dir)

        assert "0 denied, 2 more would refuse it" in out

    def test_requests_when_the_server_reports_no_denial_bar_then_it_is_a_dash_not_a_zero(
        self, config_dir
    ):
        """A number the server did not send is an em dash; zero would be a claim."""
        out = self._row_for(config_dir, denials_needed=None)

        assert "0 denied, — more would refuse it" in out

    def test_requests_when_the_caller_joined_later_then_the_row_says_why_not(self, config_dir):
        out = self._row_for(
            config_dir, you_may_vote=False, you_may_vote_reason="not_in_electorate"
        )

        assert "you cannot vote: you were not in the group when this was filed" in out

    def test_requests_when_the_electorate_has_left_then_the_row_says_nobody_is(self, config_dir):
        out = self._row_for(
            config_dir,
            electorate_size=0,
            approvals_needed=None,
            you_may_vote=False,
            you_may_vote_reason="electorate_empty",
        )

        assert out.count("nobody eligible is still in the group") == 1


@pytest.mark.parametrize(
    ("tool_class", "name"),
    [
        (RineGroupRequestsTool, "rine_group_requests"),
        (RineGroupVoteTool, "rine_group_vote"),
        (RineGroupLeaveTool, "rine_group_leave"),
        (RineGroupSyncTool, "rine_group_sync"),
        (RineGroupReclaimTool, "rine_group_reclaim"),
    ],
)
def test_tool_is_exported_from_the_package_under_its_wire_name(tool_class, name):
    """A class the package does not export is a tool nobody can attach."""
    assert tool_class().name == name


class TestVoteRendering:
    """The one sentence that can only be told from the SDK's own seat report."""

    @staticmethod
    def _render(seated: bool | None, status: str = "approved") -> str:
        from rine.types import VoteResponse

        from crewai_rine.tools._admission import render_vote

        return render_vote(
            GROUP_ID,
            REQUEST_ID,
            "approve",
            VoteResponse(
                request_id=REQUEST_ID,
                your_vote="approve",
                status=status,
                mls_seated=seated,
            ),
        )

    def test_render_when_the_sdk_seated_the_applicant_then_the_welcome_is_claimed(self):
        assert "ratchet-tree leaf and Welcome were minted" in self._render(True)

    @pytest.mark.parametrize("seated", [False, None])
    def test_render_when_no_leaf_was_minted_then_none_is_claimed(self, seated):
        out = self._render(seated)
        assert "were minted as part of this vote" not in out
        assert "resume-admission" in out

    def test_render_when_a_carried_vote_awaits_consent_then_it_claims_no_membership(self):
        """`invited` is a fourth answer, and it is not `approved`.

        A member may nominate an agent, and the electorate approving that
        nomination is not the agent agreeing to be in the group — so the request
        resolves to a spendable invitation and seats nobody. Falling through to
        the bare status line reports a decided request as an unrecognised word.
        """
        out = self._render(None, status="invited")

        assert "spendable invitation instead of a seat" in out
        assert "The applicant is now a member" not in out
        assert "resume-admission" not in out

    def test_render_when_the_request_is_denied_then_nothing_is_claimed_about_a_seat(self):
        """The control arm: the branch that claims a seat stays keyed on `approved` alone."""
        out = self._render(None, status="denied")

        assert out.endswith("Request status: `denied`.")
