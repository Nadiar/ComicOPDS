#!/usr/bin/env python3
"""Strip CVVolumeID from ComicInfo.xml inside CBZ files.

Usage:
    python strip_cvvolumeid.py /path/to/unsorted [--dry-run]

Removes <Notes> content matching CVVolumeID patterns and any
dedicated ComicVine volume tags from ComicInfo.xml embedded in CBZ files.
"""
import argparse
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def strip_cvvolumeid_from_xml(xml_bytes: bytes) -> tuple[bytes | None, list[str]]:
    """Remove CVVolumeID references from ComicInfo.xml content.

    Returns (modified_xml_bytes, list_of_changes) or (None, []) if no changes.
    """
    changes = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None, []

    # Strip from <Notes> field (e.g. "CVVolumeID=141907" or "Tagged with ComicTagger ... [Issue ID 12345]")
    notes_el = root.find("Notes")
    if notes_el is not None and notes_el.text:
        original = notes_el.text
        # Remove CVVolumeID=NNNNN patterns
        cleaned = re.sub(r"CVVolumeID=\d+", "", original)
        # Remove CVDB references like [Volume ID NNNNN] or [Issue ID NNNNN]
        cleaned = re.sub(r"\[Volume ID \d+\]", "", cleaned)
        # Clean up leftover separators
        cleaned = re.sub(r"[,;]\s*[,;]", ",", cleaned)
        cleaned = re.sub(r"^\s*[,;]\s*", "", cleaned)
        cleaned = re.sub(r"\s*[,;]\s*$", "", cleaned)
        cleaned = cleaned.strip()

        if cleaned != original:
            if cleaned:
                notes_el.text = cleaned
            else:
                root.remove(notes_el)
            changes.append(f"Notes: '{original}' -> '{cleaned or '(removed)'}'")

    # Remove dedicated tags if they exist
    for tag_name in ("ComicVineVolumeID", "ComicVineVolume"):
        el = root.find(tag_name)
        if el is not None:
            val = el.text or ""
            root.remove(el)
            changes.append(f"Removed <{tag_name}>{val}</{tag_name}>")

    if not changes:
        return None, []

    # Re-serialize
    ET.indent(root, space="  ")
    out = ET.tostring(root, encoding="unicode", xml_declaration=True)
    return out.encode("utf-8"), changes


def process_cbz(cbz_path: Path, dry_run: bool = False) -> bool:
    """Process a single CBZ file, stripping CVVolumeID from its ComicInfo.xml."""
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf:
            xml_name = None
            for n in zf.namelist():
                if n.lower().endswith("comicinfo.xml") and not n.endswith("/"):
                    xml_name = n
                    break
            if not xml_name:
                return False

            xml_bytes = zf.read(xml_name)

        modified_xml, changes = strip_cvvolumeid_from_xml(xml_bytes)
        if modified_xml is None:
            return False

        print(f"  {'[DRY RUN] ' if dry_run else ''}Modifying: {cbz_path.name}")
        for c in changes:
            print(f"    {c}")

        if dry_run:
            return True

        # Rewrite the CBZ with modified ComicInfo.xml
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".cbz", dir=cbz_path.parent)
        os.close(tmp_fd)
        try:
            with zipfile.ZipFile(cbz_path, "r") as zf_in, \
                 zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf_out:
                for item in zf_in.infolist():
                    if item.filename == xml_name:
                        zf_out.writestr(item, modified_xml)
                    else:
                        zf_out.writestr(item, zf_in.read(item.filename))
            # Replace original
            shutil.move(tmp_path, cbz_path)
            return True
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    except zipfile.BadZipFile:
        print(f"  WARNING: Not a valid ZIP: {cbz_path.name}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  ERROR processing {cbz_path.name}: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Strip CVVolumeID from ComicInfo.xml in CBZ files")
    parser.add_argument("directory", help="Directory containing CBZ files to process")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without modifying files")
    parser.add_argument("--recursive", "-r", action="store_true", help="Process subdirectories recursively")
    args = parser.parse_args()

    target = Path(args.directory)
    if not target.is_dir():
        print(f"ERROR: {target} is not a directory", file=sys.stderr)
        sys.exit(1)

    if args.recursive:
        cbz_files = sorted(target.rglob("*.cbz"))
    else:
        cbz_files = sorted(target.glob("*.cbz"))

    print(f"Found {len(cbz_files)} CBZ files in {target}")
    if args.dry_run:
        print("DRY RUN - no files will be modified\n")

    modified = 0
    for cbz in cbz_files:
        if process_cbz(cbz, dry_run=args.dry_run):
            modified += 1

    print(f"\n{'Would modify' if args.dry_run else 'Modified'}: {modified}/{len(cbz_files)} files")


if __name__ == "__main__":
    main()
