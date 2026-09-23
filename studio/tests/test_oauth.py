"""OAuth routing, real JSONL IPC, approval and cancellation boundaries."""

import asyncio
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from studio.codex_runner import run
from studio.oauth import CodexRPC, OAuthConnections, anthropic_env, login_url
from studio.server import Problem, Studio, write_config


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "agent.toml"
        write_config(
            self.config,
            {
                "local": {
                    "model": "local",
                    "protocol": "chat_completions",
                    "base_url": "http://127.0.0.1:1/v1",
                    "auth": "none",
                }
            },
            "local",
        )
        self.studio = Studio(self.root, self.root / "state", self.config)

    def tearDown(self):
        self.studio.oauth.close()
        self.temp.cleanup()

    def rpc(self, scenario="success"):
        return CodexRPC(
            [
                sys.executable,
                "-u",
                str(Path(__file__).with_name("fake_codex.py")),
                str(self.root),
                scenario,
            ]
        )

    def test_optional_profile_and_disconnect_keep_default_and_credentials(self):
        with patch.object(
            self.studio.oauth,
            "status",
            return_value={"connected": True, "models": [{"id": "test-model"}]},
        ):
            name = self.studio.connect_model({"provider": "chatgpt", "model": "test-model"})["id"]
            self.assertEqual(self.studio.profiles()[1], "local")
            self.assertEqual(self.studio.profiles()[0][name]["backend"], "codex")
            project = next(iter(self.studio.projects))
            with self.assertRaises(Problem):
                self.studio.launch(
                    {"project": project, "task": "test", "profile": name, "mode": "agent_team"}
                )
            with self.assertRaises(Problem):
                self.studio.save_model({"id": name})
            with self.assertRaises(Problem):
                self.studio.connect_model({"provider": "chatgpt", "model": "not-listed"})
            self.studio.disconnect_model({"profile": name})
            self.assertNotIn(name, self.studio.profiles()[0])
            self.assertEqual(self.studio.profiles()[1], "local")
            self.assertIsNone(self.studio.oauth.codex)

    def test_transport_login_completion_and_public_status(self):
        self.studio.oauth.codex = self.rpc()
        status = self.studio.oauth.status("chatgpt")
        self.assertTrue(status["connected"])
        self.assertEqual(status["models"][0]["id"], "test-model")
        job = self.studio.oauth.start("chatgpt")
        self.assertEqual(job["url"].split("?")[0], "https://auth.openai.com/oauth/authorize")
        until = time.monotonic() + 3
        while self.studio.oauth.jobs["chatgpt"]["status"] == "waiting" and time.monotonic() < until:
            time.sleep(0.01)
        self.assertEqual(self.studio.oauth.status("chatgpt")["login"]["status"], "completed")
        self.studio.oauth.close()
        self.assertNotIn("account/logout", (self.root / "rpc.jsonl").read_text())

    def test_claude_isolation_and_url_validation(self):
        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "secret",
                "ANTHROPIC_AUTH_TOKEN": "secret",
                "ANTHROPIC_BASE_URL": "https://evil.invalid",
                "ANTHROPIC_PROFILE": "other",
            },
        ):
            env = anthropic_env(self.root)
            self.assertEqual(
                {k: v for k, v in env.items() if k.startswith("ANTHROPIC_")},
                {"ANTHROPIC_CONFIG_DIR": str(self.root)},
            )
        for url in [
            "http://auth.openai.com",
            "https://auth.openai.com.evil.invalid",
            "https://user:secret@auth.openai.com",
            "javascript:alert(1)",
        ]:
            with self.assertRaises(RuntimeError):
                login_url(url)
        self.assertFalse(OAuthConnections(self.root).status("claude_console")["connected"])

    @unittest.skipUnless(
        (Path(__file__).resolve().parents[2] / ".switch-agent/tools/ant").exists(),
        "Optional Anthropic CLI is not installed",
    )
    def test_real_claude_cli_callback_url_and_cancel_without_grant(self):
        self.studio.oauth.start("claude_console")
        job = self.studio.oauth.jobs["claude_console"]
        until = time.monotonic() + 5
        while not job.get("url") and job["status"] == "waiting" and time.monotonic() < until:
            time.sleep(0.02)
        self.assertEqual(job["status"], "waiting")
        uri = urlsplit(parse_qs(urlsplit(job["url"]).query)["redirect_uri"][0])
        self.assertEqual(uri.hostname, "localhost")
        self.assertGreater(uri.port, 0)
        self.assertEqual(uri.path, "/callback")
        self.studio.oauth.cancel("claude_console")
        job["process"].wait(timeout=3)
        self.assertEqual(job["status"], "cancelled")
        self.assertFalse((self.studio.data / "anthropic/credentials/switch-studio.json").exists())

    def run_case(self, scenario, allow=True):
        directory = self.root / "run"
        directory.mkdir()
        (directory / "request.json").write_text(
            json.dumps(
                {
                    "model": "test-model",
                    "cwd": str(self.root),
                    "task": "test",
                    "auto_approve": False,
                }
            )
        )
        finished = threading.Event()

        def decide():
            while not finished.wait(0.01):
                approvals = list(directory.glob("approval-*.json"))
                if approvals:
                    approval = json.loads(approvals[0].read_text())
                    path = directory / f"decision-{approval['id']}.json"
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps({"allow": allow}))
                    tmp.replace(path)
                    return
                if (
                    scenario == "cancel"
                    and (self.root / "rpc.jsonl").exists()
                    and '"turn/start"' in (self.root / "rpc.jsonl").read_text()
                ):
                    os.kill(os.getpid(), signal.SIGINT)
                    return

        helper = threading.Thread(target=decide)
        helper.start()
        try:
            self.assertEqual(run(directory, rpc_factory=lambda: self.rpc(scenario)), 0)
        finally:
            finished.set()
            helper.join(2)
        return json.loads((directory / "result.json").read_text()), [
            json.loads(s) for s in (directory / "events.jsonl").read_text().splitlines()
        ]

    def test_real_transport_approval_to_artifact_and_completion(self):
        outcome, events = self.run_case("success")
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual((self.root / "result.txt").read_text(), "OAUTH_TEST_OK")
        self.assertTrue(any(e["type"] == "changed_files" for e in events))
        calls = [json.loads(s) for s in (self.root / "rpc.jsonl").read_text().splitlines()]
        start = next(c for c in calls if c.get("method") == "thread/start")["params"]
        self.assertEqual(start["sandbox"], "workspace-write")
        self.assertEqual(start["approvalPolicy"], "untrusted")

    def test_declined_action_does_not_write(self):
        outcome, events = self.run_case("success", allow=False)
        self.assertEqual(outcome["status"], "completed")
        self.assertFalse((self.root / "result.txt").exists())
        self.assertTrue(any(e["type"] == "decision" and not e["allow"] for e in events))

    def test_provider_failure_is_not_completed(self):
        outcome, _ = self.run_case("failure")
        self.assertEqual(outcome["status"], "failed")

    def test_transport_disconnect_is_not_completed(self):
        outcome, _ = self.run_case("disconnect")
        self.assertEqual(outcome["status"], "failed")

    def test_cancel_interrupts_provider_and_closes_child(self):
        outcome, _ = self.run_case("cancel")
        self.assertEqual(outcome["status"], "cancelled")
        self.assertIn('"turn/interrupt"', (self.root / "rpc.jsonl").read_text())

    def test_claude_profile_reaches_main_reporter_and_compaction(self):
        from apodex.config import ModelConfig
        from apodex.llm import build_llm
        from apodex.switch_cli import workflow_settings
        from frontier_agent.infra.llm.aux_builder import _build_anthropic_aux_llm
        from frontier_agent.infra.protocol_client import build_protocol_client

        model = {
            "model": "claude-test",
            "protocol": "anthropic",
            "oauth_provider": "claude_console",
            "auth_profile": "switch-studio",
        }
        settings = workflow_settings({"llm": {}, "report_llm": {}}, model)
        self.assertEqual(settings["llm"]["auth_profile"], "switch-studio")
        self.assertEqual(settings["report_llm"]["auth_profile"], "switch-studio")
        with patch("anthropic.AsyncAnthropic") as sdk:
            build_protocol_client(
                {
                    "protocol": "anthropic",
                    "model": "claude-test",
                    "api_key": "placeholder",
                    "auth_profile": "switch-studio",
                },
                title="test",
            )
            self.assertEqual(sdk.call_args.kwargs["profile"], "switch-studio")
            self.assertNotIn("api_key", sdk.call_args.kwargs)
            _build_anthropic_aux_llm(
                {"model": "claude-test", "api_key": "placeholder", "auth_profile": "switch-studio"}
            )
            self.assertEqual(sdk.call_args.kwargs["profile"], "switch-studio")
            build_llm(
                ModelConfig(
                    model="claude-test", api_key="placeholder",
                    base_url="https://api.anthropic.com", protocol="anthropic",
                    client_options={"auth_profile": "switch-studio"},
                )
            )
            self.assertEqual(sdk.call_args.kwargs["profile"], "switch-studio")
            self.assertNotIn("api_key", sdk.call_args.kwargs)

    def test_real_anthropic_sdk_uses_oauth_bearer_not_ambient_key(self):
        import anthropic
        import httpx2
        from frontier_agent.core.messages import Message
        from frontier_agent.infra.protocol_client import build_protocol_client

        config_dir = self.root / "anthropic"
        (config_dir / "configs").mkdir(parents=True)
        (config_dir / "credentials").mkdir()
        (config_dir / "configs/switch-studio.json").write_text(
            json.dumps({"authentication": {"type": "user_oauth", "client_id": "test-cli"}})
        )
        credential = config_dir / "credentials/switch-studio.json"
        credential.write_text(
            json.dumps(
                {
                    "access_token": "fixture-oauth-token",
                    "refresh_token": "fixture-refresh",
                    "expires_at": int(time.time()) + 3600,
                }
            )
        )
        credential.chmod(0o600)
        seen = []

        def respond(request):
            seen.append(request)
            return httpx2.Response(
                200,
                json={
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "OAUTH_OK"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )

        sdk_class = anthropic.AsyncAnthropic

        async def exercise():
            http = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
            with patch(
                "anthropic.AsyncAnthropic",
                side_effect=lambda **kw: sdk_class(http_client=http, **kw),
            ):
                client = build_protocol_client(
                    {
                        "protocol": "anthropic",
                        "model": "claude-test",
                        "base_url": "https://api.anthropic.com",
                        "auth_profile": "switch-studio",
                        "thinking_type": "disabled",
                    },
                    title="test",
                )
            try:
                result = await client.chat([Message(role="user", content="Test")])
                self.assertEqual(result.content, "OAUTH_OK")
            finally:
                await client._client.close()

        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_CONFIG_DIR": str(config_dir),
                "ANTHROPIC_API_KEY": "ambient-key-must-not-be-used",
            },
        ):
            asyncio.run(exercise())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].headers.get("authorization"), "Bearer fixture-oauth-token")
        self.assertIsNone(seen[0].headers.get("x-api-key"))
        self.assertNotIn("thinking", json.loads(seen[0].content))
