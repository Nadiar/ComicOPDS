# tests/fixtures/cbz_samples.py
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image
import io


def create_sample_cbz(output_path: Path, title: str, pages: int = 3):
    """
    Create a minimal sample CBZ file for testing.

    CBZ = ZIP file containing JPG/PNG images in order.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
        # Create sample ComicInfo.xml
        comic_info = ET.Element('ComicInfo')
        ET.SubElement(comic_info, 'Title').text = title
        ET.SubElement(comic_info, 'Series').text = title.rsplit(' #', 1)[0] if '#' in title else title

        xml_str = ET.tostring(comic_info, encoding='unicode')
        cbz.writestr('ComicInfo.xml', xml_str)

        # Create sample page images
        for page_num in range(1, pages + 1):
            # Create minimal 10x10 pixel JPEG
            img = Image.new('RGB', (10, 10), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='JPEG')
            img_bytes.seek(0)

            page_filename = f"{page_num:03d}.jpg"
            cbz.writestr(page_filename, img_bytes.read())


def create_cbz_with_metadata(output_path: Path, metadata: dict, pages: int = 3):
    """Create CBZ with specific metadata."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as cbz:
        # Create ComicInfo.xml with provided metadata
        comic_info = ET.Element('ComicInfo')

        for key, value in metadata.items():
            if value is not None:
                ET.SubElement(comic_info, key).text = str(value)

        xml_str = ET.tostring(comic_info, encoding='unicode')
        cbz.writestr('ComicInfo.xml', xml_str)

        # Create sample pages
        for page_num in range(1, pages + 1):
            img = Image.new('RGB', (100, 100), color='white')
            img_bytes = io.BytesIO()
            img.save(img_bytes, format='JPEG')
            img_bytes.seek(0)

            cbz.writestr(f"{page_num:03d}.jpg", img_bytes.read())
