"""Packaging (H8): sdist and wheel carry no personal paths or tailnet addresses."""
import re
import shutil
import subprocess
import tarfile
import zipfile

import pytest

from conftest import ROOT

from agentloadout import __version__

# built from pieces so this file does not match its own search
PATTERNS = [re.compile(p) for p in ("/" + "home/", r"ts\." + "net", "tail" + "[0-9a-f]{6}")]


@pytest.mark.skipif(not shutil.which("uv"), reason="needs uv")
def test_sdist_and_wheel_are_clean(sb):
    out = sb.root / "dist"
    r = subprocess.run(["uv", "build", "--out-dir", str(out), str(ROOT)], env=sb.env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    sdist = next(out.glob(f"agentloadout-{__version__}.tar.gz"))
    wheel = next(out.glob(f"agentloadout-{__version__}-*.whl"))

    files = {}
    with tarfile.open(sdist) as t:
        for m in t.getmembers():
            if m.isfile():
                files[f"sdist:{m.name}"] = t.extractfile(m).read()
    with zipfile.ZipFile(wheel) as z:
        for n in z.namelist():
            files[f"wheel:{n}"] = z.read(n)

    names = [n.split("/", 1)[1] for n in files if n.startswith("sdist:")]
    assert not [n for n in names if n.startswith(("docs/", "scripts/", "TODO", "data/", "dist/"))], names
    hits = [(n, p.pattern) for n, b in files.items() for p in PATTERNS if p.search(b.decode("utf-8", "replace"))]
    assert hits == []
