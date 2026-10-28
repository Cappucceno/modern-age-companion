// Compile packs/_source/<pack> (JSON) into LevelDB packs/<pack>, as listed in module.json.
import { compilePack } from "@foundryvtt/foundryvtt-cli";
import { readFileSync, existsSync } from "node:fs";

const { packs } = JSON.parse(readFileSync("module.json", "utf8"));
for (const { name } of packs) {
  const src = `packs/_source/${name}`;
  if (!existsSync(src)) continue;
  await compilePack(src, `packs/${name}`, { log: true });
}
