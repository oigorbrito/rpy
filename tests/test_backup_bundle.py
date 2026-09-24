from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SCRIPT = ROOT / "scripts" / "verify_backup_bundle.sh"


def _fake_pg_restore(bin_dir: Path) -> None:
    script = bin_dir / "pg_restore"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)


def _run_verifier(tmp_path: Path, backup: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _fake_pg_restore(bin_dir)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        ["sh", str(VERIFY_SCRIPT), str(backup)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_backup_bundle_checksum_is_bound_to_requested_archive(tmp_path: Path) -> None:
    backup = tmp_path / "rpy.dump"
    other = tmp_path / "other.dump"
    backup.write_bytes(b"requested-backup")
    other.write_bytes(b"different-valid-file")

    other_digest = hashlib.sha256(other.read_bytes()).hexdigest()
    (tmp_path / "rpy.dump.sha256").write_text(
        f"{other_digest}  other.dump\n",
        encoding="utf-8",
    )

    completed = _run_verifier(tmp_path, backup)

    if completed.returncode == 0:
        raise AssertionError("checksum for another file must not verify the requested backup")
    if "checksum mismatch for backup" not in completed.stderr:
        raise AssertionError(f"unexpected verifier failure: {completed.stderr!r}")


def test_backup_bundle_accepts_matching_digest_even_after_relocation(tmp_path: Path) -> None:
    backup = tmp_path / "rpy.dump"
    backup.write_bytes(b"relocated-backup")
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    (tmp_path / "rpy.dump.sha256").write_text(
        f"{digest}  original-name.dump\n",
        encoding="utf-8",
    )

    completed = _run_verifier(tmp_path, backup)

    if completed.returncode != 0:
        raise AssertionError(
            f"matching relocated backup should verify: {completed.stderr!r}"
        )
