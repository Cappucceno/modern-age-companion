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


BUILDERS = {"talents": build_talents}


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
