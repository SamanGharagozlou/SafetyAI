"""Offline tests for the credential scan. No network, no git needed for the
rule tests. Run: python3 scripts/test_audit_secrets.py

A scanner nobody has watched fail is a green light wired to nothing, so every
rule here gets a positive case (it fires) and the shapes that surround it in
this repo get a negative case (it stays quiet).
"""
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_secrets as a  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def rules(text, path="some/file.py"):
    """Rule names raised for `text`, excluding documented exceptions."""
    return sorted(r for _, r, _, reason in a.scan_text(path, text) if not reason)


class Catches(unittest.TestCase):
    def test_private_key_block(self):
        self.assertIn("private-key-block", rules("-----BEGIN RSA PRIVATE KEY-----"))
        self.assertIn("private-key-block", rules("-----BEGIN PRIVATE KEY-----"))
        self.assertIn("private-key-block", rules("-----BEGIN OPENSSH PRIVATE KEY-----"))

    def test_provider_shapes(self):
        for text, name in (
            ("AKIAIOSFODNN7EXAMPLE", "aws-access-key"),
            ("sk-proj-" + "a" * 32, "openai-style-key"),
            ("sk_live_" + "b" * 24, "stripe-live-key"),
            ("AIza" + "C" * 35, "google-api-key"),
            ("ghp_" + "d" * 36, "github-token"),
            ("xoxb-123456789012-abcdefghijkl", "slack-token"),
            ("tskey-auth-k123CNTRL-abcdefghij", "tailscale-auth-key"),
            ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K", "jwt"),
            ("sshpass -p Tr0ub4dor ssh robot@host", "ssh-password-flag"),
        ):
            with self.subTest(name=name):
                self.assertIn(name, rules(text))

    def test_secret_named_literal_in_every_language_here(self):
        """The rule that matters most: a token with no provider shape at all."""
        for text in (
            'D1_ROBOT_TOKEN = "9f8e7d6c5b4a39281726354453627180"',          # python
            "const TOKEN = '9f8e7d6c5b4a39281726354453627180'",            # js
            'BRAIN_API_KEY=9f8e7d6c5b4a39281726354453627180',              # env file
            'export CONSOLE_KEY=9f8e7d6c5b4a39281726354453627180',         # shell
            'api_key: "9f8e7d6c5b4a39281726354453627180"',                 # yaml
            '{"client_secret": "9f8e7d6c5b4a39281726354453627180"}',       # json
            'password = "cGFzc3dvcmQtdGhhdC1pcy1yYW5kb20tZW5vdWdoIQ"',     # base64-ish
            'auth_key := "550e8400-e29b-41d4-a716-446655440000"',          # uuid secret
        ):
            with self.subTest(text=text[:40]):
                self.assertIn("secret-named literal", rules(text))

    def test_real_password_in_a_url(self):
        self.assertIn("basic-auth-url",
                      rules("rtsp://admin:Xk4nQ2zLp9@192.168.1.5:554/stream"))


class StaysQuiet(unittest.TestCase):
    def test_env_lookups_are_the_correct_pattern(self):
        for text in (
            'const TOKEN = process.env.D1_ROBOT_TOKEN',
            'self.key = os.environ.get("BRAIN_API_KEY", "")',
            'api_key = os.getenv("COSMOS_API_KEY")',
            'VAPI_PRIVATE_KEY = process.env.VAPI_PRIVATE_KEY',
            'ap.add_argument("--brain-key", default="", help="BRAIN_API_KEY")',
            'token: "${D1_ROBOT_TOKEN}"',
            'CONSOLE_KEY="${CONSOLE_KEY:-$(tr -dc A-Za-z0-9 </dev/urandom | head -c 24)}"',
        ):
            with self.subTest(text=text[:40]):
                self.assertEqual([], rules(text))

    def test_placeholders_and_prose(self):
        for text in (
            'D1_ROBOT_TOKEN=<token>',
            'password = "changeme"',
            'token = "your-token-here"',
            'D1_ROBOT_TOKEN=simulator-no-robot',
            '# The token lives on the Jetson in ~/vapi-robot/start_server.sh.',
            'console.error("D1_ROBOT_TOKEN is not set.")',
            'secret = "this is not a secret at all"',
        ):
            with self.subTest(text=text[:40]):
                self.assertEqual([], rules(text))

    def test_redaction_examples_are_not_leaks(self):
        """These shapes are the repo proving it strips credentials from logs."""
        for text in (
            "rtsp://user:pass@host:8554/test",
            "rtsp://admin:hunter2@192.168.1.5:554/stream?token=abc",
            "rtsp://user:secret@10.0.0.2:8554/x -> rtsp://***@10.0.0.2:8554/x",
            "https://user:pass@example.test/join",
        ):
            with self.subTest(text=text[:40]):
                self.assertEqual([], rules(text))

    def test_non_secret_hashes_and_ids_do_not_fire(self):
        """A hash or an id is long and random and is not a credential."""
        for text in (
            'sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"',
            'const commit = "518d912abc1234567890abcdef1234567890abcd"',
            'session_id = "550e8400-e29b-41d4-a716-446655440000"',
        ):
            with self.subTest(text=text[:40]):
                self.assertEqual([], rules(text))


