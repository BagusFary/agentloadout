"""agentloadout command line.

  agentloadout ui [--host H --port P]   start the local web catalog
  agentloadout refresh                  back up, then fetch the catalog from GitHub
  agentloadout mcp                      run the MCP server (stdio) — used by agent harnesses
  agentloadout setup / uninstall        register / remove the MCP server in your harnesses
  agentloadout approve <plan> / reject <plan> / plans
  agentloadout backup | restore [file] | check-updates | where
"""
import argparse
import sys


def cmd_refresh(_a):
    from . import backup, collector
    backup.main_args([])
    collector.main()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agentloadout", description="Agent Loadout: MCP servers, skills and apps for any agent harness.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ui", help="start the local web catalog")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    sub.add_parser("refresh", help="back up and fetch the catalog now")
    sub.add_parser("mcp", help="run the MCP server over stdio")
    sub.add_parser("backup", help="back up the catalog database")
    p = sub.add_parser("restore", help="restore the catalog from a backup")
    p.add_argument("file", nargs="?")
    sub.add_parser("check-updates", help="check newer versions of installed items")
    sub.add_parser("where", help="show data, config and backup folders")
    a = ap.parse_args(argv)

    if a.cmd == "ui":
        from .web import server
        server.main(a.host, a.port)
    elif a.cmd == "refresh":
        cmd_refresh(a)
    elif a.cmd == "mcp":
        from . import mcp_server
        mcp_server.main()
    elif a.cmd == "backup":
        from . import backup
        backup.main_args([])
    elif a.cmd == "restore":
        from . import restore
        sys.argv = ["restore"] + ([a.file] if a.file else ["--list"])
        restore.main()
    elif a.cmd == "check-updates":
        from . import updates
        updates.main()
    elif a.cmd == "where":
        from . import config, paths
        print(f"data    : {paths.home()}\ncatalog : {paths.db_path()}\nbackups : {paths.backup_dir()}\nconfig  : {paths.config_file()}")
        print(f"github token: {'set' if config.github_token() else 'not set (optional, raises API limit)'}")


if __name__ == "__main__":
    main()
