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


def section(body):
    """Blocks of an entry up to the next chapter or section heading (the last entry of a section runs on into it)."""
    return body[: next((i for i, b in enumerate(body) if b[0] == "h" and b[1] <= 2), len(body))]


def chapter_pages(pdf, first, last, skip=(), skip_pages=(), stop_at=()):
    pages, words = book(pdf)
    return prose.chapter_sections(pages, first, last, words, skip=set(skip), skip_pages=skip_pages, stop_at=stop_at)


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
    # the Artificer talent of the extraordinary items chapter
    for title, parent, body in entries(blocks(pdf, 71, 72), 3):
        if title == "Artificer":
            req, desc, deg = parse_talent(section(body))
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


# --------------------------------------------------------------------------- enhancements (p62-70) and extraordinary items (p70-71)

ENH_TITLE = re.compile(r"^(?P<name>.+?) (?P<source>Biology|Magic|Technology|Superpower|Any) - (?P<cost>\d+) EN$")
ENH_MODS = {
    "Blessing of Deflection": [("defense", "1", "+1 Defense")],
    "Dermal Armor": [("toughness", "1", "+1 Toughness")],
}


def build_enhancements(pdf):
    pack = Pack("enhancements")
    ents = entries(blocks(pdf, 62, 71), 3)
    for title, parent, body in ents:
        if parent != "Sample Enhancements" or not title:
            continue
        m = ENH_TITLE.match(title)
        assert m, f"unexpected enhancement heading {title!r}"
        name, source, cost = m.group("name"), m.group("source"), m.group("cost")
        mods = dict(modifier(k, v) for k, v, _ in ENH_MODS.get(name, []))
        for mid, mod in mods.items():
            mod["flavor"] = next(n for k, v, n in ENH_MODS[name] if k == mod["type"])
        pack.item(name, "power", {
            "longDesc": f"<p><strong>Source:</strong> {esc(source)}. <strong>Cost:</strong> {cost} EN.</p>" + render(section(body)), "shortDesc": f"{cost} EN",
            "activate": False, "hasRoll": False, "modifiers": mods,
        }, "gift-of-knowledge.svg", folder=f"{source} Enhancements" if source != "Any" else "Any Source")
    return pack


ITEM_TITLE = re.compile(r"^(?P<name>.+?) (?P<rarity>Common|Uncommon|Rare|Unique)$")


def build_extraordinary_items(pdf):
    pack = Pack("extraordinary-items")
    for title, parent, body in entries(blocks(pdf, 70, 72), 3):
        if parent != "Sample Items" or not title:
            continue
        m = ITEM_TITLE.match(title)
        assert m, f"unexpected item heading {title!r}"
        name, rarity = m.group("name"), m.group("rarity")
        mods = {}
        if name == "Steelcoat":
            mods = dict([modifier("impactArmor", 6), modifier("ballisticArmor", 2)])
        pack.item(name, "equipment", {
            "longDesc": f"<p><strong>Rarity:</strong> {rarity}.</p>" + render(section(body)), "shortDesc": rarity, "weight": 0, "cost": 0,
            "quantity": 1, "equiped": False, "modifiers": mods,
        }, "briefcase.svg", folder=f"{rarity} Items")
    return pack


# --------------------------------------------------------------------------- explosives weapons (p96)

LAUNCHER_ROW = re.compile(r"^\s*(?P<name>Grenade Launcher|Rocket Launcher)\s+(?P<dmg>\(grenade\)|\d+d6\+\d+)\s+(?P<str>\d+)\s+(?P<src>[IPB](?:/B)?)\s+(?P<sw>[SW])\s+(?P<rof>\w+)\s+(?P<range>\d+) yards\s+\S+\s+(?P<reload>.+?)\s+(?P<tn>\d+)\s*$")


