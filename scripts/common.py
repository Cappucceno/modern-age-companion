"""Helpers shared by the converters: PDF text access, deterministic ids, pack writers and journal/roll table documents."""
import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from pathlib import Path

SYSTEM_STATS = {"coreVersion": "14", "systemId": "age-system", "systemVersion": "5.0.0"}
ABILITIES = {
    "Accuracy": "acc", "Communication": "comm", "Constitution": "cons", "Dexterity": "dex", "Fighting": "fight",
    "Intelligence": "int", "Perception": "per", "Strength": "str", "Willpower": "will",
}
ICON = "systems/age-system/resources/imgs/item-icon/"
ACTOR_ICON = "icons/svg/mystery-man.svg"


# --------------------------------------------------------------------------- PDF text helpers

def pdftotext(pdf, first, last, *args):
    out = subprocess.run(["pdftotext", *args, "-f", str(first), "-l", str(last), str(pdf), "-"],
                         check=True, capture_output=True, text=True).stdout
    return out.split("\f")[: last - first + 1]


FOOTER = re.compile(r"^(Chapter \d+( - |: ).*|\d{2,3}|Chapter \d+)$")


def clean_lines(text):
    lines = [l.rstrip() for l in text.splitlines()]
    return [l for l in lines if not FOOTER.match(l.strip())]


def column_pages(pdf, first, last, split=300):
    """Text of each page, left column then right column (reading order for two-column prose pages)."""
    left = pdftotext(pdf, first, last, "-x", "0", "-y", "0", "-W", str(split), "-H", "783")
    right = pdftotext(pdf, first, last, "-x", str(split), "-y", "0", "-W", "303", "-H", "783")
    return [clean_lines(l) + [""] + clean_lines(r) for l, r in zip(left, right)]


def full_pages(pdf, first, last):
    return [clean_lines(p) for p in pdftotext(pdf, first, last)]


class Words:
    """Vocabulary of the book, to decide whether a line-end hyphen is a real hyphen."""
    def __init__(self, text):
        self.known = set(re.findall(r"\b[a-z]{3,}\b", text))

    def join(self, left, right):
        if left.endswith("-") and right[:1].islower():
            stem = left[:-1]
            first = right.split()[0]
            word = re.split(r"[^A-Za-z]", stem)[-1] + re.match(r"[A-Za-z]*", first).group(0)
            if word.lower() in self.known:
                return stem + right
            return left + right
        return left + " " + right


def paragraph(lines, words):
    out = ""
    for l in lines:
        l = l.strip()
        if l:
            out = l if not out else words.join(out, l)
    out = re.sub(r"\s{2,}", " ", out)
    # a hyphen left at a line end that the layout turned into "dimen- sional": join when the word is known
    return re.sub(r"([A-Za-z]+)- ([a-z]+)", lambda m: m.group(1) + m.group(2) if (m.group(1) + m.group(2)).lower() in words.known else m.group(0), out)


def esc(s):
    return html.escape(s, quote=False)


def p(s):
    return f"<p>{esc(s)}</p>"


# --------------------------------------------------------------------------- document builders

def make_id(pack, name):
    return hashlib.sha1(f"{pack}:{name}".encode()).hexdigest()[:16]


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


