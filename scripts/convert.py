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


def chapter_pages(pdf, first, last, skip=(), skip_pages=(), stop_at=(), drop=None):
    pages, words = book(pdf)
    return prose.chapter_sections(pages, first, last, words, skip=set(skip), skip_pages=skip_pages, stop_at=stop_at, drop=drop)


def toks(s):
    """Lowercase words without digits, so SP costs glued to a word ('1-3action') do not break the comparison."""
    out = []
    for w in re.split(r"[^a-z0-9]+", s.lower()):
        w = re.sub(r"^\d+", "", w)
        if w:
            out.append(w)
    return out


_CORPUS = {}


def stunt_corpus(pdf):
    """3-grams of every stunt table of the book, to drop the garbled copies of those tables from the running text."""
    if "grams" not in _CORPUS:
        words, names = [], set()
        for page in STUNT_PAGES:
            for heading, themed, rows in stunt_tables(pdf, page):
                words += toks(heading)
                for r in rows:
                    words += toks(r)
                for e in parse_stunt_rows(rows, themed):
                    names.add(" ".join(toks(squash(" ".join(e["text"])).split(": ", 1)[0])))
        _CORPUS["grams"] = {tuple(words[i:i + 3]) for i in range(len(words) - 2)}
        _CORPUS["names"] = names
    return _CORPUS["grams"], _CORPUS["names"]


def stunt_dropper(pdf):
    grams, names = stunt_corpus(pdf)

    def drop(b):
        if b[0] not in ("p", "li"):
            return False
        t = text(b[1])
        w = toks(t)
        if not w:
            return bool(re.fullmatch(r"[\d–\-+\s]+", t))
        if " ".join(w) in names or t.lower().replace(" ", "") in ("spcoststunt", "stunt", "spcost"):
            return True
        if len(w) < 3:
            return False
        return all(tuple(w[i:i + 3]) in grams for i in range(len(w) - 2))
    return drop


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
STUNT_PAGES = {46: "Battle Map", 58: "General", 59: "Environmental", 60: "Environmental", 61: "Thematic", 73: "Intensity", 74: "Intensity",
               84: "Organization", 85: "Organization"}
CROP = {84: (300, 303), 85: (0, 300)}      # pages where the table sits in one column next to running text


def stunt_tables(pdf, page):
    """[(heading, [(cost, setting, text)])] for each 'SP Cost / Stunt' table on a page, read from the layout text."""
    x, w = CROP.get(page, (0, 612))
    lines = pdftotext(pdf, page, page, "-layout", "-x", str(x), "-y", "0", "-W", str(w), "-H", "783")[0].splitlines()
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
                elif kind == "Organization":
                    folder, desc = "Organization Stunts", esc(desc)
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
    age_of_sail_weapons(pdf, pack)
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


# --------------------------------------------------------------------------- journals: rules, organizations, events, genres

def merge_lists(pdf, bl):
    """List items that run on in the next block (a lowercase start) are joined back into one item."""
    words = book(pdf)[1]
    out = []
    for b in bl:
        if b[0] == "li" and out and out[-1][0] == "li" and re.match(r"[a-z]", prose.plain(b[1]).lstrip()[:1] or "A"):
            out[-1] = ("li", words.join(out[-1][1], b[1]))
        else:
            out.append(b)
    return out


def split_pages(bl, level=1, intro="Introduction"):
    """[(title, blocks)] with one entry per heading of `level`; blocks before the first one go under `intro`."""
    out, cur = [], [intro, []]
    for b in bl:
        if b[0] == "h" and b[1] == level:
            if cur[1]:
                out.append(cur)
            cur = [b[2], []]
        else:
            cur[1].append(b)
    if cur[1]:
        out.append(cur)
    return out


def tidy_page(html_, cut_after=None):
    """Remove the leftovers of stunt tables from a page; `cut_after` ends the page after the paragraph that contains the marker."""
    if cut_after and cut_after in html_:
        html_ = html_[: html_.index("</p>", html_.index(cut_after)) + 4]
    return re.sub(r"\s*sP Cost(?:Genre/setting)?\s*stunt\s*|\s*sP Cost\s*", " ", html_)


def html_pages(bl, level=1, skip=(), intro="Introduction", cut_after=None):
    return [(squash(t), tidy_page(render(prose._drop_sections(b, set(skip))), cut_after)) for t, b in split_pages(bl, level, intro) if prose._drop_sections(b, set(skip))]


def talent_headings(pdf):
    """Headings of Chapter 2 whose text lives in talent items."""
    out = set()
    for title, parent, body in entries(blocks(pdf, 22, 33), 3):
        if title and parent in ("Existing Talents", "New Talents"):
            out.add(title)
    for t, _, _ in entries(blocks(pdf, 22, 33), 2):
        pass
    return out


