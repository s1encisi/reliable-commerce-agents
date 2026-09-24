"""K3 仅生成结构化计划；Flash 执行。规划器没有业务工具。"""

import json

from agent_framework import Agent

from shared.factory import get_compatible_client
from shared.paid_transport import current_call_purpose, current_root_run
from shared.prompt_loader import _load_shared_file
from shared.task_state import TaskPlan


async def generate_plan(task: dict, provider: str = "moonshot") -> TaskPlan:
    if provider not in {"moonshot", "deepseek"}:
        raise ValueError("不支持的规划提供方")
    purpose = current_call_purpose.set("planning")
    root = current_root_run.set(str(task["id"]))
    try:
        client = get_compatible_client(provider)
        agent = Agent(
            client=client,
            name="task-planner",
            tools=[],
            instructions=_load_shared_file("task-planner.yaml")["instructions"],
        )
        state = {
            "goal": task["goal"],
            "constraints": task["constraints"],
            "previous_plan": task.get("plan"),
            "completed_steps": task.get("results", {}),
            "schema": TaskPlan.model_json_schema(),
        }
        response = await agent.run(json.dumps(state, ensure_ascii=False, default=str), options={"max_tokens": 4096})
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        plan = TaskPlan.model_validate_json(text)
        if plan.goal != task["goal"] or plan.constraints != task["constraints"]:
            raise ValueError("规划改变了原始目标或约束")
        return plan
    finally:
        current_call_purpose.reset(purpose)
        current_root_run.reset(root)
