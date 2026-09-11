#!/usr/bin/env python3
"""Inspect raw protobuf structure of a car's setup and limits files."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_car_tuning_catalog import decode_file


def print_entry(entry, indent=0):
    pad = "  " * indent
    f = entry.get("field")
    w = entry.get("wire")
    if w == 5:
        print(f"{pad}field {f} (float): {entry['value']:.6f}")
    elif w == 0:
        print(f"{pad}field {f} (varint): {entry['value']}")
    elif w == 2:
        size = entry.get("size", "?")
        if "children" in entry:
            print(f"{pad}field {f} (message, {size} bytes):")
            for child in entry["children"]:
                print_entry(child, indent + 1)
        else:
            raw = entry.get("raw_hex", "?")
            print(f"{pad}field {f} (bytes[{size}]): {raw[:80]}")
    else:
        print(f"{pad}field {f} (wire={w})")


def main():
    if len(sys.argv) < 2:
        print("Usage: inspect_car_setup.py <car_dir>")
        sys.exit(1)

    car_dir = sys.argv[1]
    setup_dir = os.path.join(car_dir, "data", "setup")
    if not os.path.isdir(setup_dir):
        print(f"No setup dir at {setup_dir}")
        sys.exit(1)

    for name in sorted(os.listdir(setup_dir)):
        path = os.path.join(setup_dir, name)
        if not os.path.isfile(path):
            continue
        print(f"\n=== {name} ===")
        try:
            decoded = decode_file(path)
            for entry in decoded:
                print_entry(entry)
        except Exception as e:
            print(f"  PARSE ERROR: {e}")


if __name__ == "__main__":
    main()
