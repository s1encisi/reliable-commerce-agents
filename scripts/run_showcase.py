"""独立容器作品展示控制器；仅生成本地随机密钥，不继承真实模型配置。"""

import argparse
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ensure_environment(path: Path) -> None:
    if not path.exists():
        values = {
            name: secrets.token_hex(32)
            for name in (
                "SHOWCASE_DB_PASSWORD",
                "SHOWCASE_JWT_SECRET",
                "SHOWCASE_AGENT_SECRET",
            )
        }
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(
                "\n".join(f"{name}={value}" for name, value in values.items()) + "\n"
            )
    path.chmod(0o600)


def compose_command(path: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(path),
        "-f",
        str(ROOT / "docker-compose.showcase.yml"),
        *arguments,
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        nargs="?",
        choices=["up", "serve", "build", "status", "stop"],
        default="up",
    )
    args = parser.parse_args()
    environment_file = ROOT / ".env.showcase.local"
    ensure_environment(environment_file)
    # Compose 变量只读取本脚本生成的文件；宿主同名值不能覆盖本地密钥。
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "SHOWCASE_DB_PASSWORD",
            "SHOWCASE_JWT_SECRET",
            "SHOWCASE_AGENT_SECRET",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
        }
    }
    subprocess.run(
        compose_command(environment_file, "config", "--quiet"), env=env, check=True
    )
    if args.action in {"up", "serve", "build"}:
        build_arguments = ["build"]
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            if env.get(key):
                build_arguments.extend(["--build-arg", key])
        subprocess.run(
            compose_command(environment_file, *build_arguments), env=env, check=True
        )
    if args.action in {"up", "serve"}:
        subprocess.run(
            compose_command(
                environment_file, "up", "-d", "--wait", "--wait-timeout", "180"
            ),
            env=env,
            check=True,
        )
        print(
            "容器展示已通过健康检查：http://localhost:3020（仅本机；固定离线示例不调用模型）"
        )
        if args.action == "serve":
            print(
                "前台展示会话已保持。WSL中请保持此终端；Ctrl-C停止容器但保留数据卷。",
                flush=True,
            )
            try:
                subprocess.run(
                    compose_command(
                        environment_file, "up", "--no-build", "--no-recreate"
                    ),
                    env=env,
                    check=True,
                )
            except KeyboardInterrupt:
                # compose前台模式处理SIGINT并停止服务；再显式stop保证子进程退出后状态一致。
                subprocess.run(
                    compose_command(environment_file, "stop"), env=env, check=True
                )
                print("前台展示已停止；数据卷保留。")
    elif args.action == "status":
        subprocess.run(compose_command(environment_file, "ps"), env=env, check=True)
    elif args.action == "stop":
        subprocess.run(compose_command(environment_file, "stop"), env=env, check=True)
        print("容器展示已停止；数据库和预算卷保留。")


if __name__ == "__main__":
    main()
