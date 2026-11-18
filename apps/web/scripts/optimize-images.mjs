// Converts source photographs in assets/photos to AVIF and WebP at several widths and writes
// the manifest read by the Photo component plus public/images/CREDITS.md.
// Source files are named <slot>.jpg. assets/photos/credits.json maps a slot to
// { alt, photographer, source, license }.
import { readdir, readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

const root = path.resolve(import.meta.dirname, "..");
const input = path.join(root, "assets", "photos");
const output = path.join(root, "public", "images");
const widths = [640, 960, 1440, 2400];

await mkdir(output, { recursive: true });
let credits = {};
try {
  credits = JSON.parse(await readFile(path.join(input, "credits.json"), "utf8"));
} catch {
  /* no credits file yet */
}

const manifest = {};
for (const file of (await readdir(input)).filter((f) => /\.(jpe?g|png)$/i.test(f))) {
  const slot = path.parse(file).name;
  const meta = credits[slot];
  if (!meta) throw new Error(`Missing credit entry for ${file}`);
  const image = sharp(path.join(input, file));
  const { width = 0, height = 0 } = await image.metadata();
  if (width < 2400) throw new Error(`${file} is ${width}px wide; 2400px is the minimum`);
  const sizes = widths.filter((w) => w <= width);
  for (const w of sizes) {
    const resized = sharp(path.join(input, file)).resize({ width: w });
    await resized
      .clone()
      .avif({ quality: 55 })
      .toFile(path.join(output, `${slot}-${w}.avif`));
    await resized
      .clone()
      .webp({ quality: 72 })
      .toFile(path.join(output, `${slot}-${w}.webp`));
  }
  manifest[slot] = { width, height, sizes, alt: meta.alt };
}

await writeFile(
  path.join(root, "src", "content", "images.json"),
  JSON.stringify(manifest, null, 2) + "\n",
);
const lines = [
  "# Image credits",
  "",
  "Photographs are self-hosted. Each entry records the photographer and source page.",
  "",
];
for (const [slot, meta] of Object.entries(credits)) {
  lines.push(`- ${slot}: ${meta.photographer}, ${meta.source}, ${meta.license}`);
}
if (!Object.keys(credits).length)
  lines.push("No photographs are included yet. All current imagery is in-house SVG.");
await writeFile(path.join(output, "CREDITS.md"), lines.join("\n") + "\n");
console.log(`optimized ${Object.keys(manifest).length} photographs`);