class Pack:
    def __init__(self, name, kind="Item"):
        self.name, self.kind = name, kind
        self.docs, self.folders = [], {}

    def folder(self, name, sort):
        if name not in self.folders:
            fid = make_id(self.name + "-folder", name)
            self.folders[name] = {
                "name": name, "type": self.kind, "folder": None, "sorting": "a", "sort": sort, "color": None,
                "description": "", "flags": {}, "_id": fid, "_key": f"!folders!{fid}", "_stats": SYSTEM_STATS,
            }
        return self.folders[name]["_id"]

    def item(self, name, type_, system, img, folder=None, sort=0, uid=None):
        did = make_id(self.name, uid or name)
        self.docs.append({
            "name": name, "_id": did, "_key": f"!items!{did}", "folder": self.folder(folder, 100000 * (len(self.folders) + 1)) if folder else None,
            "sort": sort, "flags": {}, "effects": [], "_stats": SYSTEM_STATS, "type": type_, "img": ICON + img,
            "system": system, "ownership": {"default": 0},
        })

    def actor(self, name, type_, system, folder=None):
        did = make_id(self.name, name)
        self.docs.append({
            "name": name, "_id": did, "_key": f"!actors!{did}", "folder": self.folder(folder, 100000 * (len(self.folders) + 1)) if folder else None,
            "sort": 0, "flags": {}, "effects": [], "_stats": SYSTEM_STATS, "type": type_, "img": ACTOR_ICON,
            "system": system, "prototypeToken": {"name": name, "actorLink": True}, "ownership": {"default": 0},
        })

    def write(self, root):
        d = Path(root) / self.name
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.json"):
            f.unlink()
        for f in self.folders.values():
            (d / f"_folder-{slug(f['name'])}.json").write_text(json.dumps(f, indent=2, ensure_ascii=False) + "\n")
        seen = set()
        for doc in self.docs:
            s = slug(doc["name"])
            while s in seen:
                s += "-2"
            seen.add(s)
            (d / f"{s}.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"{self.name}: {len(self.docs)} documents, {len(self.folders)} folders")



def num(s):
    s = s.replace("–", "-").replace("−", "-").lstrip("+")
    return int(s) if re.fullmatch(r"-?\d+", s) else 0



def modifier(kind, formula):
    mid = make_id("modifier", kind + str(formula))
    return mid, {"type": kind, "formula": str(formula), "flavor": "", "isActive": True, "valid": True,
                 "conditions": {}, "ftype": "", "key": mid}



def layout_columns(pdf, first, last, top=None):
    """Layout-mode text of each page, left column then right column; `top` maps page -> y (points) below which to read."""
    out = []
    for pg in range(first, last + 1):
        y = (top or {}).get(pg, 0)
        cols = []
        for x, w in ((0, 300), (300, 303)):
            t = pdftotext(pdf, pg, pg, "-layout", "-x", str(x), "-y", str(y), "-W", str(w), "-H", str(783 - y))[0]
            cols += clean_lines(t)
        out += cols
    return out



def indent(l):
    return len(l) - len(l.lstrip())



def parse_ranges(rows):
    """[(range text, result)] -> [(lo, hi, result)]"""
    out = []
    for r, text in rows:
        parts = [int(x) for x in re.split(r"[–\-]", r)]
        out.append((parts[0], parts[-1], text))
    return out



def table_doc(pack, name, formula, desc, rows, folder):
    tid = make_id(pack.name, name)
    results = []
    for lo, hi, text in sorted(rows):
        rid = make_id(tid, f"{lo}-{hi}-{text}")
        results.append({"_id": rid, "type": "text", "name": text, "description": "", "img": "icons/svg/d20-black.svg",
                        "weight": hi - lo + 1, "range": [lo, hi], "drawn": False, "flags": {}, "_stats": SYSTEM_STATS,
                        "_key": f"!tables.results!{tid}.{rid}"})
    pack.docs.append({"name": name, "img": "icons/svg/d20-grey.svg", "description": desc, "results": results,
                      "formula": formula, "replacement": True, "displayRoll": True, "flags": {},
                      "folder": pack.folder(folder, 100000 * (len(pack.folders) + 1)), "sort": 0,
                      "ownership": {"default": 0}, "_stats": SYSTEM_STATS, "_id": tid, "_key": f"!tables!{tid}"})



def journal_doc(pack, name, pages):
    jid = make_id(pack.name, name)
    out, seen = [], {}
    for n, (title, html_) in enumerate(pages, 1):
        seen[title] = seen.get(title, 0) + 1
        if seen[title] > 1:
            title = f"{title} ({seen[title]})"   # page ids come from the title, so they must be unique within a journal
        pid = make_id(jid, title)
        out.append({"_id": pid, "name": title, "type": "text", "title": {"show": True, "level": 1},
                    "text": {"format": 1, "content": html_}, "sort": n * 100000, "ownership": {"default": -1}, "flags": {},
                    "_key": f"!journal.pages!{jid}.{pid}", "_stats": SYSTEM_STATS})
    pack.docs.append({"name": name, "pages": out, "flags": {}, "folder": None, "sort": 0, "ownership": {"default": 0},
                      "_id": jid, "_key": f"!journal!{jid}", "_stats": SYSTEM_STATS})


def table_html(headers, rows):
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def li(label, text):
    return f"<li><strong>{label}:</strong> {esc(text)}</li>" if text else ""



def crop_lines(pdf, page, side):
    x, w = (0, 300) if side == "left" else (300, 303)
    return pdftotext(pdf, page, page, "-layout", "-x", str(x), "-y", "0", "-W", str(w), "-H", "783")[0].splitlines()


