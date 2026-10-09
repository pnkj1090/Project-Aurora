import pytest
from app.services.tasks import InMemoryTaskStore, TaskManager, TaskPriority
from app.services.commitments import InMemoryCommitmentStore, CommitmentManager, CommitmentPriority
from app.services.context.providers.task import TaskContextProvider
from app.services.context.providers.commitment import CommitmentContextProvider
from app.services.context.engine import ContextEngine


@pytest.mark.asyncio
async def test_context_providers_user_scoping_and_isolation():
    task_store = InMemoryTaskStore()
    task_manager = TaskManager(store=task_store)

    cmt_store = InMemoryCommitmentStore()
    cmt_manager = CommitmentManager(store=cmt_store)

    task_provider = TaskContextProvider(task_manager=task_manager)
    cmt_provider = CommitmentContextProvider(commitment_manager=cmt_manager)

    # Seed User Alpha
    await task_manager.create_task(
        title="Alpha Task: Launch website",
        user_id="user_alpha",
        priority=TaskPriority.HIGH
    )
    await cmt_manager.create_commitment(
        title="Alpha Commitment: Meet with mentor",
        user_id="user_alpha",
        priority=TaskPriority.HIGH
    )

    # Seed User Beta
    await task_manager.create_task(
        title="Beta Task: Organize garage",
        user_id="user_beta",
        priority=TaskPriority.LOW
    )
    await cmt_manager.create_commitment(
        title="Beta Commitment: Return library book",
        user_id="user_beta",
        priority=TaskPriority.LOW
    )

    # 1. Query context for User Alpha
    alpha_task_block = await task_provider.get_context(user_id="user_alpha")
    assert alpha_task_block is not None
    assert "Alpha Task: Launch website" in alpha_task_block.content
    assert "Beta Task: Organize garage" not in alpha_task_block.content
    assert "<user_tasks>" in alpha_task_block.content

    alpha_cmt_block = await cmt_provider.get_context(user_id="user_alpha")
    assert alpha_cmt_block is not None
    assert "Alpha Commitment: Meet with mentor" in alpha_cmt_block.content
    assert "Beta Commitment: Return library book" not in alpha_cmt_block.content
    assert "<user_commitments>" in alpha_cmt_block.content

    # 2. Query context for User Beta
    beta_task_block = await task_provider.get_context(user_id="user_beta")
    assert beta_task_block is not None
    assert "Beta Task: Organize garage" in beta_task_block.content
    assert "Alpha Task: Launch website" not in beta_task_block.content

    beta_cmt_block = await cmt_provider.get_context(user_id="user_beta")
    assert beta_cmt_block is not None
    assert "Beta Commitment: Return library book" in beta_cmt_block.content
    assert "Alpha Commitment: Meet with mentor" not in beta_cmt_block.content

    # 3. Context Engine end-to-end assembly isolation
    engine = ContextEngine(providers=[task_provider, cmt_provider])

    alpha_prompt = await engine.build_system_prompt(user_id="user_alpha")
    assert "Alpha Task: Launch website" in alpha_prompt
    assert "Alpha Commitment: Meet with mentor" in alpha_prompt
    assert "Beta Task: Organize garage" not in alpha_prompt
    assert "Beta Commitment: Return library book" not in alpha_prompt

    beta_prompt = await engine.build_system_prompt(user_id="user_beta")
    assert "Beta Task: Organize garage" in beta_prompt
    assert "Beta Commitment: Return library book" in beta_prompt
    assert "Alpha Task: Launch website" not in beta_prompt
    assert "Alpha Commitment: Meet with mentor" not in beta_prompt
