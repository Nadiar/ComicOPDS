#!/usr/bin/env python3
"""One-off script: Remove ComicInfo.xml from CBZ files where the Series name
doesn't appear in the filename (indicating the metadata was applied to the
wrong file).

Usage:
    python strip_bad_comicinfo.py /path/to/unsorted [--dry-run]
"""
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def get_series_from_comicinfo(cbz_path: Path) -> tuple[str | None, str | None]:
    """Return (series_name, xml_entry_name) from a CBZ's ComicInfo.xml."""
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf:
            xml_name = None
            for n in zf.namelist():
                if n.lower().endswith("comicinfo.xml") and not n.endswith("/"):
                    xml_name = n
                    break
            if not xml_name:
                return None, None
            with zf.open(xml_name) as fp:
                tree = ET.parse(fp)
                root = tree.getroot()
                for el in root:
                    if el.tag.lower() == "series":
                        return (el.text or "").strip(), xml_name
        return None, xml_name
    except Exception as e:
        print(f"  ERROR reading {cbz_path.name}: {e}")
        return None, None


def remove_comicinfo_from_cbz(cbz_path: Path, xml_name: str) -> bool:
    """Rewrite CBZ without the ComicInfo.xml entry."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".cbz", dir=cbz_path.parent)
    os.close(tmp_fd)
    try:
        with zipfile.ZipFile(cbz_path, "r") as zf_in:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf_out:
                for item in zf_in.infolist():
                    if item.filename.lower().endswith("comicinfo.xml"):
                        continue
                    zf_out.writestr(item, zf_in.read(item.filename))
        shutil.move(tmp_path, cbz_path)
        return True
    except Exception as e:
        print(f"  ERROR rewriting {cbz_path.name}: {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python strip_bad_comicinfo.py /path/to/unsorted [--dry-run]")
        sys.exit(1)

    directory = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        sys.exit(1)

    if dry_run:
        print("=== DRY RUN — no files will be modified ===\n")

    cbz_files = sorted(directory.rglob("*.cbz"))
    print(f"Found {len(cbz_files)} CBZ files in {directory}\n")

    stats = {"checked": 0, "match": 0, "mismatch": 0, "no_series": 0, "no_xml": 0, "errors": 0}

    for cbz_path in cbz_files:
        stats["checked"] += 1
        filename_lower = cbz_path.stem.lower()

        series, xml_name = get_series_from_comicinfo(cbz_path)

        if xml_name is None:
            stats["no_xml"] += 1
            continue

        if not series:
            stats["no_series"] += 1
            print(f"  NO SERIES  {cbz_path.name}")
            continue

        if series.lower() in filename_lower:
            stats["match"] += 1
            print(f"  OK         {cbz_path.name}  (Series: {series})")
        else:
            stats["mismatch"] += 1
            print(f"  MISMATCH   {cbz_path.name}  (Series: {series})")
            if not dry_run:
                if remove_comicinfo_from_cbz(cbz_path, xml_name):
                    print(f"             -> Removed ComicInfo.xml")
                else:
                    stats["errors"] += 1

    print(f"\n{'=== DRY RUN SUMMARY ===' if dry_run else '=== SUMMARY ==='}")
    print(f"  Checked:    {stats['checked']}")
    print(f"  Match:      {stats['match']}")
    print(f"  Mismatch:   {stats['mismatch']} {'(would remove)' if dry_run else '(removed)'}")
    print(f"  No Series:  {stats['no_series']}")
    print(f"  No XML:     {stats['no_xml']}")
    if stats["errors"]:
        print(f"  Errors:     {stats['errors']}")


if __name__ == "__main__":
    main()