SPECS = ("Controller", "Communicator", "Field Researcher", "Hunter", "Liaison", "Special Forces", "Technician")


def build_rules_reference(pdf):
    pack = Pack("rules-reference", "JournalEntry")
    drop = stunt_dropper(pdf)
    names = talent_headings(pdf) | set(SPECS)
    journal_doc(pack, "Introduction", html_pages(blocks(pdf, 4, 5), 1))
    journal_doc(pack, "Chapter 2: Talents and Specializations", html_pages(blocks(pdf, 22, 33), 1, skip=names | {"Artificer"}))
    journal_doc(pack, "Chapter 3: Rules", html_pages([b for b in blocks(pdf, 34, 55) if not drop(b)], 1))
    journal_doc(pack, "Chapter 4: Stunts", html_pages([b for b in blocks(pdf, 56, 61) if not drop(b)], 1, cut_after="Thematic stunts may apply all the time"))
    enh = {t for t, par, _ in entries(blocks(pdf, 62, 71), 3) if par in ("Sample Enhancements", "Sample Items")}
    journal_doc(pack, "Chapter 5: Extraordinary Abilities", html_pages([b for b in blocks(pdf, 62, 72) if not drop(b)], 1, skip=enh | {"Artificer"}))
    journal_doc(pack, "Chapter 6: Social Options", html_pages([b for b in blocks(pdf, 72, 79) if not drop(b)], 1))
    journal_doc(pack, "Chapter 7: Technology", html_pages([b for b in blocks(pdf, 90, 98) if not drop(b)], 1))
    return pack


# --------------------------------------------------------------------------- weapons of the Age of Sail (p118)

AOS_ROW = re.compile(r"^\s*(?P<name>[A-Z][A-Za-z/]+(?: [A-Z][a-z]+)?)\s{2,}(?P<dmg>\d+d6(?:\+\d+| – \d+)?)\s+(?P<str>\d+)\s+(?P<src>[IPB])\s+(?P<sw>[SW])\s+(?P<tn>\d+)\s+(?P<rof>\S+)\s+(?P<range>.+?)\s{2,}(?P<cap>\S+)\s+(?P<reload>.+?)\s*$")


def age_of_sail_weapons(pdf, pack):
    desc = {}
    for b in blocks(pdf, 118, 118):
        if b[0] == "p":
            m = re.match(r"^(Dragon|Flintlock Pistol|Broadsword/Cutlass): (.*)$", text(b[1]))
            if m:
                desc[m.group(1)] = m.group(2)
    focus = None
    for line in pdftotext(pdf, 118, 118, "-layout")[0].splitlines():
        m = re.search(r"Focus: (.+?)\s*$", line)
        if m:
            focus = m.group(1)
        m = AOS_ROW.match(line)
        if not m:
            continue
        d = m.groupdict()
        ranged = d["rof"] != "N/A"
        dmg = d["dmg"].replace(" – ", "-")
        n = int(dmg[0])
        extra = int(re.search(r"([+-]\d+)$", dmg).group(1)) if re.search(r"[+-]\d+$", dmg) else 0
        s_ = {
            "longDesc": p(desc[d["name"]]) + p(f"Focus: {focus}. Minimum Strength {d['str']}. Resources TN {d['tn']}." + (f" Range {d['range'].rstrip('*')} yards. Reload: {d['reload']}." if ranged else "")),
            "shortDesc": "", "useAbl": "acc" if ranged else "fight", "useFocus": focus.lower(), "minStr": int(d["str"]), "nrDice": str(n), "diceType": "6",
            "extraValue": extra, "dmgAbl": "per" if ranged else "str", "dmgType": "wound", "dmgSource": "impact", "damageFormula": dmg,
            "weight": 0, "cost": 0, "quantity": 1, "equiped": False, "purchaseTN": int(d["tn"]), "ranged": ranged,
        }
        if ranged:
            s_.update({"rof": "singleShot", "range": 10, "rangeMax": 20, "capacity": 0, "reload": "minor"})
        pack.item(d["name"], "weapon", s_, "fist.svg", folder=focus)


# --------------------------------------------------------------------------- organization focuses (p82)

ORG_ABILITIES = ("Might", "Wealth", "Influence", "Intrigue", "Phenomena")


