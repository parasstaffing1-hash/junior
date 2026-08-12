from __future__ import annotations

import hashlib
import os
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
DESTINATION = DIST / "junior_data_intelligence_os_tools_1_300.zip"
ARCHIVE_ROOT = "junior_data_intelligence_os_tools_1_300"

SOURCE_DIRECTORIES = (
    "app",
    ".github",
    "deploy",
    "docs",
    "examples",
    "famous_datasets",
    "frontend",
    "migrations",
    "scripts",
    "tests",
)
SOURCE_FILES = (
    ".env.example",
    ".gitignore",
    "alembic.ini",
    "DESIGN_ATTRIBUTION.md",
    "docker-compose.yml",
    "Dockerfile",
    "FINAL_VALIDATION_REPORT.md",
    "import_famous.py",
    "preview.py",
    "README.md",
    "requirements.txt",
    "requirements-connectors.txt",
    "validate.py",
)
EXCLUDED_DIRECTORY_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "storage", "dist", "venv", ".venv", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".db", ".log", ".part", ".zip"}


def iter_sources():
    for relative in SOURCE_FILES:
        path = ROOT / relative
        if path.is_file() and not path.is_symlink():
            yield path
    for relative in SOURCE_DIRECTORIES:
        base = ROOT / relative
        if not base.is_dir() or base.is_symlink():
            continue
        for current, directories, files in os.walk(base, followlinks=False):
            directories[:] = sorted(
                name
                for name in directories
                if name not in EXCLUDED_DIRECTORY_NAMES and not (Path(current) / name).is_symlink()
            )
            for name in sorted(files):
                path = Path(current) / name
                if path.is_symlink() or path.suffix.casefold() in EXCLUDED_SUFFIXES:
                    continue
                yield path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def build() -> tuple[Path, int, str]:
    sources = sorted(set(iter_sources()), key=lambda path: path.relative_to(ROOT).as_posix())
    if not sources:
        raise RuntimeError("No source files were selected for packaging.")
    DIST.mkdir(parents=True, exist_ok=True)
    temporary = DESTINATION.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sources:
            relative = source.relative_to(ROOT).as_posix()
            archive.write(source, f"{ARCHIVE_ROOT}/{relative}")
    temporary.replace(DESTINATION)
    with zipfile.ZipFile(DESTINATION) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"Release archive CRC validation failed at {bad}.")
        entries = len(archive.infolist())
    return DESTINATION, entries, digest(DESTINATION)


if __name__ == "__main__":
    path, entries, sha256 = build()
    print(f"path={path}")
    print(f"entries={entries}")
    print(f"bytes={path.stat().st_size}")
    print(f"sha256={sha256}")
