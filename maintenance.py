#!/usr/bin/env python3
"""Maintenance commands for an existing Blackboard backup."""

import argparse
import json

from manifest import Manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["audit-manifest"])
    args = parser.parse_args()

    if args.command == "audit-manifest":
        result = Manifest().audit()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