def build_focuses(pdf):
    pack = Pack("focuses")
    for title, _, body in entries(blocks(pdf, 82, 82), 2):
        m = re.match(r"^(" + "|".join(ORG_ABILITIES) + r") Focuses$", title or "")
        if not m:
            continue
        for t in paras(section(body)):
            # a block holds one or several 'Name: description' entries run together
            name_re = r"[A-Z][A-Za-z’'-]*(?: (?:and|of|the|[A-Z][A-Za-z’'-]*))*"
            found = re.findall(rf"({name_re}): (.*?)(?= {name_re}: |$)", t)
            assert found and " ".join(f"{n}: {d}" for n, d in found) == t, f"unparsed organization focuses: {t!r}"
            found = [list(x) for x in found]
            for k in range(1, len(found)):
                if found[k][0].startswith("Scope "):       # 'within the organization’s Scope' ends the previous description
                    found[k - 1][1] += " Scope"
                    found[k][0] = found[k][0][len("Scope "):]
            for name, desc in found:
                pack.item(name, "focus", {
                    "longDesc": p(desc), "shortDesc": "", "useAbl": "", "initialValue": 2, "improved": False, "isOrg": True,
                }, "gift-of-knowledge.svg", folder=f"{m.group(1)} Focuses", uid=f"{m.group(1)}:{name}")
    return pack


# --------------------------------------------------------------------------- roll tables: events (p103) and misfortunes (p84)

EVENT_TABLES = {
    "Crime and Punishment": ["Arrest", "Corruption", "Matter of Justice", "Notorious Crime", "Organized Crime", "Terrorist Attack"],
    "Culture": ["Artwork", "Failure", "Innovation", "Religious Movement", "Scandal", "Sporting Event"],
    "Economics": ["Bubble", "Corporate Shift", "Economic Downturn", "Espionage", "Inequality", "Prosperity"],
    "Politics": ["Appointment/Election", "Corruption", "Coup", "Espionage", "Militant Politics", "Political Crisis"],
    "War and Peace": ["Coup", "Espionage", "Military Action", "Militant Politics", "Peacemaking", "Terrorist Attack"],
    "Miscellaneous Incidents": ["Disease", "Ecological Event", "Industrial Accident", "Infrastructure Failure", "Natural Disaster", "Weirdness"],
}
EVENT_PROGRESS = [
    (1, 1, "No event or change"), (2, 2, "New minor event"),
    (3, 3, "Existing event intensity increases by one step or new minor event*"), (4, 4, "New moderate event"),
    (5, 5, "Existing event intensity increases by two steps or new moderate event*"),
    (6, 6, "New major event and roll again. If the second roll is a 6, it’s a new epic event instead."),
]


def build_campaign_events(pdf):
    pack = Pack("campaign-events", "RollTable")
    listed = {e["name"] for e in event_listings(pdf)}
    table_doc(pack, "Event Progress & Intensity", "1d6", "Roll for progress or intensity of a campaign event. * If an event is active, increase its intensity as noted; if no events are active, create a new event of the listed intensity.",
              EVENT_PROGRESS, "Campaign Events")
    table_doc(pack, "Event Categories", "1d6", "Category of a new campaign event.",
              [(i + 1, i + 1, c) for i, c in enumerate(EVENT_TABLES)], "Campaign Events")
    for cat, evs in EVENT_TABLES.items():
        missing = [e for e in evs if e not in listed]
        assert not missing, f"events not found in the listings: {missing}"
        table_doc(pack, f"{cat} Events", "1d6", f"Specific event of the {cat} category. See the Campaign Event Listings journal for the intensity levels and campaign hooks of each.",
                  [(i + 1, i + 1, e) for i, e in enumerate(evs)], "Event Tables")
    return pack


def build_rules_tables(pdf):
    pack = Pack("rules-tables", "RollTable")
    lines = pdftotext(pdf, 84, 84, "-layout")[0].splitlines()
    start = next(i for i, l in enumerate(lines) if re.match(r"^\s*2d6 Roll\s+Misfortune", l))
    rows = []
    for l in lines[start + 1:]:
        if not l.strip():
            break
        rows.append(l)
    mis = parse_stunt_rows(rows, False)
    assert len(mis) == 7, f"expected 7 misfortunes, got {len(mis)}"
    res = []
    for e in mis:
        m = COST.match(e["cost"].replace("–", "-"))
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        res.append((lo, hi, squash(" ".join(e["text"]))))
    table_doc(pack, "Organization Misfortunes", "2d6", "Misfortune that befalls an organization after a failed growth check.", res, "Organizations")
    return pack


EVENT_TAGS = {"C&P": "Crime & Punishment", "CUL": "Culture", "Econ": "Economics", "POL": "Politics", "W&P": "War & Peace", "MISC": "Miscellaneous"}
EVENT_START = re.compile(r"^(?P<name>[A-Z][A-Za-z/&’' ]+?) (?P<tags>(?:(?:C&P|CUL|Econ|POL|W&P|MISC)(?: |$))+)$")


