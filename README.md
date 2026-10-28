# Modern AGE Compendium for AGE System

A Foundry VTT v14 **module** for the [AGE System (unofficial)](https://github.com/vkdolea/age-system) (`age-system` 5.0.0+),
meant to carry the compendium content of **Modern AGE**, converted to AGE System item and actor types.
It follows the same layout as the [Expanse compendium](https://github.com/Cappucceno/expanse-age-compendium).

**Status: work in progress.** Converted so far from the *Modern AGE Basic Rulebook*: 89 ability focuses, 54 talents and
specializations, 114 stunts, 40 weapons, 6 armors and 2 shields. Still empty: equipment, vehicles, adversaries,
character creation (backgrounds, professions, drives), roll tables and the journals.

| Compendium | Planned content (AGE type) |
|---|---|
| Talents / Ability Focuses / Stunts | `talent`, `focus`, `stunts` items |
| Weapons / Armor / Equipment | `weapon` and `equipment` items |
| Vehicles / Sample Adversaries | `spaceship`/vehicle and `char` actors |
| Character Creation Guide / Tables, GM Chapters, Rules Reference / Tables | journals and roll tables |

## Building
Requires `poppler-utils` (`pdftotext`), Python 3 and Node.
```bash
npm install
python3 scripts/convert.py "/path/to/Modern_AGE_Basic_Rulebook.pdf"   # PDF -> packs/_source/*.json (not stored in the repo)
npm run build                                                         # packs/_source -> packs/<pack> (LevelDB)
```
`--only focuses,talents,weapons,armor,stunts` converts a subset. The converter reads PDF pages directly, so it is tied to the
GRR6301 edition of the book.

Pack sources go in `packs/_source/<pack>/` (one JSON per document, `_folder-*.json` for folders, as in the Expanse module).
Compiled LevelDB packs go in `packs/<pack>/`.

## Install
Manifest URL (once a release `v0.1.0` with `module.zip` exists):
`https://raw.githubusercontent.com/Cappucceno/modern-age-compendium/main/module.json`

Manual install: put this folder in `Data/modules/` **named `modern-age-compendium`**.

## Notes
- Not tested inside Foundry. Empty packs are declared in `module.json`; Foundry creates them on first load.
- See [NOTICE.md](NOTICE.md) for copyright and credits.
