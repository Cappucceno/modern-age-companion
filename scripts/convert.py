#!/usr/bin/env python3
"""Convert the Modern AGE Basic Rulebook PDF into Foundry pack sources for the AGE System.

Usage: scripts/convert.py PATH/TO/Modern_AGE_Basic_Rulebook.pdf [--out packs/_source] [--only focuses,talents,...]

The PDF is read with `pdftotext` (poppler-utils) and is never stored in the repository.
Output is one JSON per document in packs/_source/<pack>/, plus `_folder-*.json` for folders, in the same
layout as the Expanse module. Ids are deterministic (hash of pack + name) so reruns produce stable diffs.

Page numbers below are PDF pages (printed page number + 1).
"""
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
    return out


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

    def item(self, name, type_, system, img, folder=None, sort=0):
        did = make_id(self.name, name)
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


# --------------------------------------------------------------------------- focuses

NAME_RE = r"[A-Z][A-Za-z'’&/\-]*(?: (?:of|and|the|in|on|to|for|[A-Z][A-Za-z'’&/\-]*)){0,4}"


# These two entries are interrupted by a sidebar box in the PDF, so the extracted text is incomplete.
FOCUS_FIXES = {
    "Navigation": "Planning and following a route from one place to another, interpreting maps and geographical data, "
                  "and figuring out where you are.",
    "Propelling": "Moving a muscle-powered vehicle such as a bicycle or rowboat.",
}


def parse_focuses(pdf, words):
    pages = column_pages(pdf, 53, 56)
    lines = [l for pg in pages for l in pg]
    out, ability, cur = [], None, None
    head = re.compile(r"^(" + "|".join(ABILITIES) + r") Focuses$")
    entry = re.compile(r"^(" + NAME_RE + r"): (.+)$")
    for l in lines:
        s = l.strip()
        if not s:
            continue
        if s == "Talents":
            break
        m = head.match(s)
        if m:
            ability, cur = m.group(1), None
            continue
        if ability is None:
            continue
        if s.startswith("Optional"):
            cur = None
            continue
        m = entry.match(s)
        if m:
            cur = {"name": m.group(1), "ability": ability, "lines": [m.group(2)]}
            out.append(cur)
        elif cur is not None and not cur["lines"][-1].endswith("."):
            # an entry ends at its first full stop; anything after it is sidebar text from the same column
            cur["lines"].append(s)
    for f in out:
        f["desc"] = FOCUS_FIXES.get(f["name"]) or paragraph(f["lines"], words)
    return out


def build_focuses(pdf, words):
    pack = Pack("focuses")
    for f in parse_focuses(pdf, words):
        pack.item(f["name"], "focus", {
            "longDesc": p(f["desc"]), "shortDesc": "", "useAbl": ABILITIES[f["ability"]], "initialValue": 2,
            "improved": False, "isOrg": False,
        }, "gift-of-knowledge.svg", folder=f"{f['ability']} Focuses")
    return pack


# --------------------------------------------------------------------------- talents and specializations

DEGREE = re.compile(r"^(Novice|Expert|Master): ?(.*)$")


def parse_blocks(lines, words, header_re):
    """Split lines into blocks starting at a name line directly followed by a requirement line."""
    blocks, cur = [], None
    n = len(lines)
    for i, l in enumerate(lines):
        s = l.strip()
        nxt = lines[i + 1].strip() if i + 1 < n else ""
        if s and header_re.match(nxt) and not DEGREE.match(s) and len(s) < 60:
            cur = {"name": re.sub(r" Talent$", "", s), "lines": []}
            blocks.append(cur)
            continue
        if cur is not None:
            cur["lines"].append(l)
    out = []
    for b in blocks:
        req, pre, deg, key, plural = [], [], {"Novice": [], "Expert": [], "Master": []}, None, False
        mode = "req"
        for l in b["lines"]:
            s = l.strip()
            if not s:
                continue
            m = DEGREE.match(s)
            if m:
                mode, key = "deg", m.group(1)
                deg[key].append([m.group(2)])
                continue
            if re.match(r"^Requirements?:", s):
                mode = "req"
                req = [re.sub(r"^Requirements?: ?", "", s)]
                plural = s.startswith("Requirements")  # specializations end their requirement with a full stop
                continue
            if mode == "req":
                prev = req[-1] if req else ""
                wraps = (plural and not prev.endswith(".")) or prev.endswith((",", " and", " or", " the", " a", " an", " of", " in")) or prev.count("(") > prev.count(")") or (len(prev) >= 54 and not prev.endswith(".")) \
                    or s.startswith("(") or s[:1].islower()
                if req and wraps:
                    req.append(s)
                else:
                    mode = "pre"
                    pre.append(s)
            elif mode == "pre":
                pre.append(s)
            elif mode == "deg":
                deg[key][-1].append(s)
        out.append({
            "name": b["name"], "requirement": paragraph(req, words) if req else "",
            "desc": paragraph(pre, words),
            "deg": {k: [paragraph(v, words) for v in vs] for k, vs in deg.items()},
        })
    return out


