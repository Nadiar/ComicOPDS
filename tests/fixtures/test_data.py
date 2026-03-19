# tests/fixtures/test_data.py
from pathlib import Path
from typing import Dict, Any
import sqlite3
from app import db


def create_test_directory_structure(library_dir: Path):
    """Create a sample directory structure for testing."""
    # Root level
    series_dir = library_dir / "Series"
    series_dir.mkdir(parents=True, exist_ok=True)

    # Subdirectory
    comic_series = series_dir / "Amazing Spider-Man"
    comic_series.mkdir(parents=True, exist_ok=True)

    # Another series
    other_series = series_dir / "X-Men"
    other_series.mkdir(parents=True, exist_ok=True)

    return {"series": series_dir, "spider_man": comic_series, "x_men": other_series}


def create_test_cbz_files(test_dirs: Dict[str, Path], count: int = 3):
    """Create placeholder CBZ files in test directories."""
    from tests.fixtures.cbz_samples import create_sample_cbz

    cbz_files = []

    # Create files in spider_man series
    for i in range(count):
        cbz_path = test_dirs["spider_man"] / f"Amazing_Spider-Man_{i+1}.cbz"
        create_sample_cbz(cbz_path, f"Amazing Spider-Man #{i+1}")
        cbz_files.append(cbz_path)

    # Create files in x_men series
    for i in range(count // 2):
        cbz_path = test_dirs["x_men"] / f"X-Men_{i+1}.cbz"
        create_sample_cbz(cbz_path, f"X-Men #{i+1}")
        cbz_files.append(cbz_path)

    return cbz_files


def create_test_cbz(path: Path):
    """Create a simple test CBZ file."""
    from tests.fixtures.cbz_samples import create_sample_cbz
    create_sample_cbz(path, path.stem)


def index_test_data(conn: sqlite3.Connection, library_dir: Path):
    """Add test data to database index."""
    # Example: insert test items and metadata
    test_dirs = create_test_directory_structure(library_dir)
    test_files = create_test_cbz_files(test_dirs)

    # Insert directory entries first (using forward slashes for consistency)
    series_rel = str(test_dirs["series"].relative_to(library_dir)).replace("\\", "/")
    db.upsert_dir(
        conn,
        series_rel,
        test_dirs["series"].name,
        "",
        test_dirs["series"].stat().st_mtime,
    )

    spider_man_rel = str(test_dirs["spider_man"].relative_to(library_dir)).replace("\\", "/")
    db.upsert_dir(
        conn,
        spider_man_rel,
        test_dirs["spider_man"].name,
        series_rel,
        test_dirs["spider_man"].stat().st_mtime,
    )

    x_men_rel = str(test_dirs["x_men"].relative_to(library_dir)).replace("\\", "/")
    db.upsert_dir(
        conn,
        x_men_rel,
        test_dirs["x_men"].name,
        series_rel,
        test_dirs["x_men"].stat().st_mtime,
    )

    for cbz_file in test_files:
        rel_path = str(cbz_file.relative_to(library_dir)).replace("\\", "/")
        parent_path = str(cbz_file.parent.relative_to(library_dir)).replace("\\", "/")
        db.upsert_file(
            conn,
            rel_path,
            cbz_file.name,
            cbz_file.stat().st_size,
            cbz_file.stat().st_mtime,
            parent_path,
            cbz_file.suffix
        )

        # Add basic metadata
        meta = {
            "title": cbz_file.stem,
            "series": cbz_file.parent.name,
            "format": "CBZ"
        }
        db.upsert_meta(conn, rel_path, meta)

    conn.commit()
    return test_dirs, test_files
