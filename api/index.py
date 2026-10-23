"""Vercel entry point: the V1 HTTP server's request handler as a Python Vercel Function.

Every path is rewritten here (vercel.json), so the page, icons and /api/* are served by crawler.server.H unchanged.
Serverless mode (crawler.server.serverless, on when VERCEL=1, which Vercel sets): scans run inside the request and job
ids are self-describing, because a function keeps no background threads or shared memory between invocations.
Writable storage on Vercel is /tmp only (LOCAL_DB_PATH, OPMEM_DB_PATH default there; contents are ephemeral).
"""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("LOCAL_DB_PATH", "/tmp/crawl.db")
os.environ.setdefault("OPMEM_DB_PATH", "/tmp/memory.db")

from crawler.server import H  # noqa: E402


class handler(H):
    """Vercel looks for a top-level BaseHTTPRequestHandler subclass named handler."""
