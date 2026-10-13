"""Serving the built Angular app beside the API.

In development the Angular dev server serves the UI and proxies /api here,
so nothing in this module runs. In production one process serves both: the
build in web/dist is static files, and every other path is the single-page
app's index.html, which routes it in the browser.
"""
import logging
import os
import re

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIRECTORY = os.path.join(ROOT, "web", "dist", "web", "browser")

# Angular names its bundles by content hash (main-TMAFEFHT.js), so one can
# be cached forever: a new build is a new name. Everything else, index.html
# above all, keeps the API's default of no-store so a deploy shows up at once.
_HASHED = re.compile(r"-[A-Z0-9]{8}\.(?:js|css)$")
_IMMUTABLE = "public, max-age=31536000, immutable"
_ASSET_TYPES = {".js", ".css", ".map", ".woff", ".woff2", ".ico", ".png", ".jpg",
                ".svg", ".webp", ".json", ".txt", ".webmanifest"}


def mount_spa(app: FastAPI, directory: str = DEFAULT_DIRECTORY) -> bool:
    """Serve the Angular build from `directory`, if there is one.

    Call it after every API route is registered: it adds a catch-all, and
    routes match in the order they were added. Returns whether it mounted.
    """
    directory = os.path.realpath(directory)
    index = os.path.join(directory, "index.html")
    if not os.path.isfile(index):
        return False

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        # An unknown API path is a mistake in a client, and must say so
        # rather than answer 200 with a page of HTML.
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)

        target = os.path.realpath(os.path.join(directory, path))
        # realpath has resolved any "..", so a path that climbed out of the
        # build directory no longer starts with it.
        if target.startswith(directory + os.sep) and os.path.isfile(target):
            headers = {"Cache-Control": _IMMUTABLE} if _HASHED.search(target) else None
            return FileResponse(target, headers=headers)
        # A missing asset is a broken link, not a page. Judged by a list
        # of asset types rather than "has a dot": /stock/BRK.B is a page.
        if os.path.splitext(path)[1].lower() in _ASSET_TYPES:
            return JSONResponse({"detail": "Not found"}, status_code=404)
        return FileResponse(index)

    log.info("Serving the web UI from %s", directory)
    return True
