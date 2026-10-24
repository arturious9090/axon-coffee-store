"""Back up a local demo and prove restoration into an owned scratch database."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", default="desktop-linux")
    parser.add_argument("--media-root", default="data/media")
    parser.add_argument("--report", default="docs/verification/restore-summary.json")
    args = parser.parse_args()
    docker = ["docker", "--context", args.context]
    host = subprocess.check_output(docker + ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], text=True).strip()
    if not host.startswith(("unix://", "npipe://")):
        parser.error("This verification is restricted to a local Docker socket")
    compose = docker + ["compose", "exec", "-T", "db"]
    suffix = uuid.uuid4().hex[:12]
    scratch = "plyn_restore_check_" + suffix
    backup = Path("data/backups") / suffix
    backup.mkdir(parents=True, exist_ok=False)
    media = Path(args.media_root)
    media.mkdir(parents=True, exist_ok=True)
    probe = media / f"restore-probe-{suffix}.webp"
    shutil.copyfile("static/images/product-plyn-400.webp", probe)
    dump = backup / "database.dump"
    archive = backup / "media.tar.gz"

    def psql(database, query):
        return subprocess.check_output(compose + ["psql", "-U", "plyn", "-d", database, "-At", "-c", query], text=True).strip()

    def signatures(database):
        tables = psql(database, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines()
        result = {}
        for table in tables:
            # Names come from PostgreSQL's own catalog, not a user argument.
            quoted = '"' + table.replace('"', '""') + '"'
            result[table] = psql(database, f"SELECT count(*)::text || ':' || md5(coalesce(string_agg(to_jsonb(t)::text, E'\\n' ORDER BY to_jsonb(t)::text), '')) FROM {quoted} t")
        return result

    def hashes(directory):
        return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.rglob("*")) if path.is_file()}

    created = False
    try:
        expected = signatures("plyn")
        with dump.open("wb") as output:
            subprocess.run(compose + ["pg_dump", "-U", "plyn", "-d", "plyn", "-Fc"], stdout=output, check=True)
        dump.chmod(0o600)
        expected_media = hashes(media)
        with tarfile.open(archive, "w:gz") as output:
            output.add(media, arcname="media")
        archive.chmod(0o600)
        subprocess.run(compose + ["createdb", "-U", "plyn", scratch], check=True)
        created = True
        with dump.open("rb") as source:
            subprocess.run(compose + ["pg_restore", "-U", "plyn", "-d", scratch, "--no-owner", "--exit-on-error"], stdin=source, check=True)
        assert signatures(scratch) == expected, "Restored database rows differ"
        restored = backup / "restored"
        restored.mkdir()
        with tarfile.open(archive, "r:gz") as source:
            source.extractall(restored, filter="data")
        assert hashes(restored / "media") == expected_media, "Restored media differs"
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({"checked_at": datetime.now(timezone.utc).isoformat(), "database": "PostgreSQL 17 local Docker", "table_count": len(expected), "tables": {table: int(value.split(":")[0]) for table, value in expected.items()}, "media_files": len(expected_media), "all_row_signatures_match": True, "all_media_sha256_match": True, "original_database_modified": False}, indent=2, ensure_ascii=False) + "\n")
        print(f"Verified restoration: {len(expected)} tables, {len(expected_media)} media files; report: {report}")
    finally:
        if created:
            subprocess.run(compose + ["dropdb", "-U", "plyn", scratch], check=True)
        probe.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
