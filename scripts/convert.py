#!/usr/bin/env python3
"""Convert the Modern AGE Companion PDF into Foundry pack sources for the AGE System.

Usage: scripts/convert.py PATH/TO/Modern_AGE_Companion.pdf [--out packs/_source] [--only talents,stunts,...]

The PDF is read with `pdftotext` and `pdftohtml` (poppler-utils) and is never stored in the repository.
Output is one JSON per document in packs/_source/<pack>/, plus `_folder-*.json` for folders.
Ids are deterministic (hash of pack + name), so reruns produce stable diffs.

Page numbers below are PDF pages (printed page number + 1).
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blocks import blocks, entries, paras, text  # noqa: E402
import prose  # noqa: E402
from blocks import book  # noqa: E402
from prose import render  # noqa: E402
from common import (ABILITIES, Pack, crop_lines, esc, journal_doc, li, make_id, modifier, num, p, parse_ranges, pdftotext,  # noqa: E402
                    table_doc, table_html)

ABL_PATTERN = "|".join(ABILITIES)


def squash(s):
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- talents and specializations (p22-33)

DEGREES = ("Novice", "Expert", "Master", "Grandmaster", "Apex")
DEGREE = re.compile(r"^(" + "|".join(DEGREES) + r"):\s*(.*)$")
REQ = re.compile(r"^Requirements?:\s*(.*)$")


def parse_talent(bl):
    """(requirement, description, {degree: [texts]}) from the blocks under a talent heading."""
    req, desc, deg, key = "", [], {d: [] for d in DEGREES}, None
    for b in bl:
        if b[0] not in ("p", "li"):
            continue
        t = text(b[1])
        m = DEGREE.match(t)
        if m:
            key = m.group(1)
            deg[key].append(m.group(2))
        elif REQ.match(t) and not req:
            req = REQ.match(t).group(1)
        elif key is not None:
            deg[key].append(t)      # a degree can run over several paragraphs
        else:
            desc.append(t)
    return req, desc, deg


def talent_system(kind, req, desc, deg, extra=""):
    system = {"longDesc": "".join(p(x) for x in desc) + extra, "shortDesc": "", "requirement": req, "type": kind, "degree": 0, "activate": True}
    for i, d in enumerate(DEGREES):
        system[f"degree{i}"] = {"desc": "".join(p(x) for x in deg[d]), "isActive": True}
    return system


def build_talents(pdf):
    pack = Pack("talents")
    bl = blocks(pdf, 22, 33)
    ents = entries(bl, 3)
    # Grandmaster and Apex degrees for talents of the Basic Rulebook (Existing Talents section)
    for title, parent, body in ents:
        if parent == "Existing Talents":
            _, desc, deg = parse_talent(body)
            note = "Grandmaster and Apex degrees for the talent of the same name in the Modern AGE Basic Rulebook."
            pack.item(title, "talent", talent_system("talent", "", desc + [note], deg), "skills.svg", folder="Existing Talents")
        elif parent == "New Talents":
            req, desc, deg = parse_talent(body)
            pack.item(title, "talent", talent_system("talent", req, desc, deg), "skills.svg", folder="Talents")
    # specializations: an H2 per specialization (after 'New Specializations'), with an Eras H3 and a '<Name> Talent' H3
    started = False
    for title, _, body in entries(bl, 2):
        if title == "New Specializations":
            started = True
            continue
        if not started or not title:
            continue
        subs = {t: b for t, _, b in entries(body, 3)}
        flavor = paras([b for b in body[: next((i for i, b in enumerate(body) if b[0] == "h"), len(body))]])
        req, desc, deg = parse_talent(subs[f"{title} Talent"])
        eras = paras(subs["Eras"]) if "Eras" in subs else []
        extra = "<h3>Eras</h3>" + "".join(p(x) for x in eras) if eras else ""
        pack.item(title, "talent", talent_system("spec", req, flavor + desc, deg, extra), "skills.svg", folder="Specializations")
    return pack


# --------------------------------------------------------------------------- stunts (p46, p58-61, p73-74)

COST = re.compile(r"^(\d+)(?:[–-](\d+)|(\+))?$")
NAME = re.compile(r"^[A-Z“][^:]{1,48}: ")
# (PDF page, table heading override) -> stunt type; tables are found by their 'SP Cost ... Stunt' header
STUNT_PAGES = {46: "Battle Map", 58: "General", 59: "Environmental", 60: "Environmental", 61: "Thematic", 73: "Intensity", 74: "Intensity"}


def stunt_tables(pdf, page):
    """[(heading, [(cost, setting, text)])] for each 'SP Cost / Stunt' table on a page, read from the layout text."""
    lines = pdftotext(pdf, page, page, "-layout")[0].splitlines()
    out, i = [], 0
    while i < len(lines):
        if re.match(r"^\s*SP(\s*Cost\b|\s{2,}Stunt\s*$)", lines[i]) or lines[i].strip() == "SP":
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            heading = lines[j].strip() if j >= 0 else ""
            themed = "Genre/Setting" in lines[i]
            rows = []
            k = i + 1
            if lines[i].strip() == "SP" or lines[i].strip().startswith("SP") and "Cost" not in lines[i]:       # the header wraps onto a second line ("Cost")
                k += 1
            while k < len(lines) and lines[k].strip() and not lines[k].strip().startswith("Chapter "):
                rows.append(lines[k])
                k += 1
            out.append((heading, themed, rows))
            i = k
        i += 1
    return out


def parse_stunt_rows(rows, themed):
    entries_, cur = [], None
    starts = [len(r) - len(r.lstrip()) for r in rows if NAME.match(r.lstrip()) or re.match(r"^\s*\S+\s{2,}.*", r)]
    # the text column starts where the first stunt name starts
    col = None
    for r in rows:
        m = re.match(r"^(\s*(?:\S+\s{2,})?(?:[A-Z][A-Za-z/ ]+?\s{2,})?)([A-Z“][^:]{1,48}: .*)$", r)
        if m and (col is None or len(m.group(1)) >= 6):
            col = len(m.group(1)) if col is None else col
            break
    for r in rows:
        left, right = r[:col].strip(), r[col:].strip()
        if not right and left:
            right = ""
        new = NAME.match(right) and (cur is None or re.search(r"[.)”]$", cur["text"][-1]) and not cur["text"][-1].endswith("(General"))
        if new:
            cur = {"cost": "", "setting": "", "text": [right]}
            entries_.append(cur)
        elif cur is not None and right:
            cur["text"].append(right)
        parts = re.split(r"\s{2,}", left) if left else []
        for part in parts:
            if COST.match(part) and not cur["cost"]:
                cur["cost"] = part
            elif part:
                cur["setting"] = (cur["setting"] + " " + part).strip()
    return entries_


def build_stunts(pdf):
    pack = Pack("stunts")
    for page, kind in STUNT_PAGES.items():
        for heading, themed, rows in stunt_tables(pdf, page):
            for e in parse_stunt_rows(rows, themed):
                full = squash(" ".join(e["text"]))
                name, desc = full.split(": ", 1)
                m = COST.match(e["cost"])
                assert m, f"no SP cost for {name!r} (page {page})"
                lo = int(m.group(1))
                hi = int(m.group(2)) if m.group(2) else lo
                plus = bool(m.group(3))
                if themed:
                    folder, desc = "Thematic Stunts", f"<em>{esc(e['setting'])}.</em> " + esc(desc)
                elif kind == "Environmental":
                    folder, desc = "Environmental Stunts", esc(desc)
                elif kind == "General":
                    folder, desc = "General Stunts", esc(desc)
                elif kind == "Intensity":
                    folder, desc = "Intensity Stunts", esc(desc)
                else:
                    folder, desc = "Battle Map Stunts", esc(desc)
                if kind == "Environmental":
                    desc = f"<em>{esc(heading)}.</em> " + desc
                if plus:
                    desc += " <em>(Cost " + e["cost"] + " SP.)</em>"
                pack.item(name, "stunts", {
                    "longDesc": f"<p>{desc}</p>", "shortDesc": "", "type": folder, "stuntPoints": lo,
                    "stuntPointsMax": hi, "costType": "variable" if hi > lo or plus else "fixed",
                }, "split-cross.svg", folder=folder, uid=f"{heading}:{name}")
    return pack


BUILDERS = {"talents": build_talents, "stunts": build_stunts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "packs" / "_source"))
    ap.add_argument("--only", default=",".join(BUILDERS))
    a = ap.parse_args()
    pdf = Path(a.pdf)
    if not pdf.is_file():
        sys.exit(f"PDF not found: {pdf}")
    for name in a.only.split(","):
        BUILDERS[name](pdf).write(a.out)


if __name__ == "__main__":
    main()
