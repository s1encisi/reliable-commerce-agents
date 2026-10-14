"""在实际模型HTTP发送边界累计采购任务尝试，不随需求版本和重启清零。"""

from shared.campaign_budget import BudgetError


async def reserve_procurement_call(purpose: str) -> None:
    from shared.db import get_pool
    from shared.procurement import state
    from shared.procurement.model_io import require_model_authorization
    from shared.procurement.models import current_procurement_binding

    binding = current_procurement_binding.get()
    if binding is None:
        return
    require_model_authorization()
    pool = get_pool()
    task = await state.get_task(pool, binding["task_id"], binding["owner_email"])
    if task is None or task["execution_mode"] != "model":
        raise BudgetError("模型调用不属于有效的采购模型任务")
    if binding.get("spec_revision", task["spec_revision"]) != task["spec_revision"]:
        raise BudgetError("需求已更新，旧模型请求不得继续")
    if task["status"] in {"paused", "completed", "failed", "budget_exhausted"}:
        raise BudgetError("任务已停止，不能继续调用模型")
    try:
        await state.reserve_model_call(pool, binding["task_id"], binding["owner_email"], purpose, max_calls=24)
    except state.BudgetExceeded as exc:
        raise BudgetError(str(exc)) from exc
