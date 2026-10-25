// @ts-check
import { defineConfig } from 'astro/config';

// Served from GitHub Pages under the custom domain in public/CNAME.
// https://docs.astro.build/en/guides/deploy/github/
export default defineConfig({
  site: 'https://homelab.aemreusta.com',
  // Astro 7 defaults to JSX whitespace rules, which drop the space between a
  // word and an inline element on the next source line ("and<b>").
  compressHTML: true,
});
