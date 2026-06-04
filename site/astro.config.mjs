import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

// Fully static, prerendered site. Data is baked in at build time from
// site/src/data/*.json (written by `python -m pipeline.run`).
export default defineConfig({
  site: 'https://mpoftheweek.com',
  trailingSlash: 'ignore',
  build: { format: 'directory' },
  integrations: [sitemap()],
});
