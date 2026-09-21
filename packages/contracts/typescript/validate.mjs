import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemaPath = path.resolve(here, "../schema/horizon.schema.json");
const fixturesPath = path.resolve(here, "../fixtures");
const schema = JSON.parse(fs.readFileSync(schemaPath, "utf8"));
const ajv = new Ajv2020({ allErrors: true, strict: true });
addFormats(ajv);
const validate = ajv.compile(schema);

for (const name of fs.readdirSync(fixturesPath).filter((entry) => entry.endsWith(".json")).sort()) {
  const value = JSON.parse(fs.readFileSync(path.join(fixturesPath, name), "utf8"));
  if (!validate(value)) {
    console.error(`${name}: ${ajv.errorsText(validate.errors, { separator: "\n" })}`);
    process.exitCode = 1;
  } else {
    console.log(`valid ${name}`);
  }
}
