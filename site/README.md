# pi-homelab site

The single-page site for this repository, built with [Astro](https://astro.build/) and published to
GitHub Pages at <https://homelab.aemreusta.com/> by [`.github/workflows/pages.yml`](../.github/workflows/pages.yml).

| Path | Purpose |
| --- | --- |
| `src/pages/index.astro` | The page: stack, release pipeline, architecture and lessons |
| `src/pages/privacy.astro` | Privacy policy, also used by the `homelab-rclone` Google API client |
| `src/layouts/Base.astro` | Shared head, meta tags and footer |
| `src/styles/global.css` | All styling; no CSS framework |
| `public/CNAME` | Custom domain for GitHub Pages |

```sh
npm ci
npm run dev      # http://localhost:4321
npm run build    # static output in dist/
```

Requires Node.js 22.22 or later. The page is static: no cookies, analytics or client-side JavaScript.
