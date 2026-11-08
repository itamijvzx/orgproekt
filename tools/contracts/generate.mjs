import openapiTS, { astToString } from 'openapi-typescript';
import { readFile, writeFile } from 'node:fs/promises';
const input = new URL('../../frontend/openapi.json', import.meta.url);
const output = new URL('../../frontend/src/lib/schema.generated.ts', import.meta.url);
const result = astToString(await openapiTS(input, { alphabetize: true }));
if (process.argv.includes('--check')) {
  if (await readFile(output, 'utf8') !== result) {
    throw new Error('OpenAPI types are stale: run node tools/contracts/generate.mjs');
  }
  console.log('OpenAPI TypeScript contract is current');
} else {
  await writeFile(output, result);
  console.log('Generated frontend/src/lib/schema.generated.ts');
}
