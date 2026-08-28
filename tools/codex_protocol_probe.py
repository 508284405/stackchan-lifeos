"""Record the locally installed Codex app-server protocol surface.

This intentionally generates schemas instead of starting a live agent turn. It is
safe to run before credentials are configured and makes protocol drift visible.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path


REQUIRED_METHODS = {"initialize", "thread/start", "turn/start", "turn/interrupt"}


def main() -> None:
    codex = shutil.which("codex")
    if codex is None:
        raise SystemExit("codex executable not found")

    with tempfile.TemporaryDirectory(prefix="stackchan-codex-schema-") as directory:
        subprocess.run(
            [codex, "app-server", "generate-json-schema", "--out", directory],
            check=True,
        )
        bundle = Path(directory) / "ClientRequest.json"
        data = json.loads(bundle.read_text(encoding="utf-8"))
        text = json.dumps(data, ensure_ascii=False)
        missing = sorted(method for method in REQUIRED_METHODS if method not in text)
        if missing:
            raise SystemExit(f"incompatible Codex app-server; missing methods: {missing}")

    version = subprocess.run(
        [codex, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    print(f"compatible protocol surface: {version}")
    for method in sorted(REQUIRED_METHODS):
        print(f"  {method}")


if __name__ == "__main__":
    main()

