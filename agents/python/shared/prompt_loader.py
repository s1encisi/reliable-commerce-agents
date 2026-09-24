"""加载 YAML 提示词配置。

从 agents/python/config/prompts/ 读取系统提示词，组合角色指令、
共享数据模式上下文、工具示例及事实核验规则。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "config" / "prompts"
SHARED_DIR = PROMPTS_DIR / "_shared"


@lru_cache(maxsize=32)
def _load_yaml(path: Path) -> dict:
    """加载并缓存 YAML 文件。"""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_prompt(agent_name: str, user_role: str = "customer") -> str:
    """加载并组合智能体系统提示词。

    agent_name 对应 YAML 文件名，user_role 选择角色专属段落；
    返回最终组合的提示词字符串。
    """
    config_path = PROMPTS_DIR / f"{agent_name}.yaml"
    if not config_path.exists():
        logger.warning("No YAML config found for %s, using empty prompt", agent_name)
        return ""

    config = _load_yaml(config_path)
    sp = config.get("system_prompt", {})

    parts: list[str] = []

    # 1. 基础提示词。
    base = sp.get("base", "")
    if base:
        parts.append(base.strip())

    # 2. 始终包含事实核验规则。
    grounding = _load_shared_file("grounding-rules.yaml")
    rules = grounding.get("rules", "")
    if rules:
        parts.append(rules.strip())

    # 3. 角色专属指令。
    role_instructions = sp.get("role_instructions", {})
    role_text = role_instructions.get(user_role, role_instructions.get("customer", ""))
    if role_text:
        parts.append(f"## Your Role Context\n{role_text.strip()}")

    # 4. 数据模式上下文。
    schema_data = _load_shared_file("schema-context.yaml")
    for ref in sp.get("schema_refs", []):
        section = schema_data.get(ref, "")
        if section:
            parts.append(section.strip())

    # 5. 工具调用示例。
    tool_data = _load_shared_file("tool-examples.yaml")
    for ref in sp.get("tool_example_refs", []):
        section = tool_data.get(ref, "")
        if section:
            parts.append(section.strip())

    return "\n\n".join(parts)


@lru_cache(maxsize=16)
def _load_shared_file(filename: str) -> dict:
    """从 _shared 目录加载共享 YAML 文件。"""
    path = SHARED_DIR / filename
    if not path.exists():
        logger.warning("Shared prompt file not found: %s", path)
        return {}
    return _load_yaml(path)