def parse_talents(pdf, words):
    pages = column_pages(pdf, 56, 64)
    lines = [l for pg in pages for l in pg]
    start = next(i for i, l in enumerate(lines) if l.strip() == "Talent Descriptions")
    return parse_blocks(lines[start + 1:], words, re.compile(r"^Requirements?:"))


def build_talents(pdf, words):
    pack = Pack("talents")
    for t in parse_talents(pdf, words):
        system = {
            "longDesc": p(t["desc"]) if t["desc"] else "", "shortDesc": "", "requirement": t["requirement"], "type": "talent",
            "degree": 0, "activate": True,
        }
        for i, d in enumerate(("Novice", "Expert", "Master")):
            system[f"degree{i}"] = {"desc": "".join(p(x) for x in t["deg"][d]), "isActive": True}
        pack.item(t["name"], "talent", system, "skills.svg", folder="Talents")
    build_specs(pack, pdf, words)
    return pack



SPECIALIZATIONS = ["Academic", "Agent", "Driver", "Executive", "Gunfighter", "Investigator", "Martial Artist", "Performer",
                   "Sniper", "Socialite", "Thief"]


def parse_specs(pdf, words):
    pages = column_pages(pdf, 65, 71)
    lines = [l.strip() for pg in pages for l in pg if l.strip()]
    start = next(i for i, l in enumerate(lines) if l == "Specializations")
    lines = lines[start + 1:]
    heads = []
    for name in SPECIALIZATIONS:
        # heading = the name alone on a line, directly followed by its flavor text (not the "<Name> Talent" line)
        idx = next((i for i, l in enumerate(lines) if l == name and i >= (heads[-1][1] if heads else 0)), None)
        if idx is None:
            print(f"warning: specialization {name!r} not found", file=sys.stderr)
            continue
        heads.append((name, idx))
    out = []
    for n, (name, idx) in enumerate(heads):
        end = heads[n + 1][1] if n + 1 < len(heads) else len(lines)
        chunk = lines[idx + 1:end]
        t = next((i for i, l in enumerate(chunk) if l == f"{name} Talent"), None)
        if t is None:
            print(f"warning: no talent block for {name!r}", file=sys.stderr)
            continue
        blocks = parse_blocks(chunk[t:], words, re.compile(r"^Requirements?:"))
        if not blocks:
            continue
        b = blocks[0]
        b["name"], b["flavor"] = name, paragraph(chunk[:t], words)
        out.append(b)
    return out


def build_specs(pack, pdf, words):
    for t in parse_specs(pdf, words):
        system = {
            "longDesc": p(t["flavor"]) + (p(t["desc"]) if t["desc"] else ""), "shortDesc": "", "requirement": t["requirement"],
            "type": "spec", "degree": 0, "activate": True,
        }
        for i, d in enumerate(("Novice", "Expert", "Master")):
            system[f"degree{i}"] = {"desc": "".join(p(x) for x in t["deg"][d]), "isActive": True}
        pack.item(t["name"], "talent", system, "skills.svg", folder="Specializations")


# --------------------------------------------------------------------------- weapons

WEAPON_ROW = re.compile(r"^\s*(?P<name>[A-Z][\w' \-]+?)\s{2,}(?P<dmg>\d+d\d+(?:[+\-–]\d+)?\*?)\s+(?P<minstr>[–\-—]?\d*|—)\s+"
                        r"(?P<src>[IPBW])\s+(?P<sw>[SW])\s+(?P<tn>\d+|—)(?P<rest>.*)$")
FOCUS_ROW = re.compile(r"^\s*Focus: (.+?)\s*$")


