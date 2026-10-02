"""Web adapter: the same runtime, with durable, explicit approval decisions."""

from pathlib import Path

from nexus.chat import ChatControls
from nexus.core.agent import Agent
from nexus.core.approval import ApprovalRequired
from nexus.skills.read_file import ReadFileSkill

READ_ONLY_TOOLS = {
    "inspect_workspace",
    "list_directory",
    "read_file",
    "search_code",
    "recall",
}


async def run_turn(
    controls: ChatControls,
    prompt: str,
    progress=None,
    approval_id: str | None = None,
    approval: bool | None = None,
):
    await controls.memory.init_db()
    consumed = False

    async def confirm(skill, arguments):
        nonlocal consumed
        if isinstance(skill, ReadFileSkill):
            path = skill.resolve_path(arguments.get("filepath", ""))
            if path is not None and not skill._is_sensitive(path):
                return True
        # A button authorizes exactly one pending call, never later requests.
        # Re-read under Agent.chat's lock; a stale browser decision must not
        # authorize a different call created by another client.
        current = await controls.memory.load_state()
        pending = current.pending_approval if current else None
        if (
            pending
            and approval_id == pending["id"]
            and skill.name == pending["function"]["name"]
            and approval is not None
            and not consumed
        ):
            consumed = True
            return approval
        raise ApprovalRequired()

    agent = Agent(
        model_name=controls.model,
        db_path=controls.database,
        session_id=controls.session,
        workspace_root=Path(controls.workspace),
        max_steps=controls.max_steps,
        cost_budget_usd=controls.cost_budget,
        enabled_skill_names=READ_ONLY_TOOLS if controls.read_only else None,
        progress_callback=progress,
        approval_callback=confirm,
    )
    agent.session_approval = controls.session_approval and not controls.read_only
    try:
        await agent.init()
        reply = await agent.chat(prompt)
        state = await agent.memory.load_state()
        history = await agent.memory.get_history()
        return reply, state.public_payload() if state else None, history
    finally:
        await agent.close()
