#!/usr/bin/env python3
"""Deep analysis of OPDS feed entries to find client-visible issues."""
import sys
import json
import urllib.request
import base64
import xml.etree.ElementTree as ET

SERVER = "https://comicopds.genjack.net"
AUTH = base64.b64encode(b"copilot:copilot").decode()


def fetch(path: str, accept: str) -> bytes:
    headers = {"Authorization": f"Basic {AUTH}", "Accept": accept}
    req = urllib.request.Request(f"{SERVER}{path}", headers=headers)
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def analyze_entries(path: str):
    url = f"/opds?path={path}"
    print(f"\n{'=' * 80}")
    print(f"ANALYZING: {path}")
    print(f"{'=' * 80}")

    # --- OPDS 2.0 (JSON) ---
    print("\n--- OPDS 2.0 Entry Analysis ---")
    data = json.loads(fetch(url, "application/opds+json"))
    pubs = data.get("publications", [])
    nav = data.get("navigation", [])

    print(f"Navigation: {len(nav)}, Publications: {len(pubs)}")

    for i, p in enumerate(pubs):
        m = p.get("metadata", {})
        links = p.get("links", [])
        imgs = p.get("images", [])
        acq = [l for l in links if "acquisition" in l.get("rel", "")]
        pse = [l for l in links if "opds-pse" in l.get("rel", "")]
        enc = [l for l in links if l.get("rel", "") == "enclosure"]
        title = m.get("title", "?")

        issues = []
        if not acq:
            issues.append("NO-ACQUISITION")
        if not imgs:
            issues.append("NO-IMAGES")
        if not pse:
            issues.append("NO-PSE")
        if not enc:
            issues.append("NO-ENCLOSURE")
        if not m.get("modified"):
            issues.append("NO-MODIFIED")
        for l in links:
            if not l.get("type"):
                rel = l.get("rel", "?")
                issues.append("LINK-NO-TYPE:" + rel)

        status = " ".join(issues) if issues else "OK"
        marker = "✗" if issues else "✓"
        print(f"  {marker} [{i + 1:2d}] {title[:55]:55s} {status}")

    # --- OPDS 1.2 (XML) ---
    print("\n--- OPDS 1.2 Entry Analysis ---")
    xml_data = fetch(url, "application/atom+xml")
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "opds": "http://opds-spec.org/2010/catalog",
        "pse": "http://vaemendis.net/opds-pse/ns",
    }
    root = ET.fromstring(xml_data)
    entries = root.findall("atom:entry", ns)
    print(f"Entries: {len(entries)}")

    for i, entry in enumerate(entries):
        title_el = entry.find("atom:title", ns)
        title = title_el.text if title_el is not None else "?"
        links = entry.findall("atom:link", ns)

        issues = []

        # Check required elements
        id_el = entry.find("atom:id", ns)
        updated_el = entry.find("atom:updated", ns)
        if id_el is None or not id_el.text:
            issues.append("NO-ID")
        if updated_el is None or not updated_el.text:
            issues.append("NO-UPDATED")

        # Check links
        rels = [l.get("rel", "") for l in links]
        has_acq = any("acquisition" in r for r in rels)
        has_subsection = any(r == "subsection" for r in rels)
        has_image = any("opds-spec.org/image" in r for r in rels)
        has_thumb = any("image/thumbnail" in r for r in rels)
        has_pse = any("opds-pse" in r for r in rels)
        has_enclosure = any(r == "enclosure" for r in rels)

        if not has_acq and not has_subsection:
            issues.append("NO-ACQ-OR-SUBSECTION")
        if has_acq and not has_image:
            issues.append("NO-IMAGE-LINK")
        if has_acq and not has_thumb:
            issues.append("NO-THUMBNAIL-LINK")
        if has_acq and not has_pse:
            issues.append("NO-PSE-LINK")
        if has_acq and not has_enclosure:
            issues.append("NO-ENCLOSURE")

        # Check for missing type attribute on links
        for l in links:
            if not l.get("type"):
                rel = l.get("rel", "?")
                issues.append("LINK-NO-TYPE:" + rel)
            if not l.get("href"):
                rel = l.get("rel", "?")
                issues.append("LINK-NO-HREF:" + rel)

        # Check for encoding issues in title
        if title:
            try:
                title.encode("ascii")
            except UnicodeEncodeError:
                # Non-ASCII is fine, but check for control characters
                if any(ord(c) < 32 and c not in "\n\r\t" for c in title):
                    issues.append("CONTROL-CHARS-IN-TITLE")

        status = " ".join(issues) if issues else "OK"
        marker = "✗" if issues else "✓"
        print(f"  {marker} [{i + 1:2d}] {title[:55]:55s} {status}")

    # --- Check thumbnail availability ---
    print("\n--- Thumbnail Check ---")
    missing_thumbs = 0
    for i, p in enumerate(pubs):
        imgs = p.get("images", [])
        if not imgs:
            title = p.get("metadata", {}).get("title", "?")
            print(f"  ✗ [{i + 1:2d}] {title[:55]} - NO THUMBNAIL")
            missing_thumbs += 1
    if missing_thumbs == 0:
        print("  ✓ All entries have thumbnails")
    else:
        print(f"  {missing_thumbs} entries missing thumbnails")


if __name__ == "__main__":
    paths = sys.argv[1:] if len(sys.argv) > 1 else [
        "DC%20Comics/Absolute%20Flash%20(2025)",
        "DC%20Comics/Absolute%20Green%20Lantern%20(2025)",
    ]
    for p in paths:
        analyze_entries(p)