def num(s):
    s = s.replace("–", "-").replace("−", "-").lstrip("+")
    return int(s) if re.fullmatch(r"-?\d+", s) else 0


def parse_weapons(pdf):
    rows = []
    for page in pdftotext(pdf, 73, 74, "-layout"):
        focus, ranged = None, False
        for l in page.splitlines():
            if re.search(r"Weapon\s+Damage", l):
                ranged = "ROF" in l
                continue
            m = FOCUS_ROW.match(l)
            if m:
                focus = m.group(1)
                continue
            # PDF layout puts the I/P/B letter on a separate line in a few rows; handle the regular rows only
            m = WEAPON_ROW.match(l)
            if not m or not focus:
                continue
            d = m.groupdict()
            d["focus"], d["ranged"] = focus, ranged
            rest = d.pop("rest").split()
            if ranged and len(rest) >= 3:
                d["rof"] = rest[0]
                # range may be '15 + STR yards', '55 yards*', '1000 yards**'
                tail = " ".join(rest[1:])
                mm = re.match(r"(.+?yards)\*{0,2}\s+(\S+)\s+(.+)$", tail)
                if mm:
                    d["range"], d["capacity"], d["reload"] = mm.groups()
            rows.append(d)
    return rows


def build_weapons(pdf):
    pack = Pack("weapons")
    src = {"I": "impact", "B": "ballistic", "P": "penetrating", "W": "impact"}
    rof = {"SS": "singleShot", "SS*": "singleShot", "SA": "semiAuto", "A": "fullAuto"}
    for w in parse_weapons(pdf):
        n, dtype = re.match(r"(\d+)d(\d+)", w["dmg"]).groups()
        extra = re.search(r"([+\-–]\d+)", w["dmg"])
        extra = num(extra.group(1)) if extra else 0
        s = {
            "longDesc": p(f"Focus: {w['focus']}. Minimum Strength {w['minstr']}. Resources TN {w['tn']}." + (" Knuckle dusters or steel toecaps add +1 damage." if w["dmg"].endswith("*") else "")),
            "shortDesc": "", "useAbl": "acc" if w["ranged"] else "fight", "useFocus": w["focus"].lower(), "minStr": num(w["minstr"]),
            "nrDice": n, "diceType": dtype, "extraValue": extra, "dmgAbl": "per" if w["ranged"] else "str",
            "dmgType": "stun" if w["sw"] == "S" else "wound", "dmgSource": src[w["src"]],
            "damageFormula": re.sub(r"\*", "", w["dmg"]).replace("–", "-"),
            "weight": 0, "cost": 0, "quantity": 1, "equiped": False, "purchaseTN": num(w["tn"]),
            "ranged": w["ranged"],
        }
        if w["ranged"]:
            r = re.match(r"(\d+)", w.get("range", ""))
            s.update({"range": int(r.group(1)) if r else 0, "rangeMax": int(int(r.group(1)) * 1.5) if r else 0,
                      "rof": rof.get(w.get("rof", ""), "none"), "capacity": num(w.get("capacity", "0")),
                      "reload": "major" if "Major" in w.get("reload", "") else "minor"})
        pack.item(w["name"], "weapon", s, "fist.svg", folder=w["focus"])
    return pack


# --------------------------------------------------------------------------- armor and shields

ARMOR_ROW = re.compile(r"^\s*(?P<name>[A-Z][A-Za-z\- ]+?)\s{2,}(?P<i>\d+)I/(?P<b>\d+)B\*?\s+(?P<pen>[–\-]?\d+)\s+(?P<tn>\d+)(?:\s{2,}.*)?$")
SHIELD_ROW = re.compile(r"^\s*(?P<name>[A-Z][A-Za-z\- ]+?)\s{2,}\+(?P<bonus>\d+)\*?\s+(?P<tn>\d+)(?:\s{2,}.*)?$")


def modifier(kind, formula):
    mid = make_id("modifier", kind + str(formula))
    return mid, {"type": kind, "formula": str(formula), "flavor": "", "isActive": True, "valid": True,
                 "conditions": {}, "ftype": "", "key": mid}


