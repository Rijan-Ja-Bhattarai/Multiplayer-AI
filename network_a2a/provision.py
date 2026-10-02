"""Generate private, per-agent credentials without printing secrets."""
import argparse
import json
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("agents", nargs="+")
    parser.add_argument("--group", default="team")
    parser.add_argument("--output", default="credentials.json")
    args = parser.parse_args()
    if len(set(args.agents)) != len(args.agents):
        parser.error("Agent IDs must be unique")
    credentials = {agent: {"token": secrets.token_urlsafe(32), "group": args.group} for agent in args.agents}
    from .server import Relay
    Relay(credentials)
    with Path(args.output).open("x", encoding="utf-8") as file:
        json.dump(credentials, file, indent=2)
    print(f"Created {args.output}. Share only each device's own token through a private channel.")


if __name__ == "__main__":
    main()
