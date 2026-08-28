from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    schemas = sorted((root / "contracts").rglob("*.schema.json"))
    if not schemas:
        raise SystemExit("no schemas found")
    ids: set[str] = set()
    for path in schemas:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise SystemExit(f"{path}: unsupported draft")
        schema_id = payload.get("$id")
        if not schema_id or schema_id in ids:
            raise SystemExit(f"{path}: missing or duplicate $id")
        ids.add(schema_id)
        if payload.get("type") != "object":
            raise SystemExit(f"{path}: root must be object")
        print(f"ok {path.relative_to(root)}")


if __name__ == "__main__":
    main()