def build_armor(pdf):
    armor = Pack("armor")  # shields live in the same pack, in their own folder
    page = pdftotext(pdf, 77, 77, "-layout")[0]
    for l in page.splitlines():
        l = l[:90]
        m = ARMOR_ROW.match(l)
        if m:
            d = m.groupdict()
            mods = dict([modifier("impactArmor", d["i"]), modifier("ballisticArmor", d["b"]),
                         modifier("armorPenalty", abs(num(d["pen"])))])
            armor.item(d["name"], "equipment", {
                "longDesc": p(f"Armor rating {d['i']}I/{d['b']}B, armor penalty {num(d['pen'])}, Resources TN {d['tn']}."),
                "shortDesc": "", "weight": 0, "cost": 0, "quantity": 1, "equiped": False, "purchaseTN": int(d["tn"]),
                "modifiers": mods,
            }, "briefcase.svg", folder="Armor")
            continue
        m = SHIELD_ROW.match(l)
        if m:
            d = m.groupdict()
            mid, mod = modifier("defense", d["bonus"])
            armor.item(d["name"], "equipment", {
                "longDesc": p(f"Shield bonus +{d['bonus']} to Defense; restricts you to one-handed weapons. Resources TN {d['tn']}."),
                "shortDesc": "", "weight": 0, "cost": 0, "quantity": 1, "equiped": False, "purchaseTN": int(d["tn"]),
                "modifiers": {mid: mod},
            }, "briefcase.svg", folder="Shields")
    return armor


# --------------------------------------------------------------------------- vehicles

VEHICLE_ROW = re.compile(r"^\s*(?P<name>[A-Z][A-Za-z\- ]+?)\s{2,}(?P<hand>[+\u2013\-]?\d+)\s+(?P<cap>\d+)\s+(?P<vel>(?:Standard|Fast|Very Fast|Extreme)/\S+)\s+"
                         r"(?P<ram>\d+d6|0)\s+(?P<hull>\d+)\s+(?P<tn>\d+|N/A\*{0,2})")
VELOCITY = {"Standard": "standard", "Fast": "fast", "Very Fast": "veryFast", "Extreme": "extreme"}
ACTOR_ICON = "icons/svg/mystery-man.svg"


def parse_vehicles(pdf, words):
    focus_abl = {f["name"]: ABILITIES[f["ability"]] for f in parse_focuses(pdf, words)}
    rows, focus = [], None
    for l in pdftotext(pdf, 79, 79, "-layout")[0].splitlines():
        m = FOCUS_ROW.match(l[:140])
        if m:
            focus = m.group(1)
            continue
        m = VEHICLE_ROW.match(l)
        if m and focus:
            d = m.groupdict()
            d["focus"], d["abl"] = focus, focus_abl.get(focus, "dex")
            rows.append(d)
    # descriptions: "Name: text" paragraphs after the "Vehicle Descriptions" heading, in column order
    lines = [l.strip() for l in column_pages(pdf, 79, 79)[0]]
    start = next(i for i, l in enumerate(lines) if l == "Vehicle Descriptions")
    names = [r["name"] for r in rows]
    desc, cur = {}, None
    for l in lines[start + 1:]:
        m = re.match(r"^([A-Z][A-Za-z\- ]+): (.*)$", l)
        if m and m.group(1) in names:
            cur = desc.setdefault(m.group(1), [m.group(2)])
        elif cur is not None and l and not cur[-1].endswith("."):
            cur.append(l)
    for r in rows:
        r["desc"] = paragraph(desc.get(r["name"], []), words)
    return rows


def build_vehicles(pdf, words):
    pack = Pack("vehicles", "Actor")
    for v in parse_vehicles(pdf, words):
        cls, mod = v["vel"].split("/")
        pack.actor(v["name"], "vehicle", {
            "handling": {"mod": num(v["hand"]), "useFocus": v["focus"].lower(), "useAbl": v["abl"]},
            "capacity": int(v["cap"]), "velocity": {"category": VELOCITY[cls], "catMod": num(mod)},
            "ramDmg": int(v["ram"].split("d")[0]), "hull": int(v["hull"]), "cost": 0,
            "description": p(v["desc"]) + (p(f"Resources TN {v['tn']}.") if v["tn"][0].isdigit() else p("Cannot be purchased under ordinary circumstances.")), "gmNotes": "", "coverRating": 0, "armorRating": 0,
        }, v["focus"])
    return pack


# --------------------------------------------------------------------------- equipment

