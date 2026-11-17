"""End-to-end browser test of UI-01 "Salin untuk agen" in headless Chromium, in a throwaway HOME.

Run: uv run python tests/e2e_ui.py /path/to/katalog.db --chrome /path/to/chrome-headless-shell
         [--axe /path/to/axe.min.js] [--baseline origin/main] [--shots DIR]

- A copy of the catalog is used (the file itself is never changed). The web UI runs on a random free port
  with a dummy OpenBot webhook (never called) and a test password; proxy variables point at a closed port,
  and Chromium resolves every host except 127.0.0.1 to nothing, so nothing reaches GitHub or the internet.
- Chromium is driven over the DevTools pipe (no extra Python package, no open debugging port); clicks and
  keys are real input events, so copying runs with user activation.
- With --axe, critical/serious axe violations are compared with the same pages of --baseline (the code
  before this feature, from `git archive`): there must be no new rule and no rule with more nodes.
- With --shots, screenshots of the states go to DIR (for the design review).
Exit code 0 when every check passes.
"""
import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser()
ap.add_argument("db")
ap.add_argument("--chrome", required=True)
ap.add_argument("--axe")
ap.add_argument("--baseline", default="origin/main")
ap.add_argument("--shots")
A = ap.parse_args()

T = Path(tempfile.mkdtemp(prefix="al-e2e-ui-"))
PW = "uji-e2e-password-123"  # throwaway HOME only
FAILS = []


def check(name, ok, info=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info and not ok else ""))
    if not ok:
        FAILS.append(name)


def free_port():
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p not in (8787, 8788):
            return p


