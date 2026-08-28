from __future__ import annotations

import argparse
import asyncio
import json

from .flow import run_cognitive_cycle
from .models import LifeEvent


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser(prog="stackchan-brain")
    subparsers = parser.add_subparsers(dest="command", required=True)
    simulate = subparsers.add_parser("simulate")
    simulate.add_argument("--text", required=True)
    args = parser.parse_args()

    result = asyncio.run(run_cognitive_cycle(LifeEvent(kind="user", text=args.text)))
    serializable = _jsonable(result)
    print(json.dumps(serializable, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
