"""Read-only Git data snapshots into the local immutable food catalog."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from .locking import cache_lock
from .catalog import canonical, data_path, validate_record

ACCEPTED_REF = "refs/food-catalog/accepted"
MANIFEST = {"schema_version": 1, "catalog_id": "food-catalog",
            "record_format": "meal-nutrition-v1", "privacy": "general-food-facts-only"}
MAX_RECORDS = 10000
MAX_RECORD_BYTES = 32768
MAX_SNAPSHOT_BYTES = 67108864


def run_git(cache, *args, stdin=None, allowed=(0,), env=None):
    try:
        result = subprocess.run(["git", "--git-dir", str(cache), *map(str, args)],
                                input=stdin, capture_output=True, timeout=45, env=env)
    except subprocess.TimeoutExpired:
        raise ValueError("catalog_git_timeout") from None
    if result.returncode not in allowed:
        raise ValueError("catalog_git_operation_failed")
    return result


def decode_record(path, content, state):
    match = re.fullmatch(r"(?:records|proposals)/([0-9a-f]{64})\.json", path)
    if not match or len(content) > MAX_RECORD_BYTES:
        raise ValueError("invalid_catalog_record_path_or_size")
    record = validate_record(json.loads(content))
    ref = hashlib.sha256(canonical(record).encode()).hexdigest()
    if ref != match[1] or record["review_state"] != state:
        raise ValueError("catalog_record_hash_or_review_mismatch")
    return ref, record


def check_directory(directory):
    directory = data_path(directory)
    if json.loads((directory / "catalog.json").read_text(encoding="utf-8")) != MANIFEST:
        raise ValueError("unsupported_food_catalog")
    permitted = {".git", ".gitignore", ".github", "README.md", "AGENTS.md", "catalog.json", "schema", "records", "proposals"}
    if any(path.name not in permitted for path in directory.iterdir()):
        raise ValueError("unexpected_catalog_repository_entry")
    count = {"records": 0, "proposals": 0}
    for group, state in (("records", "verified"), ("proposals", "unreviewed")):
        root = data_path(directory / group)
        if not root.exists():
            continue
        for path in root.iterdir():
            path = data_path(path)
            if not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
                raise ValueError("invalid_catalog_entry")
            decode_record(group + "/" + path.name, path.read_bytes(), state)
            count[group] += 1
            if sum(count.values()) > MAX_RECORDS:
                raise ValueError("catalog_snapshot_too_large")
    return {"state": "validated", **count, "semantic_source_review_proven": False}


def entries(cache, commit):
    raw = run_git(cache, "ls-tree", "-r", "-z", commit, "--", "records").stdout
    values = []
    for row in raw.split(b"\0"):
        if not row:
            continue
        meta, path = row.split(b"\t", 1)
        mode, kind, sha = meta.decode().split()
        path = path.decode("utf-8")
        if mode != "100644" or kind != "blob" or not re.fullmatch(r"records/[0-9a-f]{64}\.json", path):
            raise ValueError("invalid_catalog_tree_entry")
        values.append((path, sha))
    if len(values) > MAX_RECORDS:
        raise ValueError("catalog_snapshot_too_large")
    return values


def blobs(cache, shas):
    if not shas:
        return []
    request = ("\n".join(shas) + "\n").encode()
    sizes = run_git(cache, "cat-file", "--batch-check", stdin=request).stdout.splitlines()
    if len(sizes) != len(shas):
        raise ValueError("catalog_batch_mismatch")
    total = 0
    for expected, line in zip(shas, sizes):
        sha, kind, size = line.decode().split()
        if sha != expected or kind != "blob" or not 0 < int(size) <= MAX_RECORD_BYTES:
            raise ValueError("catalog_blob_limit")
        total += int(size)
    if total > MAX_SNAPSHOT_BYTES:
        raise ValueError("catalog_snapshot_too_large")
    raw = run_git(cache, "cat-file", "--batch", stdin=request).stdout
    result, offset = [], 0
    for expected in shas:
        end = raw.index(b"\n", offset)
        sha, kind, size = raw[offset:end].decode().split()
        size = int(size)
        if sha != expected or kind != "blob":
            raise ValueError("catalog_batch_mismatch")
        offset = end + 1
        result.append(raw[offset:offset + size])
        offset += size + 1
    if offset != len(raw):
        raise ValueError("catalog_batch_mismatch")
    return result


def import_snapshot(cache, commit, catalog):
    listing = run_git(cache, "ls-tree", commit, "--", "catalog.json").stdout
    if not listing.startswith(b"100644 blob "):
        raise ValueError("catalog_manifest_missing")
    manifest = json.loads(blobs(cache, [listing.decode().split()[2]])[0])
    if manifest != MANIFEST:
        raise ValueError("unsupported_food_catalog")
    previous_result = run_git(cache, "rev-parse", "--verify", ACCEPTED_REF, allowed=(0, 128))
    previous = previous_result.stdout.decode().strip() if previous_result.returncode == 0 else None
    if previous and run_git(cache, "merge-base", "--is-ancestor", previous, commit, allowed=(0, 1)).returncode:
        raise ValueError("catalog_history_not_fast_forward")
    tree = entries(cache, commit)
    if previous and not {path for path, _ in entries(cache, previous)} <= {path for path, _ in tree}:
        raise ValueError("historical_catalog_records_removed")
    records = [decode_record(path, content, "verified")
               for (path, _), content in zip(tree, blobs(cache, [sha for _, sha in tree]))]
    # Validate the entire snapshot before adding any immutable local record.
    added = 0
    for ref, record in records:
        existed = (catalog.root / (ref + ".json")).exists()
        if catalog.put(record)["nutrition_ref"] != ref:
            raise ValueError("catalog_import_hash_mismatch")
        added += int(not existed)
    args = ["update-ref", ACCEPTED_REF, commit]
    if previous:
        args.append(previous)
    run_git(cache, *args)
    return {"state": "synced", "commit": commit, "previous_commit": previous,
            "record_count": len(records), "added": added,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "personal_journal_accessed": False}


def sync_catalog(catalog, source_binding):
    config = json.loads(data_path(source_binding).read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("unsupported_catalog_source_binding")
    remote = config["remote"]
    if not re.fullmatch(r"git@github\.com:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", remote):
        raise ValueError("explicit_github_ssh_remote_required")
    cache = data_path(config["cache"])
    if "sensitive" in cache.parts or cache == catalog.root or cache in catalog.root.parents or catalog.root in cache.parents:
        raise ValueError("catalog_cache_location_invalid")
    key = Path(config["ssh_key"]).expanduser()
    if not key.is_absolute() or not key.is_file():
        raise ValueError("approved_catalog_read_key_missing")
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
                "GIT_SSH_COMMAND": shlex.join(["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes",
                                              "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])})
    with cache_lock(cache):
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "--bare", str(cache)], check=True, capture_output=True, timeout=15)
            run_git(cache, "config", "foodcatalog.cacheVersion", "1")
            run_git(cache, "remote", "add", "origin", remote)
        marker = run_git(cache, "config", "--get", "foodcatalog.cacheVersion", allowed=(0, 1)).stdout.strip()
        bare = run_git(cache, "rev-parse", "--is-bare-repository").stdout.strip()
        if marker != b"1" or bare != b"true":
            raise ValueError("unowned_catalog_cache")
        if run_git(cache, "remote", "get-url", "origin").stdout.decode().strip() != remote:
            raise ValueError("catalog_remote_mismatch")
        run_git(cache, "fetch", "--no-tags", "origin", "refs/heads/main", env=env)
        commit = run_git(cache, "rev-parse", "FETCH_HEAD^{commit}").stdout.decode().strip()
        return import_snapshot(cache, commit, catalog)