GEAR_CELL = re.compile(r"([A-Z][A-Za-z'\u2019/ ()\-]*?[A-Za-z)])\s{2,}(\d+)(?=\s|$)")
GEAR_NOTES = {"Chaff Grenades", "Flash Grenades", "Magnifying Scope", "Night Vision Goggles", "Smoke Grenades",
              "Suppressor", "Thermal Goggles"}


def parse_gear(pdf, words):
    page = pdftotext(pdf, 80, 80, "-layout")[0]
    lines = page.splitlines()
    items = {"Lifestyle": [], "Adventuring Gear": []}
    section = None
    for l in lines:
        s = l.strip()
        if s == "Lifestyle":
            section = "Lifestyle"
        elif s == "Adventuring Gear":
            section = "Adventuring Gear"
        elif section and l.startswith((" ", "\t")) and re.search(r"\s{2,}\d+(\s|$)", l) and not s.startswith("Gear "):
            if len(l) - len(l.lstrip()) < 20 or section == "Adventuring Gear":
                items[section] += [(n.strip(), int(tn)) for n, tn in GEAR_CELL.findall(l)]
    # "Night Vision Goggles" wraps over three lines in the table
    if not any(n.startswith("Night Vision") for n, _ in items["Adventuring Gear"]):
        items["Adventuring Gear"].append(("Night Vision Goggles", 14))
    # descriptions of a few gear items, from the prose on the same page
    text = [l.strip() for l in column_pages(pdf, 80, 80)[0]]
    desc, cur = {}, None
    for l in text:
        m = re.match(r"^([A-Z][A-Za-z ]+?)(?: \[[^\]]+\])?: (.*)$", l)
        if m and m.group(1) in GEAR_NOTES:
            cur = desc.setdefault(m.group(1), [m.group(2)])
        elif cur is not None and l:
            # an entry ends at a full stop, except for the "Gritty:"/"Pulpy/Cinematic:" variants that follow it
            if not cur[-1].endswith(".") or l.startswith(("Gritty:", "Pulpy")):
                cur.append(l)
    return items, {k: paragraph(v, words) for k, v in desc.items()}


def build_equipment(pdf, words):
    pack = Pack("equipment")
    items, desc = parse_gear(pdf, words)
    for folder, rows in items.items():
        for name, tn in sorted(set(rows)):
            text = desc.get(name, "")
            pack.item(name, "equipment", {
                "longDesc": (p(text) if text else "") + p(f"Resources TN {tn}."), "shortDesc": "", "weight": 0, "cost": 0,
                "quantity": 1, "equiped": False, "purchaseTN": tn, "modifiers": {},
            }, "briefcase.svg", folder=folder)
    return pack


# --------------------------------------------------------------------------- adversaries

WEAPON_LINE = re.compile(r"^\s*(?P<name>[A-Z][A-Za-z' \-]+?)\s{2,}(?P<atk>[+\u2013\-]\d+)\s{2,}(?P<dmg>\d+d\d+(?:[+\-]\d+)?)\s*$")
LABEL = re.compile(r"^[A-Z][A-Za-z' \-/&()]{1,40}: ")
ADV_SECTIONS = ("Action", "Exploration", "Social")
ADV_ICON = "systems/age-system/resources/imgs/actor-icon/sensousness.svg"


def adversary_lines(pdf):
    """Both columns of PDF pages 134-152, left then right, in layout mode, footers removed."""
    left = pdftotext(pdf, 134, 152, "-layout", "-x", "0", "-y", "0", "-W", "300", "-H", "783")
    right = pdftotext(pdf, 134, 152, "-layout", "-x", "300", "-y", "0", "-W", "303", "-H", "783")
    out = []
    for a, b in zip(left, right):
        out += clean_lines(a) + [""] + clean_lines(b) + [""]
    return out


