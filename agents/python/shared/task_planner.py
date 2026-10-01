"""K3 仅生成结构化计划；Flash 执行。规划器没有业务工具。"""

import json

from agent_framework import Agent

from shared.config import settings
from shared.factory import get_compatible_client
from shared.paid_transport import current_call_purpose, current_root_run
from shared.prompt_loader import _load_shared_file
from shared.task_acceptance import DEMO_TASK_GOAL, TaskRequirements
from shared.task_state import TaskPlan


def demo_plan(task: dict) -> TaskPlan:
    """固定演示，不是模型规划；执行器仍调用真实数据库工具并核验。"""
    requirements = TaskRequirements.model_validate(task.get("requirements") or {})
    minimum = max(2, requirements.min_items)
    steps = [
        {
            "id": "select",
            "service": "product-discovery",
            "instruction": "按结构化约束筛选商品",
            "acceptance": {
                "kind": "product_selection",
                "min_items": minimum,
                "required_ids": [str(value) for value in requirements.product_ids],
            },
        }
    ]
    if requirements.compare:
        steps.append(
            {
                "id": "compare",
                "service": "product-discovery",
                "instruction": "读取候选详情并列出对比属性",
                "depends_on": ["select"],
                "acceptance": {"kind": "product_comparison", "min_items": minimum, "from_steps": ["select"]},
            }
        )
    steps.append(
        {
            "id": "stock",
            "service": "inventory-fulfillment",
            "instruction": "核对全部候选的当前库存",
            "depends_on": ["select"],
            "acceptance": {"kind": "inventory_check", "min_items": minimum, "from_steps": ["select"]},
        }
    )
    return TaskPlan(goal=task["goal"], constraints=task["constraints"], source="deterministic_demo", steps=steps)


async def generate_plan(task: dict, provider: str = "moonshot") -> TaskPlan:
    if settings.LLM_PROVIDER == "replay" and task["goal"] == DEMO_TASK_GOAL:
        return demo_plan(task)
    if provider not in {"moonshot", "deepseek"}:
        raise ValueError("不支持的规划提供方")
    purpose = current_call_purpose.set("planning")
    root = current_root_run.set(str(task["id"]))
    try:
        if settings.LLM_PROVIDER == "replay":
            from shared.agent_factory import create_chat_client

            client = create_chat_client()
        else:
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
            "requirements": task.get("requirements", {}),
            "previous_plan": task.get("plan"),
            "completed_steps": task.get("results", {}),
            "schema": TaskPlan.model_json_schema(),
        }
        response = await agent.run(json.dumps(state, ensure_ascii=False, default=str), options={"max_tokens": 4096})
        text = (response.text or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        plan = TaskPlan.model_validate_json(text)
        plan.source = "model"
        if plan.goal != task["goal"] or plan.constraints != task["constraints"]:
            raise ValueError("规划改变了原始目标或约束")
        return plan
    finally:
        current_call_purpose.reset(purpose)
        current_root_run.reset(root)
