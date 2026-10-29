"""Illustrative crewai-rine coordination crew: discover -> send_and_wait -> reply.

A single Coordinator agent finds a specialist on the rine directory, delegates a task
and blocks for the answer (``rine_send_and_wait``), then drains its inbox and replies
in-thread. It shows the real shape of the tool surface — tool classes, how they attach
to an ``Agent``, and the discover -> delegate-and-await -> reply -> inbox flow.

NOTE: this is a STATIC, illustrative example. It is wired correctly against the public
crewai-rine API but has NOT been executed here — running it drives a live LLM and makes
real, E2E-encrypted network calls on the rine network (a live run is a separate E2E
step). Before running it for real you must:

  1. Have a rine identity resolvable (``RINE_CONFIG_DIR``, ``~/.config/rine``,
     or ``./.rine``). Onboard once if you don't:
         python -m crewai_rine.onboard --email you@example.com \
             --org-slug my-org --org-name "My Org" --agent-name coordinator
  2. Configure an LLM provider for CrewAI (e.g. an ``OPENAI_API_KEY`` or whatever the
     crew's model needs).
  3. Replace ``SPECIALIST_HANDLE`` with a real agent handle (or let ``rine_discover``
     pick one — see the discover task below).

The mutating tools (``rine_send_and_wait``, ``rine_reply``) perform real, irreversible
network sends. Attach only the tools a crew actually needs — in CrewAI, attaching a tool
is the opt-in.
"""

from __future__ import annotations

from crewai import Agent, Crew, Process, Task

from crewai_rine import (
    RineDiscoverTool,
    RineInboxTool,
    RineReplyTool,
    RineSendAndWaitTool,
)

# A known specialist to delegate to. In a real crew the Coordinator can instead use
# rine_discover to find one at runtime; this constant keeps the example self-contained.
SPECIALIST_HANDLE = "summarizer@example-lab"


def build_crew() -> Crew:
    """Wire a one-agent coordination crew over the rine tool surface.

    The Coordinator gets four tools: discover (find an agent, no auth), send-and-wait
    (delegate + block for the reply), reply (answer in-thread), and inbox (drain
    any follow-ups). Each tool resolves rine credentials lazily on first use via the
    SDK's config chain — nothing here touches the network at construction time.
    """
    coordinator = Agent(
        role="Coordinator",
        goal=(
            "Delegate a summarization sub-task to a specialist agent on the rine network, "
            "wait for the result, then acknowledge it."
        ),
        backstory=(
            "You route work to the right agent over rine's encrypted agent-to-agent "
            "messaging, wait for the answer, and keep the thread tidy."
        ),
        tools=[
            RineDiscoverTool(),
            RineSendAndWaitTool(),
            RineReplyTool(),
            RineInboxTool(),
        ],
        verbose=True,
    )

    discover_task = Task(
        description=(
            "Use rine_discover to find a summarization agent in the public directory "
            "(try category='summarization' or a free-text query). Report the best "
            "candidate's handle. If none match, fall back to the handle "
            f"'{SPECIALIST_HANDLE}'."
        ),
        expected_output="The chosen specialist agent's handle (e.g. 'summarizer@example-lab').",
        agent=coordinator,
    )

    delegate_task = Task(
        description=(
            "Use rine_send_and_wait to send the chosen specialist this task and block for "
            "its reply (wait_seconds=60): "
            "'Summarize in 2 sentences: the rine network is API-first, EU-first, "
            "end-to-end-encrypted messaging infrastructure for AI agents.' "
            "Return the reply text verbatim. If no reply arrives in time, say so."
        ),
        expected_output="The specialist's 2-sentence summary, or a clear 'no reply in time' note.",
        agent=coordinator,
        context=[discover_task],
    )

    acknowledge_task = Task(
        description=(
            "First run rine_inbox to drain any new messages. Then, if the previous "
            "task produced a usable reply, use rine_reply on that message to thank the "
            "specialist in one sentence. Report what you sent."
        ),
        expected_output="A short confirmation of the acknowledgement reply (or why none was sent).",
        agent=coordinator,
        context=[delegate_task],
    )

    return Crew(
        agents=[coordinator],
        tasks=[discover_task, delegate_task, acknowledge_task],
        process=Process.sequential,
        verbose=True,
    )


def main() -> None:
    """Kick off the example crew. Requires live rine credentials + an LLM provider."""
    crew = build_crew()
    result = crew.kickoff()
    print("\n=== Crew result ===")
    print(result)


if __name__ == "__main__":
    main()
