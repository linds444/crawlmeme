"""crawler/env.py .env parsing (inline comments, quotes, existing variables win)."""
import os, tempfile, unittest
from unittest import mock

import fakes  # noqa: F401  (repository root on sys.path)
from crawler.env import load_dotenv


class TestDotenv(unittest.TestCase):

    def load(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write(text)
        self.addCleanup(os.unlink, f.name)
        with mock.patch.dict(os.environ, {}, clear=True):
            load_dotenv(f.name)
            return dict(os.environ)

    def test_inline_comments(self):
        env = self.load("A=true   # Flap on Robinhood\nB=   # only a comment\nC=x#y\nD='v # kept'\nE=1\t# tab\n")
        self.assertEqual(env["A"], "true")              # would have been "true   # Flap…" (= off) before
        self.assertEqual(env["B"], "")
        self.assertEqual(env["C"], "x#y")                # no whitespace before #: part of the value (URL fragments)
        self.assertEqual(env["D"], "v # kept")           # quoted: verbatim
        self.assertEqual(env["E"], "1")

    def test_existing_env_wins(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("K=file\n")
        self.addCleanup(os.unlink, f.name)
        with mock.patch.dict(os.environ, {"K": "process"}, clear=True):
            load_dotenv(f.name)
            self.assertEqual(os.environ["K"], "process")

    def test_example_file_parses_to_defaults(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = self.load(open(os.path.join(root, ".env.example"), encoding="utf-8").read())
        self.assertEqual(env["FLAP_ENABLED"], "false")
        self.assertEqual(env["TG_ALLOWED_USERS"], "")
        self.assertEqual(env["TG_WATCH_LIMIT"], "20")
        self.assertEqual(env["LOCAL_DB_PATH"], "")
        self.assertTrue(all("#" not in v for v in env.values()))


if __name__ == "__main__":
    unittest.main()
