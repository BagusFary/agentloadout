"""End-to-end test: real MCP client <-> agentloadout MCP server over stdio, in a throwaway HOME.

Run: uv run python tests/e2e_mcp.py /path/to/katalog.db   (a copy is used; the file itself is never changed)

Approvals need the human's PIN, so this script approves through the Python API (plans.approve), exactly
as a human approval would record it. There is no environment switch that lets a process approve.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SRC_DB = Path(sys.argv[1])
T = Path(tempfile.mkdtemp(prefix="al-e2e-"))
(T / ".cursor").mkdir()
(T / ".codex").mkdir()
(T / ".cursor" / "mcp.json").write_text('{"mcpServers": {"existing": {"command": "x"}}}\n')
ENV = {**os.environ, "HOME": str(T), "AGENTLOADOUT_HOME": str(T / "al"), "AGENTLOADOUT_DB": str(T / "al" / "catalog.db"),
       "XDG_CONFIG_HOME": str(T / ".config"), "XDG_DATA_HOME": str(T / ".local/share")}
for _k in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "GITHUB_TOKEN", "GITHUB_TOKEN_FILE", "AGENTLOADOUT_PORT"):
    ENV.pop(_k, None)
(T / "al").mkdir()
shutil.copy(SRC_DB, T / "al" / "catalog.db")


def cli(*args):
    return subprocess.run([sys.executable, "-m", "agentloadout.cli", *args], env=ENV, capture_output=True, text=True, timeout=120)


def approve(plan_id):
    """Test-only stand-in for the human: approve through the Python API in the throwaway HOME."""
    code = f"from agentloadout import plans; print(plans.approve({plan_id!r}, 'pin:test')['status'])"
    return subprocess.run([sys.executable, "-c", code], env=ENV, capture_output=True, text=True, timeout=60)


def data(res):
    sc = getattr(res, "structured_content", None)
    if sc is not None:
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "agentloadout.cli", "mcp"], env=ENV)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", len(tools))

            res = data(await s.call_tool("search_catalog", {"query": "context7", "kind": "mcp", "limit": 3}))
            print("search:", res["catalog"], "|", [x["id"] for x in res["results"]])

            h = data(await s.call_tool("list_harnesses", {}))
            print("harness terdeteksi:", [x["id"] for x in h["harnesses"] if x["detected"]])

            plan = data(await s.call_tool("plan_install", {"item_id": "upstash/context7", "targets": ["cursor", "codex", "claude-code"]}))
            assert "error" not in plan, plan
            pid = plan["id"]
            print("rencana:", pid, plan["status"], "| spec:", plan["spec"]["command"], plan["spec"]["args"], "| peringatan:", plan["warnings"])

            denied = data(await s.call_tool("apply_plan", {"plan_id": pid}))
            print("apply TANPA persetujuan ->", "DITOLAK" if "error" in denied else "LOLOS (BUG!)", "|", denied.get("error", "")[:70])

            out = cli("approve", pid)
            print("cli approve tanpa TTY/PIN ->", "DITOLAK" if out.returncode == 2 else "LOLOS (BUG!)")
            out = approve(pid)
            print("approve (API, pengganti manusia) ->", out.stdout.strip() or out.stderr)

            done = data(await s.call_tool("apply_plan", {"plan_id": pid}))
            print("apply ->", done["status"], [(x["harness"], x["ok"], x.get("error", "")[:60]) for x in done["results"]])

            cur = json.loads((T / ".cursor" / "mcp.json").read_text())["mcpServers"]
            print("cursor mcp.json:", sorted(cur), "| context7 args:", cur.get("context7", {}).get("args"))
            print("codex config.toml:\n" + (T / ".codex" / "config.toml").read_text().strip())
            inst = data(await s.call_tool("list_installed", {}))
            print("list_installed:", [(x["item_id"], x["harness"], x["version"]) for x in inst["installed"]], "| harness_mcp:", inst["harness_mcp"])

            rm = data(await s.call_tool("plan_remove", {"item_id": "upstash/context7", "targets": ["cursor", "codex", "claude-code"]}))
            approve(rm["id"])
            rmd = data(await s.call_tool("apply_plan", {"plan_id": rm["id"]}))
            print("remove ->", rmd["status"], "| cursor sekarang:", sorted(json.loads((T / ".cursor/mcp.json").read_text())["mcpServers"]))

            sk = data(await s.call_tool("plan_install", {"item_id": "anthropics/skills", "targets": ["claude-code", "codex"], "skill_path": "skills/frontend-design"}))
            print("rencana skill:", sk.get("id"), [c["target"] for c in sk.get("changes", [])])
            rej = data(await s.call_tool("apply_plan", {"plan_id": sk["id"]}))
            print("apply skill tanpa persetujuan ->", "DITOLAK" if "error" in rej else "LOLOS (BUG!)")
            approve(sk["id"])
            skd = data(await s.call_tool("apply_plan", {"plan_id": sk["id"]}))
            print("apply skill ->", skd["status"], [os.path.exists(x.get("path", "")) for x in skd["results"]])

            insp = data(await s.call_tool("inspect_repo", {"item_id": "anthropics/skills", "skill_path": "skills/frontend-design"}))
            print("inspect_repo:", insp.get("commit"), insp.get("files"), "file |", insp.get("summary"))


try:
    asyncio.run(main())
finally:
    shutil.rmtree(T, ignore_errors=True)
