#!/usr/bin/env python3
"""Validate live OPDS feeds against our validators to diagnose issues."""
import sys
import json
import urllib.request
import base64
import xml.etree.ElementTree as ET

sys.path.insert(0, ".")
from tests.opds_validators import validate_opds1_feed, validate_opds2_feed

SERVER = "https://comicopds.genjack.net"
AUTH = base64.b64encode(b"copilot:copilot").decode()


def fetch(path: str, accept: str) -> bytes:
    headers = {"Authorization": f"Basic {AUTH}", "Accept": accept}
    req = urllib.request.Request(f"{SERVER}{path}", headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def validate_path(path: str):
    url = f"/opds?path={path}"
    print(f"\n{'='*70}")
    print(f"PATH: {path or '(root)'}")
    print(f"{'='*70}")

    # OPDS 1.2 (XML)
    print("\n--- OPDS 1.2 (Atom XML) ---")
    try:
        xml_data = fetch(url, "application/atom+xml")
        result1 = validate_opds1_feed(xml_data)
        if result1.errors:
            print("ERRORS:")
            for e in result1.errors:
                print(f"  ✗ {e}")
        else:
            print("  ✓ No errors")
        if result1.warnings:
            print("WARNINGS:")
            for w in result1.warnings:
                print(f"  ~ {w}")

        # Count entries
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_data)
        entries = root.findall("atom:entry", ns)
        print(f"  Entries: {len(entries)}")

        # Check for encoding issues in titles
        bad_titles = []
        for entry in entries:
            title_el = entry.find("atom:title", ns)
            if title_el is not None and title_el.text:
                # Check for common mojibake patterns
                if any(c in title_el.text for c in ["\x00", "\ufffd", "\u0080", "\u0094"]):
                    bad_titles.append(title_el.text)
                # Check for double-encoded UTF-8 patterns
                try:
                    title_el.text.encode("latin-1").decode("utf-8")
                    # If this succeeds, the string is double-encoded
                    bad_titles.append(f"DOUBLE-ENCODED: {title_el.text}")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass  # Normal - not double encoded
        if bad_titles:
            print("  ENCODING ISSUES:")
            for t in bad_titles:
                print(f"    ✗ {t}")

    except Exception as ex:
        print(f"  ✗ FAILED: {ex}")

    # OPDS 2.0 (JSON)
    print("\n--- OPDS 2.0 (JSON) ---")
    try:
        json_data = json.loads(fetch(url, "application/opds+json"))
        result2 = validate_opds2_feed(json_data)
        if result2.errors:
            print("ERRORS:")
            for e in result2.errors:
                print(f"  ✗ {e}")
        else:
            print("  ✓ No errors")
        if result2.warnings:
            print("WARNINGS:")
            for w in result2.warnings:
                print(f"  ~ {w}")

        nav = json_data.get("navigation", [])
        pubs = json_data.get("publications", [])
        print(f"  Navigation: {len(nav)}, Publications: {len(pubs)}")

        # Check metadata completeness
        for pub in pubs:
            meta = pub.get("metadata", {})
            links = pub.get("links", [])
            issues = []
            if not meta.get("title"):
                issues.append("missing title")
            if not meta.get("identifier"):
                issues.append("missing identifier")
            acq_links = [l for l in links if "acquisition" in l.get("rel", "")]
            if not acq_links:
                issues.append("no acquisition links")
            type_links = [l for l in links if l.get("type")]
            if len(type_links) < len(links):
                issues.append(f"{len(links) - len(type_links)} links missing type")
            if issues:
                title = meta.get("title", "??")
                print(f"  ✗ {title}: {', '.join(issues)}")

    except Exception as ex:
        print(f"  ✗ FAILED: {ex}")


if __name__ == "__main__":
    paths = sys.argv[1:] if len(sys.argv) > 1 else [
        "DC%20Comics/Absolute%20Flash%20(2025)",
        "DC%20Comics/Absolute%20Green%20Lantern%20(2025)",
    ]
    for p in paths:
        validate_path(p)
