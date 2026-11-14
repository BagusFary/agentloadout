"""Collect MCP servers, agent skills and agent apps from GitHub + the official MCP registry.

Usage: GITHUB_TOKEN_FILE=/path/to/token python3 collect.py
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time

import requests

from . import config, db, paths  # noqa: E402

GH = "https://api.github.com"
REGISTRY = "https://registry.modelcontextprotocol.io/v0/servers"
UA = "katalog-agen/1.0"
TODAY = dt.date.today()

MIN_STARS = 30
LIMITS = {"mcp": 260, "skill": 160, "app": 120}


def token() -> str:
    return config.github_token()


S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept": "application/vnd.github+json"})
_tok = token()
if _tok:
    S.headers["Authorization"] = f"Bearer {_tok}"


def gh_get(path, params=None, accept=None, ok404=True):
    headers = {"Accept": accept} if accept else None
    for attempt in range(4):
        r = S.get(GH + path, params=params, headers=headers, timeout=30)
        if r.status_code == 404 and ok404:
            return None
        if r.status_code in (403, 429) and "rate limit" in r.text.lower():
            reset = int(r.headers.get("x-ratelimit-reset", time.time() + 60))
            wait = max(5, min(120, reset - int(time.time()) + 2))
            print(f"  rate limit, tunggu {wait}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r
    return None


# --------------------------------------------------------------------------- search
def search(q, pages=1):
    out = []
    for page in range(1, pages + 1):
        r = gh_get("/search/repositories", {"q": q, "sort": "stars", "order": "desc", "per_page": 100, "page": page})
        time.sleep(2.2)  # search API: 30 requests / minute
        if not r:
            break
        items = r.json().get("items", [])
        out += items
        if len(items) < 100:
            break
    return out


def queries():
    recent = (TODAY - dt.timedelta(days=60)).isoformat()
    base = f"stars:>={MIN_STARS} fork:false"
    return [
        ("mcp", f"topic:mcp-server {base}", 3),
        ("mcp", f"topic:model-context-protocol {base}", 1),
        ("mcp", f"topic:mcp {base}", 1),
        ("mcp", f"topic:mcp-server created:>{recent} {base}", 1),
        ("skill", f"topic:claude-skills {base}", 1),
        ("skill", f"topic:agent-skills {base}", 1),
        ("skill", f"topic:claude-skill {base}", 1),
        ("skill", f"topic:skills topic:claude {base}", 1),
        ("skill", f"topic:agent-skills created:>{recent} {base}", 1),
        ("app", f"topic:claude-code {base}", 2),
        ("app", f"topic:codex-cli {base}", 1),
        ("app", f"topic:claude-code created:>{recent} {base}", 1),
        # repos that do not use GitHub topics
        ("mcp", '"mcp server" in:name,description stars:>=200 fork:false', 2),
        ("mcp", '"model context protocol" in:description stars:>=150 fork:false', 1),
        ("skill", '"agent skills" in:description stars:>=150 fork:false', 1),
        ("skill", '"claude code skill" in:description stars:>=100 fork:false', 1),
        ("skill", '"SKILL.md" in:readme stars:>=150 fork:false', 1),
    ]


# --------------------------------------------------------------------------- registry
CACHE_PATH = str(paths.cache_file("registry_cache.json"))
CACHE_DAYS = 7
_cache = {}


def load_cache():
    global _cache, _skills_cache
    try:
        _skills_cache = json.load(open(SKILLS_CACHE_PATH))
    except (OSError, ValueError):
        _skills_cache = {}
    try:
        _cache = json.load(open(CACHE_PATH))
    except (OSError, ValueError):
        _cache = {}


def save_cache():
    tmp2 = SKILLS_CACHE_PATH + ".tmp"
    json.dump(_skills_cache, open(tmp2, "w"))
    os.replace(tmp2, SKILLS_CACHE_PATH)
    tmp = CACHE_PATH + ".tmp"
    json.dump(_cache, open(tmp, "w"))
    os.replace(tmp, CACHE_PATH)


def registry_lookup(full_name):
    """Find the latest official-registry entry whose repository is this GitHub repo (cached for 7 days)."""
    key = full_name.lower()
    hit = _cache.get(key)
    if hit and (TODAY - dt.date.fromisoformat(hit["day"])).days < CACHE_DAYS:
        return hit["server"]
    found = None
    repo_name = full_name.split("/")[1]
    for term in dict.fromkeys([repo_name, full_name.split("/")[0]]):
        try:
            r = requests.get(REGISTRY, params={"search": term, "version": "latest", "limit": 100},
                             timeout=30, headers={"User-Agent": UA})
            r.raise_for_status()
        except Exception:  # noqa: BLE001  registry is optional
            return None
        for e in r.json().get("servers", []):
            srv = e.get("server", {})
            url = (srv.get("repository") or {}).get("url") or ""
            m = re.search(r"github\.com/([^/]+/[^/#?]+)", url)
            if m and m.group(1).removesuffix(".git").lower() == key:
                found = srv
                break
        if found:
            break
    _cache[key] = {"day": TODAY.isoformat(), "server": found}
    return found


# --------------------------------------------------------------------------- enrichment
def readme(full):
    r = gh_get(f"/repos/{full}/readme", accept="application/vnd.github.raw")
    return r.text[:40000] if r else ""


def skill_paths(full, branch):
    r = gh_get(f"/repos/{full}/git/trees/{branch}", {"recursive": "1"})
    if not r:
        return []
    paths = [t["path"] for t in r.json().get("tree", []) if t["path"].endswith("SKILL.md")]
    dirs = sorted({os.path.dirname(p) or "." for p in paths})
    return dirs[:200]


SKILLS_CACHE_PATH = str(paths.cache_file("skills_cache.json"))
_skills_cache = {}
RAW = requests.Session()
RAW.headers.update({"User-Agent": UA})


def frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out = {}
    if not m:
        return out
    key = None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1).lower(), kv.group(2).strip()
            out[key] = val.strip('"').strip("'") if val not in ("|", ">", ">-", "|-") else ""
        elif key and line.startswith((" ", "\t")):
            out[key] = (out.get(key, "") + " " + line.strip()).strip()
    return out


def skill_meta(full, branch, paths, pushed_at):
    hit = _skills_cache.get(full)
    if hit and hit.get("pushed_at") == pushed_at and hit.get("paths") == paths[:40]:
        return hit["skills"]
    skills = []
    for p in paths[:40]:
        rel = "SKILL.md" if p == "." else f"{p}/SKILL.md"
        try:
            r = RAW.get(f"https://raw.githubusercontent.com/{full}/{branch}/{rel}", timeout=20)
            fm = frontmatter(r.text[:8000]) if r.ok else {}
        except requests.RequestException:
            fm = {}
        skills.append({"path": p, "name": (fm.get("name") or os.path.basename(p.rstrip("/")) or full.split("/")[1])[:80],
                       "description": (fm.get("description") or "")[:300]})
    _skills_cache[full] = {"pushed_at": pushed_at, "paths": paths[:40], "skills": skills}
    return skills


def root_file(full, name):
    r = gh_get(f"/repos/{full}/contents/{name}", accept="application/vnd.github.raw")
    return r.text if r else None


def package_hint(full):
    pkg = root_file(full, "package.json")
    if pkg:
        try:
            j = json.loads(pkg)
            if j.get("name") and not j.get("private"):
                return {"type": "npm", "name": j["name"], "bin": bool(j.get("bin"))}
        except ValueError:
            pass
    py = root_file(full, "pyproject.toml")
    if py:
        m = re.search(r'^\s*name\s*=\s*"([^"]+)"', py, re.M)
        if m:
            return {"type": "pypi", "name": m.group(1), "bin": "[project.scripts]" in py}
    return None


# --------------------------------------------------------------------------- classification
CATEGORIES = [
    ("Browser & Web", r"browser|playwright|puppeteer|selenium|scrap|crawl|web ?search|search engine|fetch"),
    ("Database & Data", r"database|\bsql\b|postgres|mysql|sqlite|mongo|redis|supabase|bigquery|snowflake|vector|clickhouse|duckdb"),
    ("Git & DevTools", r"github|gitlab|\bgit\b|code review|\blsp\b|\bide\b|devtools|debug|testing|refactor|codebase"),
    ("Cloud & DevOps", r"\baws\b|azure|\bgcp\b|kubernetes|\bk8s\b|docker|terraform|cloudflare|vercel|devops|server management"),
    ("Dokumen & File", r"\bpdf\b|docx|excel|spreadsheet|markdown|filesystem|file system|notion|obsidian|google drive|document"),
    ("Desain & Media", r"figma|design|image|video|audio|music|blender|\b3d\b|canva|remotion|photo"),
    ("Komunikasi", r"slack|discord|telegram|whatsapp|email|gmail|\bteams\b|messag"),
    ("Memori & Pengetahuan", r"memory|knowledge|\brag\b|graph|second brain|notes|context engineering"),
    ("Keuangan & Bisnis", r"financ|stock|crypto|trading|payment|stripe|accounting|invoice|\bcrm\b|marketing|\bseo\b"),
    ("Produktivitas", r"calendar|todo|\btask|jira|linear|trello|productivity|workflow|automation"),
    ("Agen & Orkestrasi", r"multi-agent|orchestrat|harness|subagent|agent team|fleet of|gateway|router|proxy|claude code|codex|coding agent"),
    ("Riset & AI", r"research|arxiv|paper|\bllm\b|model|agent|prompt"),
]

# (code, label, level, pattern for name/description/topics, pattern for README)
RISKS = [
    ("shell", "Bisa menjalankan perintah shell", "high",
     r"\b(shell|terminal|bash|powershell|command[- ]line executor|code execution|sandbox)\b",
     r"(execut\w*|run\w*) (arbitrary |any |shell |terminal |system )+commands?|shell access|command execution|arbitrary code|\bexec\(|run_command|execute_command|shell_exec"),
    ("desktop", "Bisa mengendalikan desktop/mouse", "high",
     r"computer[- ]use|desktop (control|automation)|mouse|keyboard control|screen control|\brpa\b",
     r"computer[- ]use|desktop automation|control (your|the) (computer|desktop|mouse)|mouse (click|movement)|keyboard input"),
    ("files", "Akses baca/tulis file lokal", "medium",
     r"filesystem|file system|file manager|local files",
     r"(read|write|delete|modify) (and (write|read) )?(local )?files|filesystem access|file system access|write_file|delete_file"),
    ("browser", "Mengendalikan browser", "medium",
     r"playwright|puppeteer|selenium|browser[- ]automation|browser[- ]use|headless",
     r"playwright|puppeteer|selenium|browser automation|headless (chrome|browser)|controls? (a|your|the) browser"),
    ("http", "Bisa membuka server HTTP/SSE", "medium",
     r"\bsse\b|streamable[- ]http",
     r"--transport (sse|http|streamable)|streamable[- ]http transport|sse transport|--host 0\.0\.0\.0"),
    ("cloud", "Akses akun cloud/layanan berbayar", "medium",
     r"\baws\b|azure|\bgcp\b|cloudflare|stripe|kubernetes|terraform",
     r"aws_access_key|azure_|gcp_|cloudflare_api|stripe_(secret|api)|kubeconfig"),
]


def text_blob(repo, rd):
    return " ".join([repo.get("name", ""), repo.get("description") or "", " ".join(repo.get("topics") or []), rd]).lower()


GENERIC = ("Agen & Orkestrasi", "Riset & AI")


def categorize(repo):
    """Specific categories win when they have any keyword hit; generic ones are only the fallback.
    Name and topics weigh more than the description."""
    name = repo.get("name", "").lower().replace("-", " ").replace("_", " ")
    topics = " ".join(t.replace("-", " ") for t in (repo.get("topics") or [])).lower()
    desc = (repo.get("description") or "").lower()
    scores = {}
    for cat, pat in CATEGORIES:
        scores[cat] = 3 * len(re.findall(pat, name)) + 2 * len(re.findall(pat, topics)) + len(re.findall(pat, desc))
    specific = {c: v for c, v in scores.items() if c not in GENERIC and v > 0}
    if specific:
        return max(specific, key=specific.get)
    generic = {c: scores[c] for c in GENERIC if scores.get(c, 0) > 0}
    return max(generic, key=generic.get) if generic else "Lainnya"


def risks_for(repo, rd, env_vars):
    short = text_blob(repo, "")
    long = rd[:20000].lower()
    blob = short + " " + long
    out = []
    for code, label, level, pat_short, pat_long in RISKS:
        if re.search(pat_short, short) or re.search(pat_long, long):
            out.append({"code": code, "label": label, "level": level})
    if env_vars or re.search(r"api[ _-]?key|access token|\bapi token\b", blob):
        out.append({"code": "key", "label": "Butuh API key/token", "level": "info"})
    if repo.get("archived"):
        out.append({"code": "archived", "label": "Repo diarsipkan", "level": "high"})
    pushed = (repo.get("pushed_at") or "")[:10]
    if pushed and (TODAY - dt.date.fromisoformat(pushed)).days > 180:
        out.append({"code": "stale", "label": "Tidak diperbarui > 6 bulan", "level": "medium"})
    if not repo.get("license"):
        out.append({"code": "nolicense", "label": "Tanpa lisensi", "level": "info"})
    created = (repo.get("created_at") or "")[:10]
    if created and (TODAY - dt.date.fromisoformat(created)).days <= 45 and repo.get("stargazers_count", 0) >= 10000:
        out.append({"code": "surge", "label": "Repo sangat baru & naik sangat cepat, cek keasliannya", "level": "medium"})
    return out


def slug(name):
    s = re.sub(r"(^mcp[-_]?server[-_]?|^mcp[-_]|[-_]mcp[-_]?server$|[-_]mcp$|[-_]server$)", "", name.lower())
    s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
    return s or name.lower()


def mcp_install(repo, reg, hint):
    """Return (install snippets, env vars)."""
    name = slug(repo["name"])
    cmd, args, env, remote = None, [], [], None
    if reg:
        pkgs = reg.get("packages") or []
        p = next((x for x in pkgs if x.get("registryType") in ("npm", "pypi", "oci")), None)
        if p:
            ident = p.get("identifier")
            extra = [a.get("value") for a in p.get("packageArguments") or [] if a.get("type") == "positional" and a.get("value")]
            if p["registryType"] == "npm":
                cmd, args = "npx", ["-y", ident] + extra
            elif p["registryType"] == "pypi":
                cmd, args = "uvx", [ident] + extra
            else:
                cmd, args = "docker", ["run", "-i", "--rm", ident] + extra
            env = [{"name": e.get("name"), "secret": bool(e.get("isSecret")), "description": e.get("description") or ""}
                   for e in p.get("environmentVariables") or [] if e.get("name")]
        if not cmd and reg.get("remotes"):
            r0 = reg["remotes"][0]
            remote = {"type": r0.get("type"), "url": r0.get("url")}
    if not cmd and not remote and hint:
        if hint["type"] == "npm":
            cmd, args = "npx", ["-y", hint["name"]]
        elif hint["type"] == "pypi":
            cmd, args = "uvx", [hint["name"]]

    snippets = []
    if remote:
        transport = "http" if "http" in (remote["type"] or "") else "sse"
        snippets.append({"client": "Claude Code", "lang": "bash",
                         "snippet": f"claude mcp add --transport {transport} {name} {remote['url']}"})
        snippets.append({"client": "JSON (mcpServers)", "lang": "json",
                         "snippet": json.dumps({"mcpServers": {name: {"type": transport, "url": remote["url"]}}}, indent=2)})
    elif cmd:
        env_flags = "".join(f" -e {e['name']}=..." for e in env)
        snippets.append({"client": "Claude Code", "lang": "bash",
                         "snippet": f"claude mcp add {name}{env_flags} -- {cmd} {' '.join(args)}".strip()})
        toml = f'[mcp_servers.{name}]\ncommand = "{cmd}"\nargs = {json.dumps(args)}'
        if env:
            toml += "\n[mcp_servers." + name + ".env]\n" + "\n".join(f'{e["name"]} = "..."' for e in env)
        snippets.append({"client": "Codex (~/.codex/config.toml)", "lang": "toml", "snippet": toml})
        block = {"command": cmd, "args": args}
        if env:
            block["env"] = {e["name"]: "..." for e in env}
        snippets.append({"client": "JSON (mcpServers)", "lang": "json",
                         "snippet": json.dumps({"mcpServers": {name: block}}, indent=2)})
    return snippets, env


def skill_install(repo, paths):
    if not paths:
        return []
    first = paths[0]
    src = "." if first == "." else first
    target = os.path.basename(first.rstrip("/")) if first != "." else repo["name"]
    clone = f"git clone --depth 1 {repo['html_url']} /tmp/{repo['name']}"
    return [{"client": "Claude Code", "lang": "bash",
             "snippet": f"{clone}\ncp -r /tmp/{repo['name']}/{src} ~/.claude/skills/{target}"
                        + (f"\n# repo ini berisi {len(paths)} skill; ulangi untuk folder lain" if len(paths) > 1 else "")}]


def app_install(repo, hint):
    if not hint:
        return []
    if hint["type"] == "npm":
        return [{"client": "Terminal (npm)", "lang": "bash", "snippet": f"npm install -g --prefix ~/.local {hint['name']}"}]
    return [{"client": "Terminal (pipx)", "lang": "bash", "snippet": f"pipx install {hint['name']}"}]


AGENT_RE = re.compile(r"agent|claude|codex|\bmcp\b|model context protocol|context protocol|\bllm\b|\bai\b|copilot|cursor|gemini|prompt|skill|openai|anthropic|chatgpt")


SEARCH_TOPICS = {"claude-code", "codex-cli", "claude-skills", "agent-skills", "claude-skill", "skills", "claude"}


def is_agent_related(repo):
    topics = [t for t in (repo.get("topics") or []) if t not in SEARCH_TOPICS]
    return bool(AGENT_RE.search(" ".join([repo.get("description") or "", " ".join(topics)]).lower()))


def is_noise(repo):
    n = repo["name"].lower()
    d = (repo.get("description") or "").lower()
    return n.startswith("awesome") or "awesome list" in d or "curated list" in d or n in ("skills-list",)


def guess_kind(hint_kind, repo, reg):
    topics = set(repo.get("topics") or [])
    name = repo["name"].lower()
    if reg or topics & {"mcp-server", "model-context-protocol", "mcp-servers"} or re.search(r"(^|[-_])mcp($|[-_])", name):
        return "mcp"
    return hint_kind


# --------------------------------------------------------------------------- main
def enrich(cand):
    repo, kind = cand["repo"], cand["kind"]
    full = repo["full_name"]
    reg = registry_lookup(full) if kind == "mcp" else None
    rd = readme(full)
    paths, hint = [], None
    if kind in ("skill", "app"):
        paths = skill_paths(full, repo.get("default_branch") or "HEAD")
        if paths:
            kind = "skill"
        elif kind == "skill":
            kind = "app"
    if kind == "mcp" and not reg:
        hint = package_hint(full)
    if kind == "app":
        hint = package_hint(full)
    skills = skill_meta(full, repo.get("default_branch") or "HEAD", paths, repo.get("pushed_at")) if kind == "skill" else []
    if kind == "mcp":
        install, env = mcp_install(repo, reg, hint)
    elif kind == "skill":
        install, env = skill_install(repo, paths), []
    else:
        install, env = app_install(repo, hint), []
    return {
        "repo": repo, "kind": kind, "reg": reg, "install": install, "env": env,
        "risks": risks_for(repo, rd, env), "paths": paths, "skills": skills,
    }


def text_hash(text):
    import hashlib
    return hashlib.sha1((text or "").strip().encode()).hexdigest()


def translated(con, text):
    if not text:
        return None
    row = con.execute("SELECT text FROM translations WHERE src_hash=? AND lang='id'", (text_hash(text),)).fetchone()
    return row["text"] if row else None


def progress(con, step, label, done=None, total=None, running=True):
    db.set_meta(con, "progress", {
        "running": running, "step": step, "steps": 4, "label": label, "done": done, "total": total,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    })
    con.commit()


def main():
    t0 = time.time()
    con = db.connect()
    try:
        run(con, t0)
    except BaseException:
        con.rollback()  # never keep a half-written save
        progress(con, 0, "Gagal memperbarui", running=False)
        raise


def run(con, t0):
    print("1/4 cache registry...", flush=True)
    progress(con, 1, "Menyiapkan")
    load_cache()

    print("2/4 pencarian GitHub...", flush=True)
    cands = {}
    qs = queries()
    for qi, (hint_kind, q, pages) in enumerate(qs, 1):
        progress(con, 2, "Mencari repo di GitHub", qi - 1, len(qs))
        found = search(q, pages)
        print(f"  {len(found):4d}  {q}", flush=True)
        for repo in found:
            if repo.get("fork") or repo.get("stargazers_count", 0) < MIN_STARS or is_noise(repo):
                continue
            # text searches are noisy: keep only repos that are clearly about agents/MCP/skills
            if not q.startswith("topic:") and not is_agent_related(repo):
                continue
            key = repo["full_name"].lower()
            kind = guess_kind(hint_kind, repo, None)
            if kind == "app" and not is_agent_related(repo):
                continue
            c = cands.setdefault(key, {"repo": repo, "kind": kind, "sources": set()})
            c["sources"].add(q.split(" stars:")[0])
            if kind == "mcp":
                c["kind"] = "mcp"

    # keep the strongest per kind (stars + recency boost for very new repos)
    by_kind = {}
    for c in cands.values():
        by_kind.setdefault(c["kind"], []).append(c)
    selected = []
    for kind, lst in by_kind.items():
        lst.sort(key=lambda c: c["repo"]["stargazers_count"], reverse=True)
        top = lst[: LIMITS.get(kind, 100)]
        recent_cut = (TODAY - dt.timedelta(days=60)).isoformat()
        extras = [c for c in lst[LIMITS.get(kind, 100):] if (c["repo"].get("created_at") or "") > recent_cut][:30]
        selected += top + extras
    print(f"  kandidat: {len(cands)}, dipilih: {len(selected)}", flush=True)

    print("3/4 memperkaya data (README, skill, paket)...", flush=True)
    progress(con, 3, "Membaca detail repo", 0, len(selected))
    results = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(enrich, c): c for c in selected}
        for i, f in enumerate(cf.as_completed(futs), 1):
            try:
                r = f.result()
                r["sources"] = sorted(futs[f]["sources"])
                results.append(r)
            except Exception as e:
                print("  gagal:", futs[f]["repo"]["full_name"], e, flush=True)
            if i % 100 == 0:
                print(f"  {i}/{len(selected)}", flush=True)
            if i % 20 == 0 or i == len(selected):
                progress(con, 3, "Membaca detail repo", i, len(selected))

    save_cache()
    print("4/4 menyimpan...", flush=True)
    progress(con, 4, "Menyimpan")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    today = TODAY.isoformat()
    week_ago = (TODAY - dt.timedelta(days=7)).isoformat()
    seen = set()
    for r in results:
        repo = r["repo"]
        key = repo["full_name"].lower()
        seen.add(key)
        stars = repo["stargazers_count"]
        con.execute("INSERT OR REPLACE INTO star_snapshots(id, day, stars) VALUES(?,?,?)", (key, today, stars))
        old = con.execute("SELECT stars, day FROM star_snapshots WHERE id=? AND day<=? ORDER BY day DESC LIMIT 1",
                          (key, week_ago)).fetchone()
        if old:
            s7 = stars - old["stars"]
            momentum, basis = s7 / 7.0, "7d"
        else:
            s7 = None
            age = max(1, (TODAY - dt.date.fromisoformat(repo["created_at"][:10])).days)
            momentum, basis = stars / age, "lifetime"
        first = con.execute("SELECT first_seen FROM items WHERE id=?", (key,)).fetchone()
        con.execute(
            """INSERT OR REPLACE INTO items(id, kind, name, full_name, url, description, topics, language, license,
               stars, forks, open_issues, created_at, pushed_at, archived, homepage, category, install, env_vars, risks,
               skill_paths, registry_name, stars_7d, momentum, momentum_basis, sources, first_seen, updated_at,
               skills, default_branch, description_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, r["kind"], repo["name"], repo["full_name"], repo["html_url"], repo.get("description"),
             json.dumps(repo.get("topics") or []), repo.get("language"),
             (repo.get("license") or {}).get("spdx_id") if repo.get("license") else None,
             stars, repo.get("forks_count"), repo.get("open_issues_count"), repo.get("created_at"), repo.get("pushed_at"),
             int(bool(repo.get("archived"))), repo.get("homepage"), categorize(repo), json.dumps(r["install"]),
             json.dumps(r["env"]), json.dumps(r["risks"]), json.dumps(r["paths"]),
             (r["reg"] or {}).get("name"), s7, round(momentum, 2), basis, json.dumps(r["sources"]),
             first["first_seen"] if first else now, now,
             json.dumps(r["skills"]), repo.get("default_branch"), translated(con, repo.get("description"))))
    # drop items that disappeared from every source for 30 days
    con.execute("DELETE FROM items WHERE updated_at < ?", ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat(),))
    counts = {k: v for k, v in con.execute("SELECT kind, COUNT(*) FROM items GROUP BY kind")}
    db.set_meta(con, "last_run", {"at": now, "seconds": round(time.time() - t0), "counts": counts})
    db.set_meta(con, "progress", {"running": False, "step": 4, "steps": 4, "label": "Selesai", "at": now})
    con.commit()
    print(f"selesai dalam {round(time.time() - t0)} detik: {counts}", flush=True)


if __name__ == "__main__":
    main()
