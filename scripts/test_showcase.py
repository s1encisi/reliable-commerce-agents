"""展示部署边界的静态回归，不启动 Docker、不访问模型。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.run_showcase import ROOT, compose_command, ensure_environment


class ShowcaseConfigurationTests(unittest.TestCase):
    def test_generated_credentials_are_private_and_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "showcase.env"
            ensure_environment(path)
            original = path.read_bytes()
            ensure_environment(path)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(original.splitlines()), 3)

    def test_only_frontend_publishes_loopback_port_and_agents_share_budget(self):
        config = yaml.safe_load((ROOT / "docker-compose.showcase.yml").read_text())
        services = config["services"]
        self.assertEqual(
            [name for name, item in services.items() if "ports" in item], ["web"]
        )
        self.assertEqual(services["web"]["ports"], ["127.0.0.1:3020:3000"])
        for name in [
            "orchestrator",
            "product-discovery",
            "order-management",
            "pricing-promotions",
            "review-sentiment",
            "inventory-fulfillment",
        ]:
            item = services[name]
            env = item["environment"]
            self.assertEqual(env["LLM_PROVIDER"], "replay")
            self.assertEqual(env["APP_ENV_FILE"], "/dev/null")
            self.assertEqual(env["RECORD"], "false")
            self.assertEqual(
                env["CAMPAIGN_BUDGET_PATH"], "/var/lib/reliable-commerce/budget.json"
            )
            self.assertIn("showcase-budget:/var/lib/reliable-commerce", item["volumes"])
            for key in [
                "OPENAI_API_KEY",
                "AZURE_OPENAI_KEY",
                "DEEPSEEK_API_KEY",
                "MOONSHOT_API_KEY",
                "TYPESAFE_API_KEY",
            ]:
                self.assertEqual(env[key], "")

    def test_commands_name_exact_compose_file_without_destructive_default(self):
        command = compose_command(Path("/tmp/test.env"), "stop")
        self.assertIn(str(ROOT / "docker-compose.showcase.yml"), command)
        self.assertEqual(command[-1], "stop")
        self.assertNotIn("--volumes", command)

    def test_serve_attaches_foreground_and_stops_without_deleting_data(self):
        from scripts import run_showcase

        commands = []

        def run(command, **kwargs):
            commands.append(command)
            self.assertTrue(kwargs["check"])
            if command[-3:] == ["up", "--no-build", "--no-recreate"]:
                raise KeyboardInterrupt

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(run_showcase, "ROOT", Path(directory)),
            patch.object(run_showcase.subprocess, "run", run),
            patch("sys.argv", ["run_showcase.py", "serve"]),
        ):
            run_showcase.main()
        self.assertTrue(
            any(
                command[-3:] == ["up", "--no-build", "--no-recreate"]
                for command in commands
            )
        )
        self.assertEqual(commands[-1][-1], "stop")
        self.assertFalse(
            any("down" in command or "--volumes" in command for command in commands)
        )


if __name__ == "__main__":
    unittest.main()