def parse_adversaries(pdf, words):
    lines = adversary_lines(pdf)
    start = next(i for i, l in enumerate(lines) if l.strip() == "Action" and lines[i + 1].strip() == "Adversaries")
    lines = lines[start:]
    blocks, consumed = [], set()
    for i, l in enumerate(lines):
        if "Abilities (Focuses)" not in l:
            continue
        j = i - 1
        while not lines[j].strip():
            j -= 1
        end = next((k for k in range(i, len(lines)) if lines[k].strip().startswith("Threat:")), None)
        if end is None:
            continue
        blocks.append((lines[j].strip(), j, i, end))
        consumed.update(range(j, end + 1))
    section, desc, cur, out = None, {}, None, []
    for i, l in enumerate(lines):
        s = l.strip()
        if i in consumed:
            continue
        if s in ADV_SECTIONS and lines[i + 1].strip() == "Adversaries":
            section, cur = s, None
            continue
        if s == "Adversaries":
            cur = None
            continue
        nxt = next((x.strip() for x in lines[i + 1:i + 3] if x.strip()), "")
        if s and nxt.startswith("AKA:") and not s.startswith("AKA:"):
            cur = desc.setdefault(s, {"section": section, "lines": []})
            continue
        if cur is not None and s:
            cur["lines"].append(s)
    for name, j, i, end in blocks:
        b = lines[j:end + 1]
        a = parse_statblock(name, b, words)
        a["section"] = desc.get(name, {}).get("section") or section
        d = desc.get(name, {}).get("lines", [])
        aka = d[0][4:].strip() if d and d[0].startswith("AKA:") else ""
        # the AKA line may wrap before the prose starts; the prose starts at the first line after a gap, so keep it simple
        a["aka"], a["flavor"] = aka, paragraph(d[1:] if aka else d, words)
        out.append(a)
    return out


def parse_statblock(name, b, words):
    a = {"name": name, "abilities": {}, "focuses": {}, "weapons": [], "qualities": [], "threat": ""}
    mode, entries = None, []
    for l in b:
        s = l.strip()
        if mode == "abilities":
            if re.match(r"^Speed\s+Health\s+Defense", s):
                mode = None
            elif s:
                # a focus list can wrap, and the score can then sit on a line of its own
                if re.fullmatch(r"[\u2013\-]?\d+", s):
                    entries[-1]["num"] = num(s)
                    continue
                m = re.match(r"^(?:([\u2013\-]?\d+)\s+)?(" + "|".join(ABILITIES) + r")\b(.*)$", s)
                if m:
                    entries.append({"num": num(m.group(1)) if m.group(1) else None, "abl": m.group(2), "text": m.group(3)})
                elif entries:
                    entries[-1]["text"] += " " + s
                continue
        if "Abilities (Focuses)" in l:
            mode, entries = "abilities", []
            continue
        if re.match(r"^Speed\s+Health\s+Defense", s):
            mode = "derived"
            continue
        if mode == "derived":
            t = s.split()
            mm = re.match(r"(\d+)I/(\d+)B\+(\d+)", t[7]) if len(t) >= 10 else None
            if mm:
                a["speed"] = num(t[0])
                a["health"] = [num(x) for x in t[1:4]]
                a["defense"] = [num(x) for x in t[4:7]]
                a["impact"], a["ballistic"] = int(mm.group(1)), int(mm.group(2))
                a["toughness"] = [int(mm.group(3)), num(t[8]), num(t[9])]
            else:  # "N/A" speed/defense or unusual layouts
                a["speed"], a["health"], a["defense"] = 0, [num(x) for x in t[1:4]], [0, 0, 0]
                mm = re.search(r"(\d+)I/(\d+)B\+(\d+)\s+\+(\d+)\s+\+(\d+)", s)
                a["impact"], a["ballistic"] = int(mm.group(1)), int(mm.group(2))
                a["toughness"] = [int(mm.group(3)), int(mm.group(4)), int(mm.group(5))]
            mode = "weapons"
            continue
        if re.match(r"^Weapon\s+Attack Roll", s):
            mode = "weapons"
            continue
        if s == "Special Qualities":
            mode = "quality"
            continue
        if mode == "weapons":
            m = WEAPON_LINE.match(l)
            if m:
                a["weapons"].append(m.groupdict())
        elif mode == "quality":
            if s.startswith("Threat:"):
                a["threat"] = s[len("Threat:"):].strip()
            elif s:
                if LABEL.match(s) or not a["qualities"]:
                    a["qualities"].append([s])
                else:
                    a["qualities"][-1].append(s)
    for e in entries:
        key = ABILITIES[e["abl"]]
        a["abilities"][key] = e["num"] or 0
        inner = ",".join(re.findall(r"\(([^)]*)\)", e["text"]))
        a["focuses"][key] = [f.strip() for f in inner.split(",") if f.strip()]
    if not a["threat"]:
        a["threat"] = next((x.strip()[7:].strip() for x in b if x.strip().startswith("Threat:")), "")
    a["qualities"] = [paragraph(q, words) for q in a["qualities"]]
    return a


