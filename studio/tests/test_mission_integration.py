"""Initial delivery and follow-up through eight real Frontier processes and a local LLM fixture.

This validates transport and file/report handoff, not intelligence of a real model.
"""
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio.server import Studio, write_config


class ProjectModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = data["messages"]
        prompt = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
        report_path = re.search("JSON file (company/projects/[a-f0-9]+/reports/[a-f0-9]+\\.json)", prompt).group(1)
        planning = "You are the planner." in prompt
        building = "PHASE OF THIS RUN: build." in prompt
        self.server.frames.append({"planning": planning, "building": building,
            "system": "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system"),
            "tools": [t.get("function", {}).get("name") for t in data.get("tools", [])]})
        final = "PHASE OF THIS RUN: final." in prompt
        # Count only tool results from this fresh worker context.
        count = sum(m.get("role") == "tool" for m in messages)
        if planning:
            report = {"status": "plan", "questions": [], "tasks": [{"id": "build", "title": "Build",
                "instructions": "Write product.txt with OK", "depends_on": [], "criteria": ["File OK"]}]}
        else:
            report = {"status": "done" if building else "pass", "summary": "Fixture verified",
                "artifacts": ["product.txt"], "checks": [{"criterion": "Product ready" if final else "File OK",
                    "passed": True, "evidence": "Deterministic fixture read: OK"}]}
        operations = []
        if planning:
            operations.append(("bash", {"command": "printf BAD > /workspace/should-not-exist.txt"}))
        if building:
            content = "OK-v2" if '"work_kind": "feature"' in prompt else "OK"
            # A real model may use the exact physical root supplied in its
            # instructions. Both that root and /workspace must work.
            physical = re.search("The project root is (.+?)\\. File", prompt).group(1)
            file_path = physical + "/product.txt" if content == "OK" else "/workspace/product.txt"
            operations.append(("create_file", {"path": file_path, "content": content, "overwrite": content != "OK"}))
        if not planning and not building:
            operations.append(("read_file", {"path": "/workspace/product.txt"}))
        operations.append(("save_mission_report", report))
        if count < len(operations):
            name, args = operations[count]
            delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"call_{count}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}]}
            finish = "tool_calls"
        else:
            delta = {"role": "assistant", "content": "Files and report saved."}
            finish = "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for d, f in [(delta, None), ({}, finish)]:
            payload = {"id": "test", "object": "chat.completion.chunk", "created": int(time.time()), "model": "test",
                "choices": [{"index": 0, "delta": d, "finish_reason": f}]}
            self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


class MissionIntegration(unittest.TestCase):
    def test_real_workers_create_review_and_deliver_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            project = root / "project"
            project.mkdir()
            model = ThreadingHTTPServer(("127.0.0.1", 0), ProjectModel)
            model.frames = []
            thread = threading.Thread(target=model.serve_forever, daemon=True)
            thread.start()
            config = root / "agent.toml"
            write_config(config, {"test": {"model": "test", "protocol": "chat_completions", "chat_dialect": "openai",
                "base_url": f"http://127.0.0.1:{model.server_port}/v1", "auth": "none",
                "context_window": 32768, "max_output_tokens": 4096}}, "test")
            studio = Studio(project, root / "state", config)
            try:
                controller = studio.missions
                m = controller.create({"project": next(iter(studio.projects)), "title": "Integration",
                    "goal": "Create product.txt", "criteria": ["Product ready"], "auto_approve": True, "max_turns": 8,
                    "verification_checks": [{"argv": [sys.executable, "-c", "from pathlib import Path; assert Path('product.txt').read_text() in ('OK', 'OK-v2')"]}]})
                controller.action({"id": m["id"], "action": "start"})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    controller.tick()
                    m = controller.get(m["id"])
                    if m["status"] == "awaiting_plan":
                        # Re-open the persisted controller before authorizing delivery.
                        from studio.missions import Missions
                        controller = Missions(studio)
                        controller.action({"id": m["id"], "action": "approve_plan"})
                    if m["status"] in {"ready", "blocked"} or m["failures"]:
                        break
                    time.sleep(0.1)
                logs = "\n".join(p.read_text()[-5000:] for p in (root / "state/runs").glob("*/console.log"))
                self.assertEqual(m["status"], "ready", str(m) + "\n" + logs)
                work = Path(m["workspace"])
                self.assertNotEqual(work, project)
                self.assertFalse((project / "product.txt").exists())
                self.assertEqual((work / "product.txt").read_text(), "OK")
                self.assertFalse((work / "should-not-exist.txt").exists())
                self.assertEqual(len(m["attempts"]), 4)
                sessions = [r["session_id"] for r in studio.runs.values()]
                self.assertEqual(len(set(sessions)), 4)
                self.assertTrue(all((work / a["report"]).is_file() for a in m["attempts"]))
                controller.action({"id": m["id"], "action": "accept"})
                self.assertEqual((project / "product.txt").read_text(), "OK")
                self.assertEqual(controller.get(m["id"])["status"], "accepted")
                from studio.products import Products
                studio.missions = controller
                studio.products = Products(studio)
                product = studio.products.create({"project": m["project"], "source_mission": m["id"], "kind": "automation"})
                first_release = product["releases"][0]
                studio.products.action({"id": product["id"], "action": "add", "kind": "feature",
                    "title": "Second version", "goal": "Update product.txt to OK-v2", "criteria": ["Product ready"]})
                studio.products.action({"id": product["id"], "action": "settings", "autopilot": True, "cycle_limit": 2})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    studio.products.tick()
                    controller.tick()
                    product = studio.products.get(product["id"])
                    followup = controller.get(product["cycles"][-1]["mission"])
                    if followup["status"] in {"ready", "blocked"} or followup["failures"]:
                        break
                    time.sleep(0.1)
                self.assertEqual(followup["status"], "ready", str(followup))
                self.assertEqual((project / "product.txt").read_text(), "OK")
                self.assertEqual((Path(followup["workspace"]) / "product.txt").read_text(), "OK-v2")
                self.assertEqual(len(product["releases"]), 1)  # owner has not accepted yet
                controller.action({"id": followup["id"], "action": "accept"})
                self.assertEqual((project / "product.txt").read_text(), "OK-v2")
                studio.products.tick()
                product = studio.products.get(product["id"])
                self.assertEqual(len(product["releases"]), 2)
                self.assertEqual(product["releases"][0], first_release)
                self.assertNotEqual(product["releases"][1]["artifacts"], first_release["artifacts"])
                self.assertEqual(len({r["session_id"] for r in studio.runs.values()}), 8)
                self.assertTrue(all("Controller-assigned workflow phase" in f["system"] for f in model.frames))
                self.assertTrue(all("bash" not in f["tools"] and "add_task" not in f["tools"]
                                    for f in model.frames if not f["building"]))
                for run in studio.runs.values():
                    events = [json.loads(line) for line in (studio.run_dir(run["id"]) / "events.jsonl").read_text().splitlines()]
                    # Native workflows must checkpoint during work, not only at finalization.
                    self.assertGreaterEqual(sum(e["type"] == "usage" for e in events), 2)
                    self.assertIn("usage", run)
            finally:
                studio.missions.close()
                for key in list(studio.processes):
                    studio.stop(key)
                for process in list(studio.processes.values()):
                    studio.kill_later(process)
                studio.oauth.close()
                model.shutdown()
                model.server_close()
                thread.join(2)