def build_weapons(pdf):
    pack = Pack("weapons")
    bl = blocks(pdf, 96, 96)
    desc = {}
    for b in bl:
        if b[0] == "p":
            m = re.match(r"^(Grenade Launcher|Rocket Launcher): (.*)$", text(b[1]))
            if m:
                desc[m.group(1)] = m.group(2)
    focus = None
    for line in pdftotext(pdf, 96, 96, "-layout")[0].splitlines():
        m = re.search(r"Focus: (.+?)\s*$", line)
        if m:
            focus = m.group(1)
        m = LAUNCHER_ROW.match(line)
        if not m:
            continue
        d = m.groupdict()
        grenade = d["dmg"] == "(grenade)"
        n, dt, extra = (1, 6, 0) if grenade else (int(d["dmg"][0]), 6, int(d["dmg"].split("+")[1]))
        reload_ = "major" if "Major" in d["reload"] else "minor"
        note = "<p>Damage is the same as the equivalent thrown grenade.</p>" if grenade else ""
        pack.item(d["name"], "weapon", {
            "longDesc": p(desc[d["name"]]) + note + p(f"Focus: {focus}. Minimum Strength {d['str']}. Range {d['range']} yards. Reload: {d['reload']}. Resources TN {d['tn']}."),
            "shortDesc": "", "useAbl": "acc", "useFocus": focus.lower(), "minStr": int(d["str"]), "nrDice": "0" if grenade else str(n), "diceType": str(dt),
            "extraValue": extra, "dmgAbl": "no-abl", "dmgType": "wound", "dmgSource": "ballistic" if grenade else "impact",
            "damageFormula": "0" if grenade else d["dmg"], "weight": 0, "cost": 0, "quantity": 1, "equiped": False, "purchaseTN": int(d["tn"]),
            "ranged": True, "rof": "singleShot", "range": int(d["range"]), "rangeMax": int(d["range"]) * 2, "capacity": 0, "reload": reload_,
        }, "fist.svg", folder=focus)
    return pack


# --------------------------------------------------------------------------- character creation (p6-21)

CLASS_WORDS = ("Outsider", "Lower", "Middle", "Upper")
CLASS_NAMES = {"Outsider": "Outsider", "Lower": "Lower Class", "Middle": "Middle Class", "Upper": "Upper Class"}
PEOPLES = ("Artificial Intelligence", "Dwarf", "Elf", "Human", "Orc", "Shapeshifter", "Spirit-Blood")
BENEFIT_ROW = re.compile(r"(\d{1,2}(?:[–-]\d{1,2})?)\s*(\+1 [A-Za-z]+|Focus: .+?)(?=\s*\d{1,2}(?:[–-]\d{1,2})?\s*(?:\+1 |Focus: )|$)")


def benefit_table(bl, i):
    """The 2d6 benefits table that starts at block i (a 'rollbenefit' header, then its rows in one or more blocks).

    Returns ([(range, benefit)], index of the first block after the table)."""
    joined, j = "", i
    while j < len(bl) and bl[j][0] == "p":
        t = text(bl[j][1])
        if j > i and not re.match(r"^\d", t):
            break
        joined += " " + t
        j += 1
        if re.search(r"(^|\s)12\s*\+1 [A-Za-z]+\s*$", joined):
            break
    rows = BENEFIT_ROW.findall(re.sub(r"^\s*2d6 roll\s*benefit", "", joined.strip()))
    assert rows and rows[0][0] == "2" and rows[-1][0] == "12", f"unexpected benefits table: {joined!r}"
    return [(r.replace("-", "–"), squash(b)) for r, b in rows], j


def with_tables(bl):
    """HTML for blocks where each benefits table is rebuilt as a real table; also returns the tables found."""
    out, tables, i = [], [], 0
    while i < len(bl):
        if bl[i][0] == "p" and text(bl[i][1]).startswith("2d6 roll"):
            rows, i2 = benefit_table(bl, i)
            tables.append(rows)
            out.append(table_html(["2d6 Roll", "Benefit"], rows))
            i = i2
        else:
            out.append(render([bl[i]]))
            i += 1
    return "".join(out), tables


def origin_entries(pdf):
    """[(name, class, blocks)] for the eight extraordinary origin backgrounds."""
    out = []
    for title, parent, body in entries(blocks(pdf, 11, 21), 3):
        if parent != "Extraordinary Origins" or not title:
            continue
        name, _, cls = title.rpartition(" ")
        assert cls in CLASS_WORDS, title
        out.append((name, CLASS_NAMES[cls], section(body)))
    return out