def embedded(actor_id, name, type_, system, img):
    iid = make_id(actor_id, name + type_)
    return {"name": name, "type": type_, "img": ICON + img, "_id": iid, "_key": f"!actors.items!{actor_id}.{iid}",
            "flags": {}, "effects": [], "folder": None, "sort": 0, "_stats": SYSTEM_STATS, "system": system}


def build_adversaries(pdf, words):
    pack = Pack("adversaries", "Actor")
    focus_list = parse_focuses(pdf, words)
    by_abl = {}
    for f in focus_list:
        by_abl.setdefault(ABILITIES[f["ability"]], []).append(f["name"])
    book_weapons = {w["name"].lower(): w for w in parse_weapons(pdf)}
    modes = ("gritty", "pulp", "cinematic")
    for a in parse_adversaries(pdf, words):
        aid = make_id("adversaries", a["name"])
        dex = a["abilities"].get("dex", 0)
        health, defense, tough = a["health"], a["defense"], a["toughness"]
        features = (f"<p><strong>AKA:</strong> {esc(a['aka'])}</p>" if a["aka"] else "") + (p(a["flavor"]) if a["flavor"] else "")
        for q in a["qualities"]:
            m = LABEL.match(q)
            features += f"<p><strong>{esc(q[:m.end() - 2])}:</strong> {esc(q[m.end():])}</p>" if m else p(q)
        system = {
            "threat": a["threat"], "speed": {"base": a.get("speed", 0)},
            "abilities": {k: {"value": v} for k, v in a["abilities"].items()},
            "defense": {"base": 10, "mod": (defense[0] - 10 - dex) if defense[0] else 0},
            "armor": {"impact": a["impact"], "ballistic": a["ballistic"]},
            "health": {"set": health[1], "value": health[1]},
            "gameMode": {"selected": "pulp", "specs": {
                m: {"health": health[i], "healthCur": health[i], "defense": defense[i] - defense[0], "toughness": tough[i]}
                for i, m in enumerate(modes)}},
            "features": features, "statBlockView": True,
        }
        items = []
        for abl, names in a["focuses"].items():
            for n in names:
                full = next((x for x in by_abl.get(abl, []) if x == n or x == n + " Weapons" or x.startswith(n + " ")), n)
                items.append(embedded(aid, full, "focus", {"useAbl": abl, "initialValue": 2, "longDesc": ""}, "gift-of-knowledge.svg"))
        for w in a["weapons"]:
            n, dtype = re.match(r"(\d+)d(\d+)", w["dmg"]).groups()
            extra = re.search(r"\+(\d+)$", w["dmg"])
            book = book_weapons.get(w["name"].lower()) or book_weapons.get(re.sub(r" Grenade$", "", w["name"]).lower())
            ranged = bool(book and book["ranged"])
            items.append(embedded(aid, w["name"], "weapon", {
                "longDesc": p(f"Attack roll {w['atk']}, damage {w['dmg']}."),
                "useAbl": "acc" if ranged else "fight", "useFocus": book["focus"].lower() if book else "", "nrDice": n,
                "diceType": dtype, "extraValue": int(extra.group(1)) if extra else 0, "dmgAbl": "no-abl",
                "dmgType": "stun" if book and book["sw"] == "S" else "wound", "dmgSource": "normalDamage",
                "damageFormula": w["dmg"], "weight": 0, "cost": 0, "quantity": 1, "equiped": True,
                **({"ranged": True} if ranged else {}),
            }, "fist.svg"))
        did = make_id("adversaries", a["name"])
        pack.docs.append({
            "name": a["name"], "_id": did, "_key": f"!actors!{did}",
            "folder": pack.folder(f"{a['section']} Adversaries", 100000 * (len(pack.folders) + 1)) if a["section"] else None,
            "sort": 0, "flags": {"core": {"sheetClass": "age-system.ageSystemSheetCharStatBlock"}}, "effects": [],
            "_stats": SYSTEM_STATS, "type": "char", "img": ADV_ICON, "system": system, "items": items,
            "prototypeToken": {"name": a["name"], "actorLink": False}, "ownership": {"default": 0},
        })
    return pack


# --------------------------------------------------------------------------- stunts