def sandbox(name, src_root):
    home = T / name
    al = home / "al"
    al.mkdir(parents=True)
    shutil.copy(A.db, al / "catalog.db")
    (al / "hook.secret").write_text("dummy-not-a-real-secret\n")
    (al / "config.toml").write_text('[integrations.openbot_webhook]\nurl = "http://127.0.0.1:9/hook"\n'
                                    f'secret_file = "{al / "hook.secret"}"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTLOADOUT_", "GITHUB_TOKEN", "OPENBOT_"))}
    env.update(HOME=str(home), AGENTLOADOUT_HOME=str(al), AGENTLOADOUT_DB=str(al / "catalog.db"),
               XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
               HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost",
               PYTHONPATH=str(src_root))
    subprocess.run([sys.executable, "-c", "from agentloadout import web_password as w; w.set_password(%r, None)" % PW],
                   env=env, check=True, capture_output=True)
    con = sqlite3.connect(al / "catalog.db")
    con.execute("DELETE FROM installs")
    inst = con.execute("SELECT id FROM items WHERE kind='mcp' ORDER BY stars DESC LIMIT 1 OFFSET 3").fetchone()[0]
    con.execute("INSERT INTO installs(id, status, ref, enabled, updated_at) VALUES(?, 'installed', 'mcp:e2e', 1, '2026-11-17')",
                (inst,))
    con.commit()
    con.close()
    port = free_port()
    log = open(home / "ui.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "agentloadout.cli", "ui", "--port", str(port)], env=env,
                            stdout=log, stderr=subprocess.STDOUT, cwd=str(home))
    for _ in range(100):
        time.sleep(0.1)
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            pass
    return proc, port, inst


class Chrome:
    """Minimal DevTools-protocol client over --remote-debugging-pipe."""

    def __init__(self):
        self.udd = tempfile.mkdtemp(prefix="chrome-", dir=T)
        r_in, self.w_in = os.pipe()
        self.r_out, w_out = os.pipe()

        def pre():
            os.dup2(r_in, 3)
            os.dup2(w_out, 4)
        self.p = subprocess.Popen([A.chrome, "--headless", "--remote-debugging-pipe", f"--user-data-dir={self.udd}",
                                   "--no-first-run", "--no-sandbox", "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
                                   "about:blank"], pass_fds=(3, 4), preexec_fn=pre,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(r_in)
        os.close(w_out)
        self.buf, self.n = b"", 0
        tid = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.sid = self.call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        self.s("Page.enable")
        self.s("Runtime.enable")
        self.size(1280, 900)

    def call(self, method, params=None, sid=None):
        self.n += 1
        m = {"id": self.n, "method": method, "params": params or {}}
        if sid:
            m["sessionId"] = sid
        os.write(self.w_in, json.dumps(m).encode() + b"\0")
        while True:
            while b"\0" not in self.buf:
                chunk = os.read(self.r_out, 1 << 20)
                if not chunk:
                    raise RuntimeError("chrome closed the pipe")
                self.buf += chunk
            msg, self.buf = self.buf.split(b"\0", 1)
            d = json.loads(msg)
            if d.get("id") == self.n:
                if "error" in d:
                    raise RuntimeError(f"{method}: {d['error']}")
                return d.get("result", {})

    def s(self, method, params=None):
        return self.call(method, params, self.sid)

    def size(self, w, h, mobile=False):
        self.s("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})

    def media(self, scheme=None, reduce=False):
        feats = [{"name": "prefers-color-scheme", "value": scheme or ""},
                 {"name": "prefers-reduced-motion", "value": "reduce" if reduce else ""}]
        self.s("Emulation.setEmulatedMedia", {"features": feats})

    def nav(self, url, ready="document.querySelectorAll('#grid .card:not(.sk)').length > 0"):
        self.s("Page.navigate", {"url": url})
        time.sleep(0.3)
        return self.wait(ready, 15)

    def ev(self, expr, await_=False):
        r = self.s("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": await_})
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", str(r))[:400])
        return r.get("result", {}).get("value")

    def wait(self, expr, timeout=6):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.ev(expr):
                    return True
            except RuntimeError:
                pass
            time.sleep(0.1)
        return False

    def center(self, sel):
        return self.ev(f"(()=>{{const e=document.querySelector({json.dumps(sel)});if(!e)return null;"
                       "e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})()")

    def click(self, sel):
        c = self.center(sel)
        if not c:
            raise RuntimeError(f"no element {sel}")
        time.sleep(0.15)
        c = self.center(sel)
        for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.s("Input.dispatchMouseEvent", {"type": typ, "x": c[0], "y": c[1], "button": "left", "clickCount": 1,
                                                "buttons": 1 if typ == "mousePressed" else 0})
        time.sleep(0.25)

    KEYS = {"Enter": ("Enter", 13, "\r"), "Space": (" ", 32, " "), "Escape": ("Escape", 27, ""), "Tab": ("Tab", 9, "")}

    def key(self, name, shift=False):
        key, vk, text = self.KEYS[name]
        base = {"key": key, "code": name, "windowsVirtualKeyCode": vk, "modifiers": 8 if shift else 0}
        self.s("Input.dispatchKeyEvent", {"type": "keyDown", **base, **({"text": text} if text else {})})
        self.s("Input.dispatchKeyEvent", {"type": "keyUp", **base})
        time.sleep(0.25)

    def shot(self, name):
        if not A.shots:
            return
        import base64
        Path(A.shots).mkdir(parents=True, exist_ok=True)
        data = self.s("Page.captureScreenshot", {"format": "png"})["data"]
        (Path(A.shots) / f"{name}.png").write_bytes(base64.b64decode(data))

    def close(self):
        if self.p.poll() is not None:
            return
        try:
            self.call("Browser.close")
        except Exception:
            pass
        try:
            self.p.wait(5)
        except Exception:
            self.p.kill()


def login(c, base):
    c.s("Page.navigate", {"url": base + "/login"})
    c.wait("!!document.querySelector('input[name=password]')", 5)
    c.ev(f"document.querySelector('input[name=password]').value = {json.dumps(PW)}; document.querySelector('form').submit(); true")
    time.sleep(1)
    return c.nav(base + "/")


def axe_run(c, label, inside=None):
    """critical/serious violations: {rule: nodes}; with `inside`, only nodes inside that element"""
    if not A.axe:
        return None
    c.media(reduce=True)  # no running animation may change the measured colours
    time.sleep(0.3)
    if not c.ev("!!window.axe"):
        c.ev(Path(A.axe).read_text())
    keep = f"(n => document.querySelector({json.dumps(inside)}).contains(document.querySelector(n.target[0])))" if inside else "(n => true)"
    res = c.ev("axe.run(document, {resultTypes: ['violations']}).then(r => r.violations"
               ".filter(v => v.impact === 'critical' || v.impact === 'serious')"
               f".flatMap(v => v.nodes.filter({keep}).map(n => [v.id, n.target.join(' '), n.html.slice(0, 160)])))", True)
    c.media()
    return res


def axe_count(nodes):
    out = {}
    for rule, _, _ in nodes:
        out[rule] = out.get(rule, 0) + 1
    return out


SHEET_OPEN = "!document.querySelector('#sheet').hidden"
MANUAL_OPEN = "!document.querySelector('#manual').hidden"
CLIP = "navigator.clipboard.readText()"
FAIL_COPY = ("navigator.clipboard.writeText = () => Promise.reject(new Error('blocked'));"
             "document.execCommand = () => false; true")


def item_text(c, sel_card):
    return c.ev(f"explainPrompt(state.items.find(x => x.id === document.querySelector({json.dumps(sel_card)}).dataset.id))")


def main():
    src = ROOT / "src"
    proc, port, installed_id = sandbox("new", src)
    base = f"http://127.0.0.1:{port}"
    c = Chrome()
    c.call("Browser.grantPermissions", {"origin": base, "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"]})
    procs = [proc]
    axe_new = {}
    try:
        check("page loads with cards (after login)", login(c, base))
        check("explain.js is served before app.js", c.ev("typeof explainPrompt === 'function' && typeof copyText === 'function'"))
        check("no copy button on 'Baru minggu ini' mini-cards (D3)", c.ev("document.querySelectorAll('#fresh [data-explain]').length === 0"))
        check("every card has one labelled copy button", c.ev(
            "[...document.querySelectorAll('#grid .card')].every(k => k.querySelectorAll('[data-explain]').length === 1 && "
            "k.querySelector('[data-explain]').getAttribute('aria-label') === 'Salin penjelasan untuk agen: ' + k.querySelector('h3').textContent)"))
        if A.axe:
            axe_new["catalog"] = axe_run(c, "catalog")

        # --- card button: copies, does not open the detail (H2), pill + toast, live region (H5)
        c.ev("navigator.clipboard.writeText('')", True)
        want = item_text(c, "#grid .card:nth-child(1)")
        c.click("#grid .card:nth-child(1) [data-explain]")
        check("card button does not open the detail", not c.ev(SHEET_OPEN))
        check("clipboard holds exactly the template", c.ev(CLIP, True) == want and want and want.count("\n") == 20)
        check("pill shows Tersalin", c.ev("(()=>{const b=document.querySelector('#grid .card:nth-child(1) [data-explain]');"
                                         "return b.classList.contains('done') && b.textContent === 'Tersalin'})()"))
        check("toast says 'Tersalin. Tempel ke agenmu.'", c.wait("document.querySelector('#toast').textContent === 'Tersalin. Tempel ke agenmu.'", 2))
        tree = c.s("Accessibility.getFullAXTree")["nodes"]
        st = [n for n in tree if n.get("role", {}).get("value") == "status" and not n.get("ignored")]
        live = [p for n in st for p in n.get("properties", []) if p["name"] == "live"]
        check("toast is a polite status live region in the AX tree", bool(st) and live and live[0]["value"]["value"] == "polite")
        c.shot("01-card-tersalin-terang")
        time.sleep(2.3)
        check("pill goes back after ~2 s", c.ev("(()=>{const b=document.querySelector('#grid .card:nth-child(1) [data-explain]');"
                                               "return !b.classList.contains('done') && !!b.querySelector('svg') && b.textContent === ''})()"))
        check("toast hidden again but still in the DOM", c.wait("!document.querySelector('#toast').classList.contains('show') && "
                                                               "!!document.querySelector('#toast[role=status]')", 2))

        # --- card from infinite scroll (#40)
        for _ in range(6):
            if c.ev("document.querySelectorAll('#grid .card').length >= 40"):
                break
            c.ev("document.querySelector('#sentinel').scrollIntoView()")
            time.sleep(0.6)
        want = item_text(c, "#grid .card:nth-child(40)")
        c.click("#grid .card:nth-child(40) [data-explain]")
        check("card #40 (after scroll): copies, no detail", not c.ev(SHEET_OPEN) and c.ev(CLIP, True) == want)
        c.click("#grid .card:nth-child(2) .desc")
        check("clicking elsewhere on a card still opens the detail", c.ev(SHEET_OPEN))
        c.key("Escape")
        c.wait("document.querySelector('#sheet').hidden", 2)

        # --- keyboard: Enter and Space on the focused button copy; tooltip on :focus-visible
        c.ev("window.scrollTo(0,0)")
        c.ev("document.querySelector('#q').focus()")
        for _ in range(60):
            c.key("Tab")
            if c.ev("document.activeElement.matches('#grid .card:nth-child(1) [data-explain]')"):
                break
        check("Tab reaches the first card button", c.ev("document.activeElement.matches('#grid .card:nth-child(1) [data-explain]')"))
        check("tooltip shows on keyboard focus", c.wait("getComputedStyle(document.activeElement, '::after').opacity === '1'", 1))
        check("focus outline is 2px accent", c.ev("getComputedStyle(document.activeElement).outlineWidth === '2px'"))
        c.shot("02-card-fokus-keyboard")
        c.ev("navigator.clipboard.writeText('')", True)
        c.key("Enter")
        check("Enter copies, detail stays closed", c.ev(CLIP, True) == item_text(c, "#grid .card:nth-child(1)") and not c.ev(SHEET_OPEN))
        c.ev("navigator.clipboard.writeText('')", True)
        time.sleep(0.3)
        c.key("Space")
        check("Space copies too", c.ev(CLIP, True) == item_text(c, "#grid .card:nth-child(1)") and not c.ev(SHEET_OPEN))
        time.sleep(2.2)
        # headless Chromium reports (hover: none), so the hover tooltip is checked in the style sheet
        check("tooltip rule for hover devices", c.ev(
            "[...document.styleSheets].some(s => [...s.cssRules].some(r => r.conditionText === '(hover: hover)' && "
            "[...r.cssRules].some(x => x.selectorText === '.ask:hover::after' && x.style.opacity === '1')))"))

        # --- failure: pill Gagal, manual dialog with the text selected, Esc closes only it, focus returns (H1, H4)
        c.ev(FAIL_COPY)
        want = item_text(c, "#grid .card:nth-child(3)")
        c.click("#grid .card:nth-child(3) [data-explain]")
        check("failure: pill shows Gagal", c.ev("document.querySelector('#grid .card:nth-child(3) [data-explain]').textContent === 'Gagal'"))
        check("failure: manual dialog opens, no false 'Tersalin' toast", c.ev(MANUAL_OPEN) and
              not c.ev("document.querySelector('#toast').textContent.startsWith('Tersalin')"))
        check("dialog textarea focused and fully selected", c.ev(
            "(()=>{const t=document.querySelector('#manual-text');return document.activeElement===t && t.readOnly && "
            f"t.selectionStart===0 && t.selectionEnd===t.value.length && t.value==={json.dumps(want)}}})()"))
        if A.axe:
            time.sleep(0.5)
            # the page behind a modal backdrop is dimmed on purpose; the new dialog itself must be clean
            inner = axe_run(c, "dialog", inside="#manual")
            print(f"axe dialog (inside #manual): {axe_count(inner)}")
            check("axe: no critical/serious violation inside the manual dialog", inner == [], inner)
        c.shot("04-gagal-salin-manual")
        for _ in range(4):
            c.key("Tab")
        check("Tab stays inside the dialog", c.ev("document.querySelector('#manual').contains(document.activeElement)"))
        c.key("Tab", shift=True)
        check("Shift+Tab stays inside the dialog", c.ev("document.querySelector('#manual').contains(document.activeElement)"))
        c.key("Escape")
        check("Esc closes the dialog", not c.ev(MANUAL_OPEN))
        check("focus back on the card button", c.ev("document.activeElement === document.querySelector('#grid .card:nth-child(3) [data-explain]')"))
        c.ev("location.reload()")
        c.nav(base + "/")

        # --- detail: section outside actions, preview equals the copied text (H14), label once (H13)
        c.click("#grid .card:nth-child(1) .desc")
        c.wait(SHEET_OPEN, 3)
        want = c.ev("explainPrompt(state.items.find(x => x.name === document.querySelector('#detail h2').textContent))")
        check("detail preview equals the copied text", c.ev("document.querySelector('.ask-sec pre').textContent") == want)
        check("'Salin untuk agen' exactly once in the detail", c.ev("document.querySelector('#detail').innerHTML.split('Salin untuk agen').length - 1") == 1)
        check("old button renamed 'Salin perintah pasang'", c.ev("document.querySelector('#b-noah')?.textContent") == "Salin perintah pasang")
        check("section title and note", c.ev("document.querySelector('.ask-sec h4').textContent === 'Tanya agen tentang repo ini' && "
                                            "document.querySelector('.ask-sec > .note').textContent.startsWith('Tempel ke agenmu (Claude Code, OpenBot')"))
        if A.axe:
            time.sleep(0.8)  # after the sheet animation, like the baseline
            axe_new["detail"] = axe_run(c, "detail")
        c.shot("05-detail-terang")
        c.ev("navigator.clipboard.writeText('')", True)
        c.click("#b-explain")
        check("detail button copies and shows Tersalin", c.ev(CLIP, True) == want and
              c.ev("document.querySelector('#b-explain').textContent === 'Tersalin'"))
        c.shot("06-detail-tersalin-terang")
        time.sleep(2.2)
        check("detail button back to 'Salin untuk agen'", c.ev("document.querySelector('#b-explain').textContent === 'Salin untuk agen'"))
        # failure from the detail: Esc closes only the dialog, the detail stays, focus back on #b-explain
        c.ev(FAIL_COPY)
        c.click("#b-explain")
        check("detail failure opens the dialog", c.ev(MANUAL_OPEN))
        c.key("Escape")
        check("Esc: dialog closed, detail still open (H4)", not c.ev(MANUAL_OPEN) and c.ev(SHEET_OPEN))
        check("focus back on #b-explain", c.ev("document.activeElement === document.querySelector('#b-explain')"))
        # older snippet button: no false success (H1)
        if c.ev("!!document.querySelector('[data-copy]')"):
            c.click("[data-copy]")
            check("snippet button: dialog instead of a false 'Tersalin'", c.ev(MANUAL_OPEN) and
                  c.ev("document.querySelector('[data-copy]').textContent === 'Salin'"))
            c.click("#manual [data-mclose].btn")
            check("'Selesai' closes the dialog, focus back on the snippet button", not c.ev(MANUAL_OPEN) and
                  c.ev("document.activeElement === document.querySelector('[data-copy]')"))
        c.key("Escape")
        check("second Esc closes the detail", c.wait("document.querySelector('#sheet').hidden", 2))
        c.ev("location.reload()")
        c.nav(base + "/")

        # --- installed item on an instance with the OpenBot webhook (H3)
        c.ev(f"openDetail({json.dumps(installed_id)})")
        c.wait(SHEET_OPEN, 3)
        check("installed item: webhook buttons, no #b-noah, but 'Tanya agen' is there",
              c.ev("!document.querySelector('#b-noah') && !!document.querySelector('#b-remove') && !!document.querySelector('#b-explain')"))
        c.ev("navigator.clipboard.writeText('')", True)
        c.click("#b-explain")
        check("installed item: copy works", c.ev(CLIP, True) == c.ev(f"explainPrompt(state.items.find(x => x.id === {json.dumps(installed_id)}))"))
        c.key("Escape")

        # --- themes: --ok-ink / --hi-ink in light, dark and auto + dark OS (H12)
        def tokens():
            return c.ev("(()=>{const s=getComputedStyle(document.documentElement);return [s.getPropertyValue('--ok-ink').trim(),"
                        "s.getPropertyValue('--hi-ink').trim()]})()")
        res = {}
        for theme, scheme in (("light", "light"), ("dark", "light"), ("auto", "dark"), ("auto", "light")):
            c.media(scheme)
            c.ev(f"document.documentElement.dataset.theme = {json.dumps(theme)}")
            res[(theme, scheme)] = tokens()
        check("tokens per theme", res == {("light", "light"): ["#1f7a36", "#c4261d"], ("dark", "light"): ["#30d158", "#ff6961"],
                                          ("auto", "dark"): ["#30d158", "#ff6961"], ("auto", "light"): ["#1f7a36", "#c4261d"]}, res)
        c.ev("document.documentElement.dataset.theme = 'dark'")
        c.media("dark")
        c.click("#grid .card:nth-child(1) [data-explain]")
        c.shot("07-card-tersalin-gelap")
        time.sleep(2.2)
        c.click("#grid .card:nth-child(1) .desc")
        c.wait(SHEET_OPEN, 3)
        time.sleep(0.6)
        c.click("#b-explain")
        c.shot("08-detail-tersalin-gelap")
        c.key("Escape")
        c.ev("document.documentElement.dataset.theme = 'light'")
        c.media("light")
        c.ev("localStorage.setItem('katalog-theme','light')")

        # --- layout: the button covers no badge, owner or title (H11), 1280 and 390 px
        OVERLAP = ("(()=>{const hit=(a,b)=>a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;let n=0;"
                   "for(const k of document.querySelectorAll('#grid .card')){const b=k.querySelector('[data-explain]').getBoundingClientRect();"
                   "for(const e of k.querySelectorAll('.meta .state')){if(hit(b,e.getBoundingClientRect()))n++}"
                   "for(const e of k.querySelectorAll('.meta .owner, h3')){const t=document.createRange();t.selectNodeContents(e);"
                   "for(const q of t.getClientRects()){if(hit(b,q))n++}}}return n})()")
        c.ev("location.reload()")
        c.nav(base + "/")
        check("1280 px: button overlaps no badge/owner/title text", c.ev(OVERLAP) == 0, c.ev(OVERLAP))
        c.size(390, 844, mobile=True)
        c.ev("location.reload()")
        c.nav(base + "/")
        check("390 px: button overlaps no badge/owner/title text", c.ev(OVERLAP) == 0, c.ev(OVERLAP))
        check("390 px: button is 36x36", c.ev("(()=>{const b=document.querySelector('#grid [data-explain]');return b.offsetWidth===36&&b.offsetHeight===36})()"))
        c.shot("09-hp-390")
        c.click("#grid .card:nth-child(1) [data-explain]")
        c.shot("10-hp-390-tersalin")
        time.sleep(2.2)
        c.size(1280, 900)

        # --- read-only public view (Funnel): copy works, install disabled, lock note (H3)
        c.s("Network.enable")
        c.s("Network.setExtraHTTPHeaders", {"headers": {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "203.0.113.9"}})
        check("public view loads after login", login(c, base))
        check("public view: actions not allowed", c.ev("state.meta.actions_allowed") is False)
        uninstalled = c.ev(f"state.items.find(x => x.id !== {json.dumps(installed_id)} && x.kind === 'mcp').id")
        c.ev(f"openDetail({json.dumps(uninstalled)})")
        c.wait(SHEET_OPEN, 3)
        time.sleep(0.5)
        check("public view: Pasang disabled, copy enabled, lock note",
              c.ev("document.querySelector('#b-install').disabled && !document.querySelector('#b-explain').disabled && "
                   "document.querySelector('.note.lock').textContent.endsWith('Menyalin tetap bisa.')"))
        c.ev("navigator.clipboard.writeText('')", True)
        c.click("#b-explain")
        check("public view: detail copy works", c.ev(CLIP, True) == c.ev(f"explainPrompt(state.items.find(x => x.id === {json.dumps(uninstalled)}))"))
        c.shot("11-publik-hanya-lihat")
        c.key("Escape")
        c.click("#grid .card:nth-child(1) [data-explain]")
        check("public view: card copy works", c.ev(CLIP, True) == item_text(c, "#grid .card:nth-child(1)"))
        c.s("Network.setExtraHTTPHeaders", {"headers": {}})
        c.close()

        # --- axe against the baseline (code before this feature)
        if A.axe:
            bdir = T / "base-src"
            bdir.mkdir()
            tar = subprocess.run(["git", "-C", str(ROOT), "archive", A.baseline, "src"], capture_output=True, check=True).stdout
            subprocess.run(["tar", "-x", "-C", str(bdir)], input=tar, check=True)
            bproc, bport, _ = sandbox("base", bdir / "src")
            procs.append(bproc)
            b = Chrome()
            bbase = f"http://127.0.0.1:{bport}"
            check("baseline really is the old code", login(b, bbase) and not b.ev("typeof explainPrompt === 'function'"))
            axe_old = {"catalog": axe_run(b, "catalog")}
            b.click("#grid .card:nth-child(1) .desc")
            b.wait(SHEET_OPEN, 3)
            time.sleep(0.6)
            axe_old["detail"] = axe_run(b, "detail")
            b.close()
            for page in ("catalog", "detail"):
                new, old = axe_new[page], axe_old[page]
                cn, co = axe_count(new), axe_count(old)
                worse = {r: n for r, n in cn.items() if r not in co}  # no NEW kind of violation
                print(f"axe {page}: new {cn} | baseline {co}")
                if worse:  # show the nodes that differ (the copy button itself is cut from the snippet)
                    strip = lambda h: h.split('<button class="ask"')[0]
                    oset = [(r, strip(h)) for r, _, h in old]
                    for r, t, h in new:
                        if (r, strip(h)) in oset:
                            oset.remove((r, strip(h)))
                        else:
                            print("   only new:", r, t, strip(h)[:120])
                    for r, h in oset:
                        print("   only baseline:", r, h[:120])
                check(f"axe {page}: no new kind of critical/serious violation", not worse, worse)
    finally:
        try:
            c.close()
        except Exception:
            pass
        for p in procs:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(T, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
