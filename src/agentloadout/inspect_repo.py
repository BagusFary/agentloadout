"""Clone a GitHub repo into a scratch folder and look for red flags — WITHOUT running any of its code.

The result is evidence for a human: what the repo contains, where the MCP entry point or skills
are, and lines that deserve a closer look. It is not a security guarantee.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from . import paths

TEXT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".sh", ".bash", ".ps1", ".md", ".json", ".toml",
            ".yaml", ".yml", ".go", ".rs", ".rb", ".php", ".txt"}
MAX_FILES = 4000
MAX_BYTES = 400_000

RED_FLAGS = [
    ("download-and-run", "high", r"(curl|wget)[^\n|]{0,200}\|\s*(sudo\s+)?(ba|z)?sh\b|iex\s*\(\s*(irm|iwr|invoke-webrequest)"),
    ("eval-dynamic", "medium", r"\beval\s*\(|new\s+Function\s*\(|\bexec\s*\(\s*(compile|base64)"),
    ("shell-exec", "medium", r"child_process|subprocess\.(run|Popen|call)\([^)]*shell\s*=\s*True|os\.system\s*\(|execSync\s*\("),
    ("obfuscation", "high", r"(base64\s+-d|atob\s*\(|b64decode\s*\()[^\n]{0,80}(exec|eval|sh\b|Function)|\\x[0-9a-f]{2}(\\x[0-9a-f]{2}){40,}"),
    ("credential-access", "high", r"\.ssh/id_|\.aws/credentials|\.npmrc|\.netrc|\.git-credentials|Login Data|Cookies\.binarycookies|keychain"),
    ("token-env-read", "medium", r"process\.env\.(GITHUB|OPENAI|ANTHROPIC|AWS)_[A-Z_]*TOKEN|os\.environ\[['\"](GITHUB|OPENAI|ANTHROPIC)"),
    ("data-upload", "medium", r"(fetch|axios\.post|requests\.post|http\.request)\s*\([^)]{0,120}(webhook|discord(app)?\.com/api/webhooks|telegram\.org/bot|ngrok|pastebin)"),
    ("postinstall-script", "high", r"\"(pre|post)install\"\s*:"),
    ("prompt-injection", "medium", r"ignore (all )?(previous|prior) instructions|do not tell the user|disable (your )?safety"),
]


def _clone(full_name: str, dest: Path) -> str:
    if dest.exists():
        shutil.rmtree(dest)
    url = f"https://github.com/{full_name}.git"
    p = subprocess.run(["git", "clone", "--depth", "1", "--quiet", url, str(dest)],
                       capture_output=True, text=True, timeout=180,
                       env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(dest.parent)})
    if p.returncode != 0:
        raise RuntimeError(f"git clone gagal: {p.stderr.strip()[:300]}")
    sha = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return sha


def inspect(full_name: str, focus: str | None = None) -> dict:
    """Clone (shallow) and scan. `focus` limits the scan to a sub-folder (e.g. a skill path)."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
        raise ValueError("nama repo harus owner/repo")
    dest = paths.work_dir() / full_name.replace("/", "__")
    sha = _clone(full_name, dest)
    root = dest / focus if focus else dest
    if focus and (not root.resolve().is_relative_to(dest.resolve()) or not root.exists()):
        raise ValueError(f"folder {focus} tidak ada di repo")

    files, flags = [], []
    for f in sorted(root.rglob("*")):
        if ".git" in f.parts or not f.is_file():
            continue
        rel = f.relative_to(dest).as_posix()
        files.append(rel)
        if len(files) > MAX_FILES:
            break
        if f.suffix.lower() not in TEXT_EXT and f.name not in ("Dockerfile", "Makefile", "SKILL.md"):
            continue
        try:
            text = f.read_bytes()[:MAX_BYTES].decode("utf-8", "replace")
        except OSError:
            continue
        for code, level, pat in RED_FLAGS:
            for m in re.finditer(pat, text, re.I):
                line = text.count("\n", 0, m.start()) + 1
                snippet = text.splitlines()[line - 1].strip()[:160] if text.splitlines() else ""
                flags.append({"code": code, "level": level, "file": rel, "line": line, "text": snippet})
                break  # one hit per flag per file is enough

    def read(name, limit=4000):
        p = dest / name
        return p.read_text(errors="replace")[:limit] if p.exists() else None

    skills = sorted({Path(p).parent.as_posix() for p in files if p.endswith("SKILL.md")})
    manifest = next((n for n in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod", "server.json") if (dest / n).exists()), None)
    lic = next((n for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING") if (dest / n).exists()), None)
    return {
        "repo": full_name, "commit": sha[:12], "path": str(dest), "focus": focus,
        "files": len(files), "file_sample": files[:40],
        "license_file": lic, "manifest": manifest, "manifest_text": read(manifest, 3000) if manifest else None,
        "readme_excerpt": next((read(n, 2500) for n in ("README.md", "readme.md", "README") if (dest / n).exists()), None),
        "skill_folders": skills[:60],
        "red_flags": flags[:60],
        "summary": _summary(flags),
        "note": "Pemeriksaan statis berbasis pola; tidak menjalankan kode. Bukan jaminan aman — tinjau temuan dan kode intinya.",
    }


def _summary(flags):
    high = sum(1 for f in flags if f["level"] == "high")
    med = sum(1 for f in flags if f["level"] == "medium")
    if high:
        return f"{high} temuan berisiko tinggi, {med} sedang — tinjau sebelum memasang"
    if med:
        return f"{med} temuan sedang — biasanya wajar untuk MCP/CLI, tetap cek konteksnya"
    return "tidak ada pola berbahaya yang dikenali"
