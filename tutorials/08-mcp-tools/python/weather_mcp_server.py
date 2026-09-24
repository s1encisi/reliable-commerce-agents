"""
一个极小的 MCP 服务器，通过 stdio 暴露一个预置数据的天气工具。

单独运行以做冒烟检查：
    python weather_mcp_server.py  # 会一直挂着，从 stdin 读取 MCP 帧

main.py / 测试通过 agent_framework.MCPStdioTool 使用它 —— 后者把本文件作为
子进程拉起，并通过 stdio 讲 MCP 协议。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("maf-v1-ch08-weather")


@server.tool()
def get_weather(city: str) -> str:
    """Look up the current weather for a city (canned data)."""
    canned = {
        "paris": "Sunny, 18°C.",
        "london": "Overcast, 12°C.",
        "tokyo": "Rain, 15°C.",
    }
    return canned.get(city.lower(), f"No weather data for {city}.")


if __name__ == "__main__":
    server.run()
