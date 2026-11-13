"""Indonesian translations for catalog item descriptions, cached by text hash.

No third-party translation service: an agent (Noah) translates exported texts.
  python3 translate.py --apply                 # copy cached translations into items (also run by update.sh)
  python3 translate.py --export out.json [--limit N]   # texts without a translation: [{"hash", "text"}]
  python3 translate.py --import in.json        # [{"hash", "id"}] -> cache + items
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector"))
import db  # noqa: E402


def h(text):
    return hashlib.sha1(text.strip().encode()).hexdigest()


def apply(con):
    n = 0
    for r in con.execute("SELECT id, description FROM items WHERE description IS NOT NULL AND description != ''").fetchall():
        t = con.execute("SELECT text FROM translations WHERE src_hash=? AND lang='id'", (h(r["description"]),)).fetchone()
        if t:
            con.execute("UPDATE items SET description_id=? WHERE id=?", (t["text"], r["id"]))
            n += 1
    con.commit()
    return n


def export(con, path, limit):
    seen, out = set(), []
    for r in con.execute("SELECT description FROM items WHERE description IS NOT NULL AND description != '' ORDER BY momentum DESC"):
        text = r["description"].strip()
        key = h(text)
        if key in seen or con.execute("SELECT 1 FROM translations WHERE src_hash=?", (key,)).fetchone():
            continue
        seen.add(key)
        out.append({"hash": key, "text": text[:600]})
        if len(out) >= limit:
            break
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=0)
    return len(out)


def import_(con, path):
    rows = json.load(open(path))
    n = 0
    for r in rows:
        if r.get("hash") and isinstance(r.get("id"), str) and r["id"].strip():
            con.execute("INSERT OR REPLACE INTO translations(src_hash, lang, text) VALUES(?,?,?)", (r["hash"], "id", r["id"].strip()[:800]))
            n += 1
    con.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export")
    ap.add_argument("--import", dest="imp")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=5000)
    a = ap.parse_args()
    con = db.connect()
    if a.export:
        print(f"diekspor: {export(con, a.export, a.limit)} teks belum diterjemahkan")
    if a.imp:
        print(f"diimpor: {import_(con, a.imp)} terjemahan")
    if a.apply or a.imp or not (a.export or a.imp):
        print(f"terjemahan diterapkan ke {apply(con)} item")


if __name__ == "__main__":
    main()
