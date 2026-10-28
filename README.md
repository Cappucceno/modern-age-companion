# Modern AGE Compendium for AGE System

A Foundry VTT v14 **module** for the [AGE System (unofficial)](https://github.com/vkdolea/age-system) (`age-system` 5.0.0+),
meant to carry the compendium content of **Modern AGE**, converted to AGE System item and actor types.
It follows the same layout as the [Expanse compendium](https://github.com/Cappucceno/expanse-age-compendium).

**Status: empty skeleton.** No game content has been added yet.

| Compendium | Planned content (AGE type) |
|---|---|
| Talents / Ability Focuses / Stunts | `talent`, `focus`, `stunts` items |
| Weapons / Armor / Equipment | `weapon` and `equipment` items |
| Vehicles / Sample Adversaries | `spaceship`/vehicle and `char` actors |
| Character Creation Guide / Tables, GM Chapters, Rules Reference / Tables | journals and roll tables |

Pack sources go in `packs/_source/<pack>/` (one JSON per document, `_folder-*.json` for folders, as in the Expanse module).
Compiled LevelDB packs go in `packs/<pack>/` and are not committed until content exists.

## Install
Manifest URL (once a release `v0.1.0` with `module.zip` exists):
`https://raw.githubusercontent.com/Cappucceno/modern-age-compendium/main/module.json`

Manual install: put this folder in `Data/modules/` **named `modern-age-compendium`**.

## Notes
- Not tested inside Foundry. Empty packs are declared in `module.json`; Foundry creates them on first load.
- See [NOTICE.md](NOTICE.md) for copyright and credits.
