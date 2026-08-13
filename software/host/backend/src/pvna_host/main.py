from __future__ import annotations

import argparse
import os

import uvicorn

from .api import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable VNA local host service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-level", default="info")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("phase-one service may only bind 127.0.0.1")
    token = os.environ.get("PVNA_ACCESS_TOKEN")
    if not token:
        raise SystemExit("PVNA_ACCESS_TOKEN must be provided by Electron or the developer")
    uvicorn.run(
        create_app(
            access_token=token,
            instance_id=os.environ.get("PVNA_INSTANCE_ID"),
        ),
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=False,
    )


if __name__ == "__main__":
    main()
