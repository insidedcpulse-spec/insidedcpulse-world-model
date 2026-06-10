import hashlib
import hmac
import unittest
from unittest.mock import patch

from deploy_webhook import run_deploy, should_deploy, verify_signature

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


if __name__ == "__main__":
    unittest.main()
