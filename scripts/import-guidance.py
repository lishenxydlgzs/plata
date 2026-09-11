#!/usr/bin/env python3
"""Import private Markdown into a running Plata server without copying it into Git."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--server", default="http://127.0.0.1:8200")
    parser.add_argument("--key", required=True, help="Stable document key; reuse to create a new revision")
    parser.add_argument("--title", required=True)
    parser.add_argument("--instructions", help="How Plata should apply this document")
    parser.add_argument("--inactive", action="store_true", help="Save a disabled revision")
    args = parser.parse_args()
    try:
        payload = {"document_key": args.key, "title": args.title,
                   "content": args.file.read_text(encoding="utf-8"), "active": not args.inactive}
        if args.instructions:
            payload["application_instructions"] = args.instructions
        request = Request(args.server.rstrip("/") + "/api/guidance-documents",
                          data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=15) as response:
            document = json.load(response)
    except (OSError, HTTPError, URLError, ValueError) as error:
        # Avoid printing server validation bodies, which can contain private content.
        parser.exit(1, f"Import failed ({type(error).__name__}). Check the file, server, and request fields.\n")
    print(f"Saved guidance revision {document['version']} (active={document['active']}).")


if __name__ == "__main__":
    main()
