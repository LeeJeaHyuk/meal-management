"""Check the explicit public-code allowlist without opening unexpected data files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{24,}|github_pat_[A-Za-z0-9_]{24,})"),
    "api_token": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}"),
    "literal_bearer": re.compile(r"Bearer\s+[A-Za-z0-9_.~+/=-]{16,}"),
    "personal_home_path": re.compile(r"(?:/home|/Users)/[A-Za-z0-9._-]+/"),
}
BLOCKED_DIRS = {"sensitive","credentials","profiles","workspaces","fixtures","datasets","runs","reports","models"}
BLOCKED_SUFFIXES = {".env",".sqlite",".sqlite3",".db",".png",".jpg",".jpeg",".pdf",".zip",".pem",".key"}
IGNORED_DIRS = {".git","__pycache__",".venv","build","dist"}
MANIFEST = "PUBLIC-FILES.json"

def audit(root):
    root=Path(root).resolve()
    findings=[]
    path=root/MANIFEST
    if path.is_symlink() or not path.is_file():
        return {"passed":False,"findings":[{"code":"explicit_public_manifest_required"}]}
    document=json.loads(path.read_text(encoding="utf-8"))
    entries=document.get("files",{})
    if not isinstance(entries,dict) or not entries:
        return {"passed":False,"findings":[{"code":"nonempty_public_manifest_required"}]}
    allowed=set(entries)|{MANIFEST}
    def scan_text(name,content):
        for code,pattern in PATTERNS.items():
            if pattern.search(content):findings.append({"code":code,"path":name})
    seen=set()
    for folder,dirs,names in os.walk(root,followlinks=False):
        folder=Path(folder)
        for name in list(dirs):
            child=folder/name;relative=child.relative_to(root).as_posix()
            if child.is_symlink():
                findings.append({"code":"symlink_denied","path":relative});dirs.remove(name)
            elif name in BLOCKED_DIRS:
                findings.append({"code":"data_directory_denied","path":relative});dirs.remove(name)
            elif name in IGNORED_DIRS or name.endswith(".egg-info"):
                if name==".git" and child!=root/".git":
                    findings.append({"code":"nested_git_history_denied","path":relative})
                dirs.remove(name)
        for name in names:
            p=folder/name;relative=p.relative_to(root).as_posix()
            if p.is_symlink():findings.append({"code":"symlink_denied","path":relative});continue
            if p.suffix in {".pyc",".pyo"}:continue
            if relative not in allowed or p.suffix.lower() in BLOCKED_SUFFIXES or name==".env":
                findings.append({"code":"unexpected_or_data_file","path":relative});continue
            seen.add(relative)
            if p.stat().st_size>1048576:
                findings.append({"code":"file_size_limit","path":relative});continue
            raw=p.read_bytes()
            if relative in entries and hashlib.sha256(raw).hexdigest()!=entries[relative]:
                findings.append({"code":"public_file_hash_mismatch","path":relative})
            try:scan_text(relative,raw.decode("utf-8"))
            except UnicodeError:findings.append({"code":"utf8_code_only","path":relative})
    for name in sorted(allowed-seen):findings.append({"code":"manifest_file_missing","path":name})
    history=0
    if (root/".git").is_dir():
        commits=subprocess.check_output(["git","rev-list","--all"],cwd=root,text=True).splitlines()
        history=len(commits)
        if history>200:
            findings.append({"code":"history_scan_limit_requires_review"})
        else:
            blobs=set()
            for commit in commits:
                listing=subprocess.check_output(["git","ls-tree","-r","-z",commit],cwd=root)
                for entry in listing.split(b"\0"):
                    if not entry:continue
                    meta,name=entry.split(b"\t",1);mode,kind,sha=meta.decode().split()
                    relative=name.decode()
                    if relative not in allowed or kind!="blob":
                        findings.append({"code":"unexpected_history_path","path":relative});continue
                    if sha in blobs:continue
                    blobs.add(sha)
                    size=int(subprocess.check_output(["git","cat-file","-s",sha],cwd=root,text=True))
                    if size>1048576:
                        findings.append({"code":"history_blob_size_limit","path":relative});continue
                    raw=subprocess.check_output(["git","cat-file","blob",sha],cwd=root)
                    try:scan_text(relative,raw.decode("utf-8"))
                    except UnicodeError:findings.append({"code":"history_binary_denied","path":relative})
    return {"passed":not findings,"files_checked":len(seen),"history_commits_checked":history,
            "actual_credential_values_read":False,"findings":findings}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--root",default=".")
    args=p.parse_args()
    try:result=audit(args.root)
    except (OSError,ValueError,subprocess.CalledProcessError):
        result={"passed":False,"findings":[{"code":"public_tree_inspection_failed"}]}
    print(json.dumps(result,ensure_ascii=False))
    return 0 if result["passed"] else 1

if __name__=="__main__":raise SystemExit(main())
