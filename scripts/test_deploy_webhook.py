import hashlib
import hmac
import unittest

from deploy_webhook import should_deploy, verify_signature

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


if __name__ == "__main__":
    unittest.main()
