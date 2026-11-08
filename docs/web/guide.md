---
title: The user guide under /docs
role: developer
updated: 2026-11-08
---

# The user guide under /docs

Every installation serves a guide for the people who use it at
`https://<server>/docs` (German) and `/docs/en/` (English). It explains, task by
task, what someone can do with the platform: enroll probes, deploy sensors,
follow jobs and sensor logs, run iperf endpoints and the server itself. It is
not this documentation tree: nothing about the architecture, the security
model, the API or how the platform works inside belongs there.

## How it reaches an installation

The guide is a set of static pages generated from `web/frontend/guide/`.
`npm run build` runs `node guide/build.mjs --check` after Vite and writes them
to `dist/docs/`, so they travel in the same image and the same static volume as
the interface and arrive with every update. Caddy serves them from a block of
their own, without the fallback to `index.html`, and without a session: the
sign-in page links to the guide, and nothing in it needs protecting.

The generated pages are not kept in the repository; only their sources are.

```bash
cd web/frontend
npm run guide                         # pages into dist/docs, placeholders for missing images
node guide/build.mjs --check          # as in the build: a missing image fails
python3 -m http.server 5291 --directory dist   # then open http://localhost:5291/docs/
```

## How it is built

| Path | Content |
| --- | --- |
| `guide/articles/` | every article as data, one file per group; `index.mjs` sets the order |
| `guide/lib/text.mjs` | `L(de, en)` and the two markers resolved per language |
| `guide/lib/layout.mjs` | head, header with the language switch, sidebar, footer, role badges |
| `guide/build.mjs` | validates the articles, writes the pages, copies styles, fonts and images |
| `guide/static/` | `guide.css`, the two small scripts, the favicon and `illustrations/` |
| `guide/images/<lang>/` | screenshots of the interface, taken from the demo installation |
| `guide/demo/` | the demo installation the screenshots come from |
| `guide/shots/` | the list of screenshots and the script that takes them |

The guide uses the interface's own `tokens.css` and typefaces, so a change to
the palette reaches both. The proxy serves it under the interface's strict
Content-Security-Policy: nothing may be loaded from elsewhere, and nothing may
run inline. That is why the theme and the search are separate files.

## Writing an article

Every text is a pair, `L(de, en)`, so an article cannot be finished in one
language and forgotten in the other. Two markers inside a text are resolved
per language:

- `{ui:nav.probes}` is the label the interface shows for that translation key,
  set in bold and quotation marks. Every button, tab, field and message is
  named this way, never typed out: a renamed button renames itself in the
  guide, and a removed key breaks the build.
- `href="@enroll-a-probe"` links to another article by its slug. A link to a
  slug that does not exist breaks the build.

The wording follows [terminology.md](terminology.md), with the Du-Anrede in
German. `npm run i18n:check` applies the list of retired terms to the guide
too.

`role` names the lowest role an article needs; the page shows it and every
role above. Articles anyone may read show "All roles".

## The link from the interface

The navigation and the sign-in page link to the guide. The navigation link
carries the page the reader is on, `/docs/context/?path=/probes/new&lang=de`;
`guide.js` looks the path up in `context.json` and opens the matching article.
Which pages belong to an article is that article's `appPaths`: a path matches
itself and every page below it, `/probes/*` every page below `/probes` but not
the list itself, and the longest match wins.

The build reads the routes from `src/app/router.tsx` and fails when a page of
the interface belongs to no article, or when an app path names no page. A new
page therefore needs an entry in the `appPaths` of the article that explains
it.

## Screenshots

The images show a fictional installation: `nats.example.test`, five probes
named after cities, addresses from the documentation ranges. They are taken
from the demo installation in `guide/demo/`, never from a real one - a real
installation would leak host names, addresses and people into a page every
customer reads. How the demo works is described in `guide/demo/README.md`.

```bash
web/frontend/guide/demo/stop.sh --purge     # start from a fresh demo world
web/frontend/guide/demo/run.sh              # backend, NATS, seed, about two minutes
web/frontend/guide/demo/frontend.sh         # the interface against it
cd web/frontend
node guide/shots/take.mjs                   # every image, German and English
node guide/shots/take.mjs de probes-list    # single images
```

`guide/shots/shots.mjs` names, for every image, the account, the page, the
clicks before the picture and what to cut out. Buttons are found by the label
the interface shows, read from the locale files, so a renamed button fails the
script instead of producing a picture of the wrong screen. Some pictures fill
in forms without submitting them; a fresh demo world before a full run keeps
the history the pictures show the same. The script needs Google Chrome and
`cwebp`.

When a view of the interface changes, its screenshots are taken again. When a
language lacks an image, the page shows the German one.

## Illustrations

Each group has an illustration on the overview and above its first article,
under `guide/static/illustrations/`. They come from the image generation of the
Codex CLI, all in one style, and show no text at all: an image generator does
not set type reliably, so nothing with words is made that way. Explanations
with words are HTML in the article.

A new illustration starts with a description of the scene, followed by this
style:

> Style: a calm semi-realistic product illustration. Softly rendered, slightly
> stylized people with real human proportions (friendly, diverse, practical
> everyday work clothes, natural hands, calm faces; not photos, not toy
> figures, not cartoon mascots), combined with soft matte 3D objects that look
> like clay or frosted plastic. Gentle studio light from the upper left with
> soft long shadows, slightly isometric camera, rounded edges, minimal and
> tidy. Objects and furniture are mostly off-white and cool light grey with
> graphite details; a single accent colour, a clear signal blue #2B5BD7 with
> its light tint #C9D8FA, used sparingly for small highlights only. Clothing
> in muted cool neutral tones. Seamless cool near-white background #F7F8FA, no
> room walls or windows. Landscape 3:2. Screens and displays show only
> abstract shapes, bars and dots. Absolutely no text, letters, numbers, logos,
> icons with letters or watermarks anywhere.

One call makes one image, from an empty scratch directory:

```bash
codex exec -m gpt-6-astra --skip-git-repo-check --sandbox workspace-write -C <dir> \
  "Generate one image with your image generation tool and save it as bild.png in the current directory. Scene: … Style: …" < /dev/null
```

Without `< /dev/null`, `codex exec` waits for more input and hangs. Before an
image goes in, somebody checks that it shows no text or logo and that faces
and hands are not distorted. It is stored as WebP, 1600 pixels wide:

```bash
cwebp -q 80 -resize 1600 0 bild.png -o web/frontend/guide/static/illustrations/<name>.webp
```
