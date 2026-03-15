#!/usr/bin/env python3
"""Check encoding of em dash separators across all entries."""
import sys
import re

data = sys.stdin.buffer.read()

# Find all title lines
for m in re.finditer(rb"<title>[^<]*#(\d+)\s+(.{1,6})", data):
    num = m.group(1).decode()
    after = m.group(2)
    em_dash_ok = after[:3] == b"\xe2\x80\x94"
    if em_dash_ok:
        status = "OK (U+2014 em dash)"
    else:
        status = f"CORRUPTED: {repr(after[:6])}"
    print(f"  #{num:>2s}: {status}")
