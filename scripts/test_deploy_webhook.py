import hashlib
import hmac
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from deploy_webhook import (
    DeployWebhookHandler,
    run_deploy,
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
    @patch("deploy_webhook.subprocess.run")
    def test_runs_all_steps_on_success(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        run_deploy()
        self.assertEqual(mock_run.call_count, 5)
        first_cmd = mock_run.call_args_list[0].args[0]
        self.assertEqual(first_cmd, ["git", "fetch", "origin", "main"])
        last_cmd = mock_run.call_args_list[-1].args[0]
        self.assertEqual(last_cmd, ["docker", "image", "prune", "-f"])

    @patch("deploy_webhook.subprocess.run")
    def test_stops_on_first_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "boom"
        run_deploy()
        self.assertEqual(mock_run.call_count, 1)


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
