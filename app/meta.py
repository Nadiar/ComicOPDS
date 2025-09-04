from zipfile import ZipFile
from xml.etree import ElementTree as ET
from pathlib import Path

FIELDS = [
    "Series","Number","Volume","Title","Summary",
    "Writer","Penciller","Inker","Colorist","Letterer","CoverArtist",
    "Publisher","Imprint",
    "Year","Month","Day",
    "Genre","Tags","Characters","Teams","Locations",
    "Web","LanguageISO",
    "ComicVineIssue"
]

def read_comicinfo(cbz_path: Path) -> dict:
    try:
        with ZipFile(cbz_path) as z:
            name = next((n for n in z.namelist() if n.lower() == "comicinfo.xml"), None)
            if not name:
                return {}
            with z.open(name) as f:
                xml = f.read()
        root = ET.fromstring(xml)
    except Exception:
        return {}

    def g(tag):
        el = root.find(tag)
        return el.text.strip() if el is not None and el.text else None

    meta = {}
    for f in FIELDS:
        v = g(f)
        if v:
            meta[f.lower()] = v
    return meta
