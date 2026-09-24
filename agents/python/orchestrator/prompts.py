"""编排器（客服）智能体的系统提示词 —— 从 YAML 配置加载。"""

from shared.prompt_loader import load_prompt


def get_system_prompt(user_role: str = "customer") -> str:
    return load_prompt("orchestrator", user_role)


# 向后兼容
SYSTEM_PROMPT = get_system_prompt()
