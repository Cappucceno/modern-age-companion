"""Reading order blocks (headings, paragraphs, lists, tables) of the Cyberpunk Slice, from the PDF layout (see prose.py)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prose  # noqa: E402

_CACHE = {}


def book(pdf):
    """(pages, vocabulary) of the whole book, loaded once."""
    if "pages" not in _CACHE:
        pages = prose.load(pdf)
        text = " ".join(re.sub(r"<[^>]+>", " ", e["html"]) for pg in pages.values() for e in pg)
        _CACHE.update(pages=pages, words=prose.Words(text))
    return _CACHE["pages"], _CACHE["words"]


def text(h):
    """Plain text of an HTML snippet, whitespace collapsed."""
    return re.sub(r"\s+", " ", prose.plain(h)).strip()


def blocks(pdf, first, last):
    """All blocks of PDF pages first..last as tuples: ('h', level, text) / ('p', html) / ('li', html) / ('table', rows).

    Paragraphs that continue across a column or page break are merged."""
    pages, words = book(pdf)
    out = []
    for n in range(first, last + 1):
        els = [e for e in pages[n] if e["top"] < prose.FOOTER_TOP and not prose._noise(e)]
        prose.fix_small_caps(els)
        out += prose.blocks_from_page(els, words)
    return prose.merge_paragraphs(out, words)


def entries(bl, level=3):
    """Group blocks into entries starting at each heading of `level`. Returns [(title, parent_h2, [blocks])].

    Blocks before the first heading of that level are returned with title None."""
    out, cur, parent = [], [None, None, []], None
    for b in bl:
        if b[0] == "h" and b[1] == 2:
            parent = b[2]
        if b[0] == "h" and b[1] == level:
            if cur[2] or cur[0]:
                out.append(tuple(cur))
            cur = [b[2], parent, []]
        else:
            cur[2].append(b)
    if cur[2] or cur[0]:
        out.append(tuple(cur))
    return out


def paras(bl):
    """Plain text of the paragraph and list blocks."""
    return [text(b[1]) for b in bl if b[0] in ("p", "li")]