def people_entries(pdf):
    """[(name, intro blocks, inherited blocks)] for the seven peoples."""
    out = []
    for title, _, body in entries(blocks(pdf, 11, 21), 2):
        if title not in PEOPLES:
            continue
        subs = entries(body, 3)
        intro = subs[0][2]
        ben = next(b for t, _, b in subs if t and t.endswith("Inherited Benefits"))
        out.append((title, intro, ben))
    return out


def build_creation(pdf):
    pack = Pack("character-creation", "JournalEntry")
    origins, peoples = origin_entries(pdf), people_entries(pdf)
    skip = {f"{n} {k}" for n, c, _ in origins for k, v in CLASS_NAMES.items() if v == c}
    skip |= set(PEOPLES) | {f"{t.split()[0] if t != 'Artificial Intelligence' else 'AI'} Inherited Benefits" for t in PEOPLES}
    skip |= {"Spirit-Blood Inherited Benefits", "Outsider Middle Class", "Lower Class Upper Class", "Extraordinary Origins", "Peoples"}
    journal_doc(pack, "Chapter 1: Characters", chapter_pages(pdf, 6, 21, skip=skip))
    pages = []
    for name, cls, body in origins:
        html_, _ = with_tables(body)
        pages.append((name, f"<p><em>{cls}</em></p>" + html_))
    journal_doc(pack, "Origin Backgrounds", sorted(pages))
    pages = []
    for name, intro, ben in peoples:
        html_, _ = with_tables(ben)
        pages.append((name, render(intro) + "<h2>Inherited Benefits</h2>" + html_))
    journal_doc(pack, "Peoples", pages)
    return pack


def build_creation_tables(pdf):
    pack = Pack("creation-tables")
    for name, cls, body in origin_entries(pdf):
        _, tables = with_tables(body)
        table_doc(pack, f"{name} Benefits", "2d6", f"Benefits table of the {name} origin background ({cls}).",
                  [(int(re.split("[–-]", r)[0]), int(re.split("[–-]", r)[-1]), t) for r, t in tables[0]], "Origin Benefits")
    for name, _, ben in people_entries(pdf):
        _, tables = with_tables(ben)
        table_doc(pack, f"{name} Cultural Benefits", "2d6", f"Cultural benefits table of the {name} people.",
                  [(int(re.split("[–-]", r)[0]), int(re.split("[–-]", r)[-1]), t) for r, t in tables[0]], "Cultural Benefits")
    # social class and the four derivative tables, updated with the new backgrounds
    table_doc(pack, "Social Class", "1d6", "Social class of a character, with the Companion's origin backgrounds available in the tables below.",
              [(1, 1, "Outsider"), (2, 3, "Lower Class"), (4, 5, "Middle Class"), (6, 6, "Upper Class")], "Background Tables")
    for b in blocks(pdf, 11, 12):
        if b[0] == "table" and b[1][0][1].lower().replace("<strong>", "").replace("</strong>", "") == "background":
            pass
    heads = [("Outsider", "Middle Class"), ("Lower Class", "Upper Class")]
    tabs = [b[1] for b in blocks(pdf, 11, 12) if b[0] == "table" and len(b[1][0]) == 4]
    assert len(tabs) == 2, "unexpected background tables"
    for (left, right), rows in zip(heads, tabs):
        for col, cls in ((1, left), (3, right)):
            res = []
            for r in rows[1:]:
                n = int(r[col - 1])
                res.append((n, n, squash(re.sub(r"<[^>]+>", "", r[col]))))
            table_doc(pack, f"{cls} Backgrounds (with Companion origins)", "1d6", f"Background table for the {cls} social class, updated with the Companion's origin backgrounds.",
                      res, "Background Tables")
    return pack


BUILDERS = {"talents": build_talents, "stunts": build_stunts, "character-creation": build_creation, "creation-tables": build_creation_tables, "weapons": build_weapons,
            "enhancements": build_enhancements, "extraordinary-items": build_extraordinary_items}


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