STUNT_SECTIONS = {
    "General Combat Stunts": "General Combat", "Melee Stunts": "Melee", "Firearms Stunts": "Firearms",
    "Grappling Stunts": "Grappling", "Anti-Vehicle Stunts": "Anti-Vehicle", "General Exploration Stunts": "General Exploration",
    "Infiltration Stunts": "Infiltration", "Investigation Stunts": "Investigation", "General Social Stunts": "General Social",
    "Attitude Stunts": "Attitude", "Membership & Reputation Stunts": "Membership & Reputation",
}
COST = re.compile(r"^(\d+)(?:[–\-](\d+)|(\+))?$")
COST_CELL = re.compile(r"^\s{0,16}(\d+(?:[\u2013\-]\d+|\+)?)(?:\s{2,}(.*))?$")
ROW_END = (".", "!", "?", ")", "\u201d", "\"", "\u2019", "]")
STUNT_NAME = re.compile(r"^(?:\((?P<core>Core)\) )?(?P<name>[A-Z][^:]{1,50}?): (?P<rest>.*)$")


def parse_stunts(pdf, words):
    """Stunt tables are read in layout mode: the SP cost sits in the left cell (often vertically centred on its row)."""
    rows, section = [], None
    for page in pdftotext(pdf, 83, 91, "-layout"):
        in_table, cur = False, None
        for l in page.splitlines():
            s = l.strip()
            if s in STUNT_SECTIONS:
                section = STUNT_SECTIONS[s]
                continue
            if re.match(r"^SP Cost\s+Stunt$", s):
                in_table, cur = True, None
                continue
            if not in_table:
                continue
            if not s:
                in_table = False
                continue
            cm = COST_CELL.match(l)
            left, right = (cm.group(1), (cm.group(2) or "").strip()) if cm else ("", s)
            if not cm and len(l) - len(l.lstrip()) < 8:
                in_table = False  # prose outside the table
                continue
            m = STUNT_NAME.match(right)
            complete = cur is None or cur["lines"][-1].endswith(ROW_END)
            if m and complete and len(m.group("name").split()) <= 8:
                cur = {"name": m.group("name"), "core": bool(m.group("core")), "section": section, "cost": None,
                       "lines": [m.group("rest")]}
                rows.append(cur)
            elif right and cur is not None:
                cur["lines"].append(right)
            if left and cur is not None and cur["cost"] is None:
                cur["cost"] = COST.match(left)
    out = []
    for r in rows:
        c = r["cost"]
        if c is None:
            print(f"warning: no SP cost found for stunt {r['name']!r}", file=sys.stderr)
            continue
        lo = int(c.group(1))
        r["lo"], r["hi"] = lo, int(c.group(2)) if c.group(2) else (lo + 2 if c.group(3) else lo)
        r["desc"] = paragraph(r["lines"], words)
        out.append(r)
    return out


def build_stunts(pdf, words):
    pack = Pack("stunts")
    for r in parse_stunts(pdf, words):
        variable = r["hi"] != r["lo"]
        desc = ("<em>Core stunt.</em> " if r["core"] else "") + esc(r["desc"])
        pack.item(r["name"], "stunts", {
            "longDesc": f"<p>{desc}</p>", "shortDesc": "", "type": r["section"], "stuntPoints": r["lo"],
            "stuntPointsMax": r["hi"], "costType": "variable" if variable else "fixed",
        }, "split-cross.svg", folder=r["section"])
    return pack


# --------------------------------------------------------------------------- main

BUILDERS = {"focuses": build_focuses, "talents": build_talents, "weapons": build_weapons, "armor": build_armor,
            "stunts": build_stunts, "vehicles": build_vehicles, "equipment": build_equipment,
            "adversaries": build_adversaries}
NEEDS_WORDS = {"focuses", "talents", "stunts", "vehicles", "equipment", "adversaries"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "packs" / "_source"))
    ap.add_argument("--only", default=",".join(BUILDERS))
    a = ap.parse_args()
    pdf = Path(a.pdf)
    if not pdf.is_file():
        sys.exit(f"PDF not found: {pdf}")
    words = Words("\n".join(pdftotext(pdf, 1, 194)))
    for name in a.only.split(","):
        b = BUILDERS[name]
        b(pdf, words).write(a.out) if name in NEEDS_WORDS else b(pdf).write(a.out)


if __name__ == "__main__":
    main()
