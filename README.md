# Modern AGE Companion Compendium for AGE System

A Foundry VTT v14 **module** for the [AGE System (unofficial)](https://github.com/vkdolea/age-system) (`age-system` 5.0.0+),
with the content of the **Modern AGE Companion** (GRR6304) converted to AGE System item types, roll tables and journals.
It is a companion to the [Modern AGE Compendium](https://github.com/Cappucceno/modern-age-compendium) (Basic Rulebook) and only holds Companion material.

**Status: work in progress, not tested in Foundry yet.** Converted so far:

| Compendium | Content |
|---|---|
| Companion Talents | 9 new talents, the Artificer item talent, 7 new specializations (Novice to Apex degrees) and the Grandmaster/Apex degrees of Intrigue, Knowledge and Tactical Awareness |
| Companion Stunts | 22 general, 14 environmental, 12 thematic, 7 battle-map, 14 intensity and 29 organization stunts (SP costs as items) |
| Organization Focuses | 35 organization focuses (`isOrg`) for Might, Wealth, Influence, Intrigue and Phenomena |
| Enhancements | 17 sample enhancements (two with Defense/Toughness modifiers) |
| Extraordinary Items | 6 sample items (Steelcoat with armor modifiers) |
| Explosives and Weapons | Grenade Launcher, Rocket Launcher and the Age of Sail weapons |
| Character Creation Guide / Tables | Chapter 1 text, 8 origin backgrounds, 7 peoples, and 20 roll tables (benefit tables, social class and updated background tables) |
| Rules Reference / Rules Tables | Chapters 2 to 7 as journals (with clean equipment quality tables) and the Organization Misfortunes table |
| Organizations | Organization rules and the 6 sample organizations |
| Campaign Events | Event progress, category and 6 event tables; 30 event listings with intensity levels and hooks are in the GM journals |
| Campaign and Genre Chapters | Chapter 8 (complications, serendipity, events) and the five genre chapters of Chapter 9 |

Journal text is rebuilt from the PDF by position and font (`scripts/prose.py`); complex tables and sidebars can come out imperfect
(the damage conversion tables of Chapter 3 are the main example), and stat blocks inside the genre chapters are left as text.

## Building
Requires `poppler-utils` (`pdftotext`, `pdftohtml`), Python 3 and Node.
```bash
npm install
python3 scripts/convert.py "/path/to/Modern_AGE_Companion.pdf"   # PDF -> packs/_source/*.json
npm run build                                                    # packs/_source -> packs/<pack> (LevelDB)
```
The converter reads PDF pages directly, so it is tied to the GRR6304 edition of the book.

## Install
Manifest URL (once release `v0.1.0` with `module.zip` exists):
`https://raw.githubusercontent.com/Cappucceno/modern-age-companion-compendium/main/module.json`

Manual install: put this folder in `Data/modules/` **named `modern-age-companion-compendium`**.

## Notes
- See [NOTICE.md](NOTICE.md) for copyright and credits. Do not make this repository public without permission to redistribute the book's content.
