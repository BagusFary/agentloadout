# Releasing Agent Loadout

1. All tests pass (`uv run pytest`, `tests/e2e_mcp.py`), review and QA approved.
2. `pyproject.toml` and `src/agentloadout/__init__.py` carry the new version.
3. **`CHANGELOG.md` has a `## [X.Y.Z] - <date>` section for the release.** `agentloadout upgrade` shows
   it to users, read from the release commit.
4. The README install command points to the new tag.
5. Merge to `main`, then create the tag `vX.Y.Z` on the merge commit (owner's approval needed) and push it.
   Never move a published tag: `agentloadout upgrade` refuses a tag that changed since it was checked.
6. Backlog: signed tags.
