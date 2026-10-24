import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync } from 'node:fs';
import sharp from 'sharp';
mkdirSync('static/css', { recursive: true });
mkdirSync('static/js', { recursive: true });
mkdirSync('static/images', { recursive: true });
execFileSync(process.execPath, ['node_modules/@tailwindcss/cli/dist/index.mjs', '-i', 'assets/styles.css', '-o', 'static/css/app.css', '--minify'], { stdio: 'inherit' });
copyFileSync('node_modules/htmx.org/dist/htmx.min.js', 'static/js/htmx.min.js');
for (const [name, sizes] of [['hero-plyn', [640, 960, 1600]], ['product-plyn', [400, 800]]]) {
  for (const width of sizes) {
    await sharp(`assets/images/${name}.png`).resize({ width, withoutEnlargement: true }).webp({ quality: 82 }).toFile(`static/images/${name}-${width}.webp`);
  }
}
