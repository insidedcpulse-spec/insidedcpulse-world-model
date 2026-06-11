import hashlib
import hmac
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import deploy_webhook
from deploy_webhook import (
    DeployWebhookHandler,
    run_deploy,
    run_smoke_checks,
    should_deploy,
    verify_signature,
)

SECRET = b"test-secret"


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


class TestVerifySignature(unittest.TestCase):
    def test_valid_signature(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertTrue(verify_signature(SECRET, body, sign(body)))

    def test_invalid_signature(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertFalse(verify_signature(SECRET, body, "sha256=deadbeef"))

    def test_missing_header(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertFalse(verify_signature(SECRET, body, None))

    def test_wrong_prefix(self):
        body = b'{"ref": "refs/heads/main"}'
        self.assertFalse(verify_signature(SECRET, body, "sha1=abcd"))


class TestShouldDeploy(unittest.TestCase):
    def test_push_to_main(self):
        self.assertTrue(should_deploy("push", {"ref": "refs/heads/main"}))

    def test_push_to_other_branch(self):
        self.assertFalse(should_deploy("push", {"ref": "refs/heads/feature-x"}))

    def test_non_push_event(self):
        self.assertFalse(should_deploy("ping", {"ref": "refs/heads/main"}))


class TestRunDeploy(unittest.TestCase):
    @patch("deploy_webhook.run_smoke_checks")
    @patch("deploy_webhook.time.sleep")
    @patch("deploy_webhook.subprocess.run")
    def test_runs_all_steps_on_success(self, mock_run, mock_sleep, mock_smoke):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        mock_smoke.return_value = [{"name": "healthz", "ok": True, "detail": "ok"}]
        run_deploy()
        self.assertEqual(mock_run.call_count, 5)
        first_cmd = mock_run.call_args_list[0].args[0]
        self.assertEqual(first_cmd, ["git", "fetch", "origin", "main"])
        last_cmd = mock_run.call_args_list[-1].args[0]
        self.assertEqual(last_cmd, ["docker", "image", "prune", "-f"])
        mock_smoke.assert_called_once()
        self.assertEqual(deploy_webhook.LAST_SMOKE_RESULTS["results"], mock_smoke.return_value)

    @patch("deploy_webhook.run_smoke_checks")
    @patch("deploy_webhook.subprocess.run")
    def test_stops_on_first_failure_skips_smoke(self, mock_run, mock_smoke):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "boom"
        run_deploy()
        self.assertEqual(mock_run.call_count, 1)
        mock_smoke.assert_not_called()


class TestSmokeGet(unittest.TestCase):
    @patch("deploy_webhook.urllib.request.build_opener")
    def test_pass(self, mock_build_opener):
        resp = MagicMock(status=200)
        mock_build_opener.return_value.open.return_value = resp
        ok, detail = deploy_webhook._smoke_get("/healthz", 200)
        self.assertTrue(ok)
        self.assertEqual(detail, "ok")

    @patch("deploy_webhook.urllib.request.build_opener")
    def test_wrong_status(self, mock_build_opener):
        resp = MagicMock(status=500)
        mock_build_opener.return_value.open.return_value = resp
        ok, detail = deploy_webhook._smoke_get("/healthz", 200)
        self.assertFalse(ok)
        self.assertIn("500", detail)

    @patch("deploy_webhook.urllib.request.build_opener")
    def test_check_callback(self, mock_build_opener):
        resp = MagicMock(status=302)
        resp.headers.get.return_value = "https://insidedcpulse.com/grafana/login"
        mock_build_opener.return_value.open.return_value = resp
        ok, _ = deploy_webhook._smoke_get(
            "/grafana/", 302, lambda r: r.headers.get("Location", "").endswith("/grafana/login")
        )
        self.assertTrue(ok)

    @patch("deploy_webhook.urllib.request.build_opener")
    def test_check_callback_fails_on_redirect_loop(self, mock_build_opener):
        resp = MagicMock(status=301)
        resp.headers.get.return_value = "https://insidedcpulse.com/grafana/"
        mock_build_opener.return_value.open.return_value = resp
        ok, detail = deploy_webhook._smoke_get(
            "/grafana/", 302, lambda r: r.headers.get("Location", "").endswith("/grafana/login")
        )
        self.assertFalse(ok)

    @patch("deploy_webhook.urllib.request.build_opener")
    def test_connection_error(self, mock_build_opener):
        mock_build_opener.return_value.open.side_effect = urllib.error.URLError("boom")
        ok, detail = deploy_webhook._smoke_get("/healthz", 200)
        self.assertFalse(ok)
        self.assertIn("boom", detail)


class TestSmokePost(unittest.TestCase):
    @patch("deploy_webhook.urllib.request.urlopen")
    def test_pass(self, mock_urlopen):
        resp = MagicMock(status=200)
        resp.read.return_value = b'{"result": {"tools": []}}'
        mock_urlopen.return_value = resp
        ok, detail = deploy_webhook._smoke_post(
            "/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, 200, '"tools"'
        )
        self.assertTrue(ok)

    @patch("deploy_webhook.urllib.request.urlopen")
    def test_body_mismatch(self, mock_urlopen):
        resp = MagicMock(status=200)
        resp.read.return_value = b"Internal Server Error"
        mock_urlopen.return_value = resp
        ok, detail = deploy_webhook._smoke_post(
            "/mcp/", {"jsonrpc": "2.0", "id": 1, "method": "bogus"}, 200, "-32601"
        )
        self.assertFalse(ok)


class TestRunSmokeChecks(unittest.TestCase):
    @patch("deploy_webhook._smoke_post", return_value=(True, "ok"))
    @patch("deploy_webhook._smoke_get", return_value=(True, "ok"))
    def test_all_checks_run(self, mock_get, mock_post):
        results = run_smoke_checks()
        names = {r["name"] for r in results}
        self.assertEqual(
            names,
            {"healthz", "status_page", "grafana_no_redirect_loop", "mcp_tools_list", "mcp_unknown_method_handled"},
        )
        self.assertTrue(all(r["ok"] for r in results))


class TestDeployWebhookHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        DeployWebhookHandler.secret = SECRET
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DeployWebhookHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()

    def _post(self, path, body, headers):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_healthz(self):
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read(), b"ok")

    def test_smoke_endpoint_returns_last_results(self):
        deploy_webhook.LAST_SMOKE_RESULTS = {"timestamp": 123, "results": [{"name": "healthz", "ok": True}]}
        resp = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/smoke")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.headers.get("Content-Type"), "application/json")
        self.assertEqual(json.loads(resp.read()), deploy_webhook.LAST_SMOKE_RESULTS)

    def test_invalid_signature_rejected(self):
        body = b'{"ref": "refs/heads/main"}'
        status, _ = self._post(
            "/hooks/deploy",
            body,
            {
                "X-Hub-Signature-256": "sha256=deadbeef",
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 401)

    @patch("deploy_webhook.run_deploy")
    def test_valid_push_to_main_triggers_deploy(self, mock_deploy):
        body = b'{"ref": "refs/heads/main"}'
        status, resp_body = self._post(
            "/hooks/deploy",
            body,
            {
                "X-Hub-Signature-256": sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(resp_body, b"ok")
        for _ in range(50):
            if mock_deploy.called:
                break
            threading.Event().wait(0.01)
        mock_deploy.assert_called_once()

    @patch("deploy_webhook.run_deploy")
    def test_valid_push_to_other_branch_no_deploy(self, mock_deploy):
        body = b'{"ref": "refs/heads/feature-x"}'
        status, _ = self._post(
            "/hooks/deploy",
            body,
            {
                "X-Hub-Signature-256": sign(body),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 200)
        threading.Event().wait(0.05)
        mock_deploy.assert_not_called()

    def test_unknown_path(self):
        status, _ = self._post(
            "/other",
            b"{}",
            {
                "X-Hub-Signature-256": sign(b"{}"),
                "X-GitHub-Event": "push",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
