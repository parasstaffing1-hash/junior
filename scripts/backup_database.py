"""Create a consistent SQL dump for PostgreSQL production backups."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    url = os.getenv("DATABASE_URL", "")
    destination = Path(os.getenv("BACKUP_ROOT", "./backups")).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not url.startswith("postgresql"):
        raise SystemExit("DATABASE_URL must be a PostgreSQL URL for production backups.")
    output = destination / f"analytics_{stamp}.sql"
    subprocess.run(["pg_dump", "--dbname", url, "--format", "plain", "--file", str(output), "--no-owner", "--no-privileges"], check=True)
    print(output)


if __name__ == "__main__":
    main()
