from __future__ import annotations

import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from tools.fetch_fastenhancer import VerificationError, _load_manifest, fetch, verify


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_atomic_local_download_and_corrupt_cache(tmp_path: Path) -> None:
    served = tmp_path / "served"
    served.mkdir()
    payload = b"locked model bytes" * 1024
    (served / "asset.zip").write_bytes(payload)
    handler = lambda *args: QuietHandler(*args, directory=str(served))  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "asset_id": 1,
                    "asset_name": "asset.zip",
                    "asset_url": f"http://127.0.0.1:{server.server_port}/asset.zip",
                    "asset_size": len(payload),
                    "asset_sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        )
        manifest = _load_manifest(manifest_path)
        destination = tmp_path / "cache/asset.zip"
        fetch(manifest, destination)
        assert destination.read_bytes() == payload
        assert not tuple(destination.parent.glob(".asset.zip.*"))
        destination.write_bytes(b"corrupt")
        with pytest.raises(VerificationError):
            verify(
                destination,
                expected_size=len(payload),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
