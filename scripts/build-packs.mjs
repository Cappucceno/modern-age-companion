// Compile packs/_source/<pack> (JSON) into LevelDB packs/<pack>, as listed in module.json.
import { compilePack } from "@foundryvtt/foundryvtt-cli";
import { readFileSync, existsSync, readdirSync } from "node:fs";

const { packs } = JSON.parse(readFileSync("module.json", "utf8"));
for (const { name } of packs) {
  const src = `packs/_source/${name}`;
  // packs without any source document stay uncompiled
  if (!existsSync(src) || !readdirSync(src).some((f) => f.endsWith(".json"))) continue;
  await compilePack(src, `packs/${name}`, { log: true });
}
