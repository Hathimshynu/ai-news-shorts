// Usage: node render.mjs <props.json> <out.mp4>
import {bundle} from '@remotion/bundler';
import {renderMedia, selectComposition} from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const [, , propsPath, outPath] = process.argv;
if (!propsPath || !outPath) {
  console.error('Usage: node render.mjs <props.json> <out.mp4>');
  process.exit(1);
}
const inputProps = JSON.parse(fs.readFileSync(propsPath, 'utf8'));

console.log('Bundling...');
const serveUrl = await bundle({
  entryPoint: path.join(here, 'src', 'index.ts'),
  publicDir: path.join(here, 'public'),
});

// Optional: point at an existing Chrome instead of Remotion's auto-downloaded one.
const browserExecutable = process.env.REMOTION_BROWSER || null;
const composition = await selectComposition({serveUrl, id: 'NewsShort', inputProps, browserExecutable});
console.log(`Rendering ${composition.durationInFrames} frames...`);

let last = -1;
await renderMedia({
  composition,
  serveUrl,
  codec: 'h264',
  crf: 23,
  audioBitrate: '128k',
  outputLocation: path.resolve(outPath),
  inputProps,
  browserExecutable,
  onProgress: ({progress}) => {
    const pct = Math.floor(progress * 10) * 10;
    if (pct !== last) {
      last = pct;
      console.log(`  ${pct}%`);
    }
  },
});
console.log('Done:', outPath);