class Allowlist(unittest.TestCase):
    def test_public_vapi_key_is_waived_with_a_reason(self):
        line = f'const VAPI_PUBLIC_KEY = "{a.VAPI_PUBLIC_KEY}"'
        hits = a.scan_text("tools/robot-console/server.mjs", line)
        self.assertTrue(hits, "the public key should still MATCH, not be invisible")
        self.assertTrue(all(reason for _, _, _, reason in hits))
        self.assertEqual([], rules(line, "tools/robot-console/server.mjs"))

    def test_allowlist_is_path_scoped(self):
        """A test-file waiver must not cover production code."""
        line = 'token = "9f8e7d6c5b4a39281726354453627180"'
        self.assertEqual([], rules(line, "clawmetry/test_clawmetry.py"))
        self.assertIn("secret-named literal", rules(line, "clawmetry/server.py"))

    def test_every_allow_entry_carries_a_reason(self):
        for glob, value, reason in a.ALLOW:
            with self.subTest(glob=glob):
                self.assertTrue(glob and value and len(reason) > 20,
                                "an exception without a stated reason is a hole")


class Hygiene(unittest.TestCase):
    def test_findings_never_echo_the_secret(self):
        """A scanner that prints what it found moves the secret into CI logs."""
        secret = "sk-proj-" + "Z" * 32
        out = subprocess.run(
            (sys.executable, str(HERE / "audit_secrets.py"), "scripts/_scan_fixture.tmp"),
            capture_output=True, text=True, cwd=REPO)
        fixture = REPO / "scripts/_scan_fixture.tmp"
        try:
            fixture.write_text(f'key = "{secret}"\n')
            out = subprocess.run(
                (sys.executable, str(HERE / "audit_secrets.py"), "scripts/_scan_fixture.tmp"),
                capture_output=True, text=True, cwd=REPO)
            self.assertEqual(1, out.returncode, out.stdout)
            self.assertIn("_scan_fixture.tmp:1", out.stdout)
            self.assertNotIn(secret, out.stdout + out.stderr)
            self.assertNotIn("Z" * 32, out.stdout + out.stderr)
        finally:
            fixture.unlink(missing_ok=True)

    def test_skips_vendored_and_generated_trees(self):
        for name in ("third_party/agibot_D1_Edu-Ultra/x.cpp", "frontend/node_modules/p/i.js",
                     "frontend/package-lock.json", "ros2_ws/build/x/y.py",
                     "maps/spa.pgm", "frontend/dist/assets/a.js"):
            with self.subTest(name=name):
                self.assertFalse(a.interesting(name))
        for name in ("clawmetry/server.py", "tools/robot-console/server.mjs",
                     "deploy/jetson/install.sh"):
            with self.subTest(name=name):
                self.assertTrue(a.interesting(name))

    def test_the_repository_itself_is_clean(self):
        """The check a judge would run. Keeps this suite honest about the repo."""
        out = subprocess.run((sys.executable, str(HERE / "audit_secrets.py")),
                             capture_output=True, text=True, cwd=REPO)
        self.assertEqual(0, out.returncode, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