def event_listings(pdf):
    """[{name, tags, blocks}] for the campaign events of Chapter 8."""
    out, cur = [], None
    for b in blocks(pdf, 105, 114):
        if b[0] == "h" and b[1] == 2 and b[2].strip() == "Event Game Effects":
            break
        m = EVENT_START.match(text(b[1])) if b[0] == "p" else None
        if m:
            cur = {"name": m.group("name"), "tags": m.group("tags").split(), "blocks": []}
            out.append(cur)
        elif cur is not None:
            cur["blocks"].append(b)
    return out


def events_journal(pdf):
    pages = []
    for e in event_listings(pdf):
        cats = ", ".join(EVENT_TAGS[t] for t in e["tags"])
        hooks = [b for b in e["blocks"] if b[0] == "h"]
        pages.append((e["name"], f"<p><em>Category: {esc(cats)}</em></p>" + render(e["blocks"])))
    return pages


# --------------------------------------------------------------------------- organizations journal (p80-90) and gm guide (p99-127)

SAMPLE_ORGS = ("Cult", "Paramilitary Group", "Street Gang", "Pyramid Scheme", "Crime Syndicate", "Aristocratic House")


def org_stats(html_):
    return re.sub(r"<p>(Might|Wealth|Influence|Intrigue|Phenomena)(-?\d+)", r"<p><strong>\1</strong> \2", html_)


def build_organizations(pdf):
    pack = Pack("organizations", "JournalEntry")
    bl = merge_lists(pdf, blocks(pdf, 80, 90))
    cut = next(i for i, b in enumerate(bl) if b[0] == "h" and b[2].strip() == "Making & Tinkering")
    bl = bl[:cut]
    pages = []
    for title, body in split_pages(bl, 2, "Organizations"):
        if title == "Sample Organizations":
            for t3, body3 in split_pages(body, 3, "Sample Organizations"):
                if t3 in SAMPLE_ORGS:
                    pages.append((t3, org_stats(render(body3))))
        else:
            pages.append((title, render(body)))
    out = []
    for t, h in pages:
        if t in ("Might Focuses", "Wealth Focuses", "Influence Focuses", "Intrigue Focuses"):
            continue       # these lists are items of the focuses pack
        if t == "Phenomena Focuses":       # two sidebars are printed next to this list
            war, ex = h.index("<p>When players think"), h.index("<p><em>The players in Meghan")
            out += [("When Organizations Go to War", h[war:ex]), ("Example Organization: The Locomotive Busters", h[ex:])]
            continue
        out.append((squash(t), h))
    journal_doc(pack, "Organizations", [(t, h) for t, h in out if h])
    return pack


GENRES = ("Pirates, Swashbucklers", "Nevermore:", "From Outer Space!", "Agents of ACRONYM:", "Wired & Watched")


def build_gm_guide(pdf):
    pack = Pack("gm-guide", "JournalEntry")
    ch8 = merge_lists(pdf, blocks(pdf, 99, 115))
    first = next(i for i, b in enumerate(ch8) if b[0] == "p" and EVENT_START.match(text(b[1])))
    last = next(i for i, b in enumerate(ch8) if b[0] == "h" and b[2].strip() == "Event Game Effects")
    journal_doc(pack, "Chapter 8: The Modern Campaign", html_pages(ch8[:first] + ch8[last:], 1))
    journal_doc(pack, "Campaign Event Listings", events_journal(pdf))
    ch9 = merge_lists(pdf, blocks(pdf, 116, 126))
    pages, cur = [], ["Introduction", []]
    for b in ch9:
        if b[0] == "h" and b[1] == 2 and b[2].lower().startswith(tuple(g.lower() for g in GENRES)):
            pages.append(cur)
            cur = [squash(b[2]), []]
        else:
            cur[1].append(b)
    pages.append(cur)
    fixed = []
    for t, body in pages:
        if body and body[0][0] == "p" and re.match(r"^\d{4}s ", text(body[0][1])):
            t = f"{t} {text(body[0][1])}"
            body = body[1:]
        if body:
            fixed.append((t, render(body)))
    journal_doc(pack, "Chapter 9: Genres", fixed)
    return pack


BUILDERS = {"talents": build_talents, "focuses": build_focuses, "campaign-events": build_campaign_events,
            "organizations": build_organizations, "gm-guide": build_gm_guide, "rules-tables": build_rules_tables, "stunts": build_stunts, "rules-reference": build_rules_reference, "character-creation": build_creation, "creation-tables": build_creation_tables, "weapons": build_weapons,
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
