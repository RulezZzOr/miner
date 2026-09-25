"""Company structure and project installation, without starting any agents."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path

from studio.server import COMPANY_PATH, COMPANY_SOURCE, Handler, Problem, Studio


def authenticated_open(studio, value):
    req = value if isinstance(value, urllib.request.Request) else urllib.request.Request(value)
    req.add_header('Authorization', 'Bearer '+(studio.data/'access-key').read_text().strip())
    return urllib.request.urlopen(req)


class CompanyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.studio = Studio(self.project, self.root / "state")
        self.pid = next(iter(self.studio.projects))

    def test_structure_has_thirty_roles_three_layers_and_independent_reviewers(self):
        t = json.loads(COMPANY_SOURCE.read_text())
        roles = {r["id"]: r for r in t["roles"]}
        self.assertEqual(len(roles), len(t["roles"]))
        self.assertEqual(len(roles), 30)
        self.assertEqual(t["role_count"], 30)
        self.assertEqual(Counter(r["department"] for r in roles.values()),
                         {"executive": 2, "growth": 5, "delivery": 18, "platform": 5})
        for d in t["departments"]:
            self.assertEqual(d["count"], sum(r["department"] == d["id"] for r in roles.values()))
            self.assertTrue(roles[d["lead"]]["player_coach"])
        for key, role in roles.items():
            with self.subTest(role=key):
                self.assertIn(role["reviewed_by"], set(roles) | {"human_owner"})
                self.assertNotEqual(key, role["reviewed_by"])
                self.assertTrue(role["instructions"])
                self.assertTrue(role["deliverables"])
                visited = {key}
                manager = role["reports_to"]
                while manager:
                    self.assertIn(manager, roles)
                    self.assertNotIn(manager, visited)
                    visited.add(manager)
                    manager = roles[manager]["reports_to"]
                self.assertLessEqual(len(visited), t["max_reporting_layers"])
        for squad in t["squads"]:
            members = [r for r in roles.values() if r.get("squad") == squad["id"]]
            self.assertEqual(len(members), 5)
            self.assertTrue(all(r["reports_to"] == "cto" for r in members))
            self.assertIn(squad["coordinator"], roles)
        self.assertEqual(len(t["squads"]), 3)

    def test_install_is_project_scoped_preserves_edits_and_does_not_launch(self):
        before = self.studio.company_template(self.pid)
        self.assertFalse(before["installed"])
        self.assertFalse((self.project / "company").exists())
        result = self.studio.install_company_template({"project": self.pid, "path": "evil.txt", "content": "ignored"})
        target = self.project / COMPANY_PATH
        self.assertEqual(target.read_bytes(), COMPANY_SOURCE.read_bytes())
        self.assertEqual(result["path"], COMPANY_PATH)
        self.assertTrue(self.studio.company_template(self.pid)["installed"])
        self.assertFalse((self.project / "evil.txt").exists())
        target.write_text('{"my_edit": true}')
        with self.assertRaises(Problem) as caught:
            self.studio.install_company_template({"project": self.pid, "revision": result["revision"]})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(target.read_text(), '{"my_edit": true}')
        self.assertEqual(self.studio.runs, {})
        self.assertEqual(self.studio.processes, {})

    def test_unknown_project_and_symlink_cannot_redirect_install(self):
        with self.assertRaises(Problem):
            self.studio.install_company_template({"project": "missing"})
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / "company").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(Problem) as caught:
            self.studio.install_company_template({"project": self.pid})
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(list(outside.iterdir()), [])

    def test_http_preview_and_install_require_existing_mutation_token(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.studio = self.studio
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with authenticated_open(self.studio, base + "/api/templates/company?project=" + self.pid) as response:
                self.assertEqual(json.load(response)["template"]["role_count"], 30)
            request = urllib.request.Request(base + "/api/templates/company/install",
                data=json.dumps({"project": self.pid}).encode(), headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as caught:
                authenticated_open(self.studio, request)
            self.assertEqual(caught.exception.code, 403)
            self.assertFalse((self.project / COMPANY_PATH).exists())
            request.add_header("X-Studio-Token", self.studio.token)
            with authenticated_open(self.studio, request) as response:
                self.assertEqual(json.load(response)["path"], COMPANY_PATH)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
