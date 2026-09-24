"""
第 18 章 —— 状态与检查点：测试。

无 LLM——检查点管道是确定性的。
"""

import pathlib
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent_framework._workflows._checkpoint import (  # noqa: E402
    FileCheckpointStorage,
    InMemoryCheckpointStorage,
)
from main import (  # noqa: E402
    WORKFLOW_NAME,
    ReturnRequestExecutor,
    build_workflow,
    resume_from_checkpoint,
    run_once,
)


@pytest.fixture
def tmp_file_storage():
    path = pathlib.Path(tempfile.mkdtemp(prefix="maf-v1-ch18-"))
    try:
        yield FileCheckpointStorage(str(path)), path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.mark.asyncio
async def test_on_checkpoint_save_roundtrips_refund_amount() -> None:
    request = ReturnRequestExecutor(initial_refund=10.0)
    assert (await request.on_checkpoint_save()) == {"refund_amount": 10.0}

    request.refund_amount = 42.0
    assert (await request.on_checkpoint_save()) == {"refund_amount": 42.0}


@pytest.mark.asyncio
async def test_on_checkpoint_restore_overwrites_seeded_state() -> None:
    """恢复必须覆盖传给 __init__ 的初始退款。"""
    request = ReturnRequestExecutor(initial_refund=999.0)
    assert request.refund_amount == 999.0
    await request.on_checkpoint_restore({"refund_amount": 17.0})
    assert request.refund_amount == 17.0


@pytest.mark.asyncio
async def test_on_checkpoint_restore_defaults_when_key_missing() -> None:
    request = ReturnRequestExecutor(initial_refund=5.0)
    await request.on_checkpoint_restore({})
    assert request.refund_amount == 0.0


@pytest.mark.asyncio
async def test_running_workflow_writes_checkpoints_to_disk(tmp_file_storage) -> None:
    storage, directory = tmp_file_storage
    result = await run_once(storage, initial_refund=10.0, item_refund=5.0)
    assert result == 15.0
    files = list(directory.iterdir())
    assert files, "磁盘上应至少有一个检查点文件"


@pytest.mark.asyncio
async def test_list_checkpoints_returns_non_empty(tmp_file_storage) -> None:
    storage, _ = tmp_file_storage
    await run_once(storage, initial_refund=10.0, item_refund=5.0)
    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    assert checkpoints
    assert all(cp.workflow_name == WORKFLOW_NAME for cp in checkpoints)


@pytest.mark.asyncio
async def test_resume_restores_state_across_fresh_workflow(tmp_file_storage) -> None:
    """往返契约：以刻意错误的初始退款从第一个检查点恢复；
    on_checkpoint_restore 必须把 refund_amount 带回原值，
    使 FinalizeReturn 产出与原始运行相同的结果。"""
    storage, _ = tmp_file_storage
    expected = await run_once(storage, initial_refund=10.0, item_refund=5.0)
    assert expected == 15.0

    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    checkpoints.sort(key=lambda cp: cp.timestamp)
    first = checkpoints[0]

    replayed = await resume_from_checkpoint(storage, first.checkpoint_id, resume_initial_refund=999.0)
    assert replayed == expected


@pytest.mark.asyncio
async def test_in_memory_storage_produces_same_replay_result() -> None:
    """把 FileCheckpointStorage 换成 InMemoryCheckpointStorage：结果相同。"""
    storage = InMemoryCheckpointStorage()
    expected = await run_once(storage, initial_refund=7.0, item_refund=3.0)
    assert expected == 10.0

    checkpoints = await storage.list_checkpoints(workflow_name=WORKFLOW_NAME)
    checkpoints.sort(key=lambda cp: cp.timestamp)
    replayed = await resume_from_checkpoint(
        storage, checkpoints[0].checkpoint_id, resume_initial_refund=999.0
    )
    assert replayed == expected


@pytest.mark.asyncio
async def test_workflow_builds_with_checkpoint_storage(tmp_file_storage) -> None:
    storage, _ = tmp_file_storage
    workflow = build_workflow(storage, initial_refund=0.0)
    assert workflow is not None
    assert workflow.name == WORKFLOW_NAME
