---
name: PRTG-NATS
description: The operating interface for a fleet of PRTG multi-platform probes.
colors:
  signal-blue: "oklch(52% 0.190 257)"
  signal-blue-dark: "oklch(66% 0.165 257)"
  signal-blue-soft: "oklch(97.2% 0.020 256)"
  cool-paper: "oklch(98.5% 0.004 251)"
  card-white: "oklch(99.4% 0.003 250)"
  inset-mist: "oklch(94% 0.006 252)"
  graphite-ink: "oklch(20% 0.016 260)"
  slate-body: "oklch(42.5% 0.018 257)"
  slate-meta: "oklch(53.5% 0.016 256)"
  hairline: "oklch(86.5% 0.009 253)"
  control-edge: "oklch(77.5% 0.012 254)"
  night-paper: "oklch(20% 0.016 260)"
  night-surface: "oklch(24.5% 0.016 259)"
  night-inset: "oklch(34.5% 0.016 258)"
  night-ink: "oklch(98.5% 0.004 251)"
  night-body: "oklch(77.5% 0.012 254)"
  night-meta: "oklch(64.5% 0.015 255)"
  night-hairline: "oklch(29.5% 0.016 258)"
  night-hover: "oklch(29.5% 0.016 258)"
  healthy-green: "oklch(48% 0.112 156)"
  caution-amber: "oklch(53% 0.125 72)"
  fault-red: "oklch(50.5% 0.190 27)"
typography:
  headline:
    fontFamily: "Space Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.75rem
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Space Grotesk Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.5rem
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.25rem
    fontFeature: "tnum"
  meta:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1rem
  value-mono:
    fontFamily: "JetBrains Mono Variable, ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1rem
  table-head:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, -apple-system, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 500
    lineHeight: 1rem
rounded:
  inset: "4px"
  control: "6px"
  card: "10px"
spacing:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "16px"
components:
  button-primary:
    backgroundColor: "{colors.signal-blue}"
    textColor: "{colors.card-white}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  button-secondary:
    backgroundColor: "{colors.card-white}"
    textColor: "{colors.graphite-ink}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  button-secondary-hover:
    backgroundColor: "{colors.inset-mist}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.slate-body}"
    rounded: "{rounded.control}"
    padding: "0 8px"
    height: "28px"
  button-ghost-hover:
    backgroundColor: "{colors.inset-mist}"
    textColor: "{colors.graphite-ink}"
  button-danger:
    backgroundColor: "{colors.fault-red}"
    textColor: "{colors.card-white}"
    rounded: "{rounded.control}"
    padding: "0 12px"
    height: "32px"
  badge-healthy:
    textColor: "{colors.healthy-green}"
    rounded: "{rounded.inset}"
    padding: "0 6px"
    height: "20px"
  card:
    backgroundColor: "{colors.card-white}"
    rounded: "{rounded.card}"
    padding: "16px"
  input:
    backgroundColor: "{colors.card-white}"
    textColor: "{colors.graphite-ink}"
    rounded: "{rounded.control}"
    padding: "6px 10px"
  detail-row:
    textColor: "{colors.graphite-ink}"
    padding: "6px 0"
    height: "40px"
---

<!-- The DESIGN.md format repeats "Named Rules" per section and ends the
     Do/Don't headings with a colon, and opens with the North Star in bold;
     tools parse those exact lines. -->
<!-- markdownlint-disable MD024 MD026 MD036 -->

# Design System: PRTG-NATS

## Overview

**Creative North Star: "The Control Room"**

PRTG-NATS is the control room for a fleet of probes spread over sites the
operator cannot see. Its job is the job of a good control room: the state of
everything is readable at a glance, and every intervention is deliberate,
labelled and confirmed. The interface stays quiet so that the one thing that
is wrong can be loud.

Density is that of a working tool, not a showcase. Rows of label and value,
machine values in monospace, states as small flat tints, actions at the right
edge of the row they belong to. Surfaces are flat and separated by hairlines,
not lifted by shadows. One signal blue marks what can be done next; amber and
red are reserved for what needs attention. Light and dark are equal citizens,
because the same people check the fleet at a desk in the day and on a phone at
night.

It is explicitly not a generic AI admin template (no gradients, glows, pill
buttons, emoji icons or coloured tile dashboards), not a copy of PRTG's own
look, and not playful consumer design (no illustrations, mascots or motion for
its own sake).

**Key Characteristics:**

- Flat surfaces, hairline borders, no decorative depth.
- One accent, used for the primary action, focus and running work only.
- States are text with a tint, never shaped like buttons.
- Every machine value - id, address, version, checksum - is monospaced.
- Actions sit at the right edge of their row; rare and destructive ones wait
  in a menu.

## Colors

A cool graphite neutral ramp carries almost everything; colour appears only
where it means something.

### Primary

- **Signal Blue** (light `{colors.signal-blue}`, dark
  `{colors.signal-blue-dark}`): the primary button, the focus ring, the active
  tab underline, a running job, links that navigate. Its pale wash
  (`{colors.signal-blue-soft}`) backs the accent badge and text selection.

### Neutral

- **Cool Paper** / **Night Paper**: the page ground behind cards.
- **Card White** / **Night Surface**: cards, tables, dialogs, menus, inputs.
- **Inset Mist** / **Night Inset**: table heads, skeletons, the pressed step
  of a quiet control.
- **Hover** (Inset Mist / **Night Hover**): the fill of a quiet control, a
  navigation entry or a list row under the pointer. At night it sits halfway
  between surface and inset, so a hover is felt rather than lit.
- **Graphite Ink** / **Night Ink**: headings and values.
- **Slate Body** / **Night Body**: running text and quiet controls at rest.
- **Slate Meta** / **Night Meta**: row labels, timestamps, hints.
- **Hairline** / **Night Hairline**: card borders and row dividers. At night
  a step below Night Inset: a divider must not be as loud as a control's
  edge.
- **Control Edge**: the outline of inputs and secondary buttons.

### Status

- **Healthy Green**: connected, active, matching. Never decoration.
- **Caution Amber**: outdated, not yet registered, needs a human step.
- **Fault Red**: unreachable, failed, and the danger text of destructive menu
  items. A solid red fill appears only on the confirm button of a destructive
  dialog.

### Named Rules

**The One Signal Rule.** Signal Blue covers under 5% of any screen: one
primary action per view, the focus ring, the active state. A second primary
button on a screen is a bug - with one exception: the submit button of a
form is primary within that form.

**The Meaning-Only Rule.** Green, amber and red say healthy, attention and
fault. They are never used to decorate, to group, or to make a screen look
alive.

**The Equal Night Rule.** Every surface is designed for light and dark at
once; only the semantic aliases flip, the ramps never do.

## Typography

**Display Font:** Space Grotesk (with ui-sans-serif, system-ui)
**Body Font:** Inter (with ui-sans-serif, system-ui, -apple-system)
**Label/Mono Font:** JetBrains Mono (with ui-monospace, SF Mono, Menlo)

**Character:** A slightly technical grotesk for titles, a neutral workhorse
for reading, and a mono for everything a machine produced. The three never
trade roles.

### Hierarchy

- **Headline** (600, 1.125rem, 1.75rem): the page title, one per screen.
- **Title** (600, 1rem, 1.5rem): card headers. Section headings inside a
  card or dialog (h3) are 600 at body size; no heading is set smaller than
  the text it heads.
- **Body** (400, 0.875rem, 1.25rem): values, running text, button labels on
  medium buttons.
- **Meta** (400, 0.75rem, 1rem): hints under values, small-button labels,
  timestamps.
- **Value Mono** (400, 0.75rem): ids, addresses, versions, checksums,
  commands.
- **Table Head** (500, 0.75rem, 1rem, Slate Meta): table column heads, in
  sentence case on the card surface with a hairline below - no grey band, no
  capitals. The name of a value outside a table, a group heading in a form,
  the step name in a job log line and the title of a copy block are sentence
  case in Meta.

### Named Rules

**The Quiet Head Rule.** A column head names the data; it never outshouts
it. Tracked mono capitals on a grey band were the loudest thing on every
list and are gone; nothing in the interface is set in capitals.

**The Machine Value Rule.** If a machine produced it - an id, an address, a
hash, a version - it is set in mono. If a person reads it as language, it is
not.

**The Tabular Rule.** Figures are tabular everywhere, so a column of numbers
does not shift by a pixel per row.

## Layout

A fixed left rail (224px from the medium breakpoint, 56px folded to its icons,
a menu button below the breakpoint), a 48px top bar level with the rail's
head, for identity and preferences, and a content column with 16px
between blocks. Detail pages stack cards; from 1280px two cards sit side by
side, and only there, because a half-width card must still hold a row's value
and actions on one line.

The detail row is the unit of every detail page: a label column (6.5–11rem),
the value, and the row's actions at the right edge, at least 40px tall whether
it has actions or not. On a phone the label moves above its value instead of
squeezing it. Toolbars wrap under the page title rather than shortening it.

### Named Rules

**The One Line Rule.** A value and its actions share one line where there is
room, and wrap together where there is not. They are never spread over the
card.

## Elevation & Depth

Flat by default. Cards are separated from the paper by a hairline border, not
a shadow. The only shadow in the system belongs to floating layers that sit
above content - the action menu - and exists to separate them from what they
cover. Dialogs float on a 40% black scrim.

### Named Rules

**The Flat-By-Default Rule.** Nothing at rest casts a shadow. Depth appears
only for a layer that floats over other content.

## Shapes

Drawn with a ruler, no pills. Three radii, each with one job: 4px for small
insets (badges, focus outline), 6px for controls (buttons, inputs, selects),
10px for containers (cards, dialogs, menus). Borders are 1px hairlines; status
colours never draw a border.

## Components

### Buttons

Restrained and precise: a button reads as a control, never as decoration.

- **Shape:** gently squared corners (6px); fixed height per size - small 28px,
  medium 32px - so a row of buttons lines up whatever the labels say.
- **Primary:** Signal Blue fill, paper text. At most one per view; the probe
  toolbar promotes "update helper" to primary only when the probe needs it.
- **Secondary:** card surface, Control Edge outline, ink text; hover fills
  with Inset Mist. The everyday action.
- **Ghost:** no fill, no border, slate text; hover fills with Hover and
  darkens the text. Row actions and the quietest toolbar action.
- **Danger:** Fault Red fill. Only the confirm button of a destructive dialog.
- **Icons:** Lucide, 1.75 stroke, 14px on small and 16px on medium buttons,
  always before the label. An icon-only button carries its label as tooltip
  and accessible name.
- **States:** colour change over 100ms on hover, a darker step on press,
  45% opacity and no pointer events when disabled, the global 2px Signal Blue
  focus outline with a 2px offset. Loading puts a spinner in the icon's place
  and disables the button, so the label does not move and there is no second
  click.

### Action Menu

The overflow for actions that are rare or destroy something: a ghost
icon-button with a horizontal ellipsis opens a 10px-radius surface with a
hairline border and the system's only shadow. Items are 32px rows with an
icon; destructive items come last, after a divider, in Fault Red text, and
open their own confirmation. Arrow keys move, Escape closes and returns focus,
and the menu is pinned to the viewport so no card can clip it.

### Filter Chips

A filter that is on or off, never a button variant: 28px tall, 6px radius,
small medium-weight text. Off, it is quiet meta text that fills with Inset
Mist on hover; on, it sits on Inset Mist with a Control Edge hairline and ink
text. The signal colour stays for actions, so a row of filters never reads
as a row of next steps.

### Back Link

The way from a detail page to its list: meta-size slate text with a 14px
Lucide arrow, ink on hover. No typed arrow glyphs.

### More Link

The way from a summary to the full list, "Show all" in a card header: meta
text in Slate Meta with a trailing 14px arrow that moves 2px on hover, ink on
hover. It navigates, so it carries neither a fill nor the signal colour.

### Badges

A state, never an action: flat tinted text on its soft background, 20px tall,
4px radius, 11px medium weight, sentence case, no border and no hover. A
neutral, healthy, caution, fault and accent tone, one meaning each.

Badges are for the state of the one object a page is about, beside its
title, and for flags next to a row's state ("helper outdated"). The state of
a row in a table is Status Text instead.

### Status Text

A row's state in a table or a detail row: an 8px status dot and the word in
Slate Body, body size. A job's state uses its Lucide icon in the status
colour in place of the dot; a running job's icon turns. A column of badges
was a stripe of tinted boxes; a column of dots reads as a column of states.

### Cards

- **Corner Style:** 10px.
- **Background:** Card White / Night Surface on the paper.
- **Shadow Strategy:** none, see Elevation & Depth.
- **Border:** 1px hairline.
- **Internal Padding:** 16px; the header is a 10px by 16px band with a
  hairline under it and the card title in Title type.

### Inputs / Fields

- **Style:** card surface, Control Edge outline, 6px radius, 6px by 10px
  padding, body type; mono for command-like fields.
- **Label row:** the label in ink, then - where the machine knows the field
  by another name - that name in Value Mono, Slate Meta; then a muted
  "optional" on the exception. Required is the norm and carries no mark.
- **Help:** behind an info button (Lucide `Info`, 14px, a 44px hit area)
  at the end of the label row, never as a paragraph under the control. The
  control references the text through `aria-describedby`, so a screen reader
  gets it without opening anything.
- **Below the control:** only facts about the value - an error, or the state
  of what is stored ("stored, leave empty to keep it", a fingerprint).
- **Focus:** the global Signal Blue outline.
- **Error / Disabled:** a Fault Red line of meta text under the field; 50%
  opacity when disabled.

### Popover

The panel behind an info button or a compact table cell: card surface,
hairline border, 10px radius, the floating-layer shadow, body text at most
20rem wide (24rem for a hover card). It opens on hover after 250ms with a
mouse, on keyboard focus, and on a click or tap, which also pins it; Escape,
a press outside, the focus moving on, or the mouse leaving an unpinned panel
close it. Below 640px it does not float: it expands inline under the thing
it explains, on Inset Mist, because a floating panel on a phone covers the
field being filled in. A plain `title` tooltip is not a substitute - it never
opens for the keyboard or a thumb.

A table cell that would stack details shows one line - the name, a version,
a status dot - and puts the rest in a popover on that line: the tool column
of a probe's sensor table is the model.

### Disclosure

A `<details>` opens on a summary with a drawn chevron that turns 90° when
open, never the browser's own triangle.

### Navigation

The left rail lists object types in body type, each with its Lucide icon at
16px in Slate Meta; the same icon stands for the same kind of object
wherever it appears (`src/app/icons.ts`). Entries are 32px tall and fill with
Hover under the pointer. The active entry carries the Signal Blue wash, ink
text in medium weight and a Signal Blue icon - a place, not a button. Section
labels are Meta in sentence case, medium weight.
The rail folds to its icons; folded, a section label becomes a hairline, the
label stays in the link for screen readers and floats beside the icon on
hover and keyboard focus. The choice is remembered per browser.
Below the medium breakpoint the rail becomes a menu button in the top bar,
with 40px entries for a thumb.
Tabs are a row of text buttons over a hairline; the active one is ink with a
2px Signal Blue underline, and the row scrolls sideways on a phone.

### Card Figures

The figures of a dashboard card are rows, not tiles: the name on the left in
Slate Body, the value on the right in ink, medium weight, tabular, 32px per
row, so the values form one column. A status dot before the name says what
the figure means; where a row has a dot, every row in that list has one,
neutral at nought, so the names line up. A row whose figure has rows behind
it links to them and fills with Hover; a nought links nowhere.
A share of a whole - the fleet by state - is one 6px bar above the rows, in
the status colours, hidden from screen readers because the rows carry the
same numbers. No donuts, no sparklines without a history to draw.

### Compact Lists

A card's recent items are 40px rows on hairlines: a 16px icon, what happened
in ink and what it happened to in Slate Meta on the same line (under it on a
phone), and the time at the right edge in a fixed column. A job's state is
its icon in the status colour, named for screen readers and in the tooltip;
an audit event's icon is its object's, in Fault Red when it failed, with a
badge for the result only then.

### Detail Row (signature)

The workhorse of the control room: label in Slate Meta, value in ink, actions
as ghost buttons at the right edge, a hairline below. Frequent actions are
direct; rare or destructive ones go into the row's action menu.

## Do's and Don'ts

### Do:

- **Do** put a row's actions at its right edge as ghost buttons with a Lucide
  icon, and move rare or destructive ones into the row's action menu.
- **Do** confirm every destructive or secret-revealing step in a dialog; the
  solid red button lives only there.
- **Do** keep one primary button per view, and none when nothing is the
  obvious next step.
- **Do** set every machine value in mono and every figure tabular.
- **Do** design each surface in light and dark at once.

### Don't:

- **Don't** use gradients, glows, pill-shaped buttons, emoji icons or
  coloured tile dashboards.
- **Don't** imitate PRTG's own visual style; the tool works with PRTG and
  does not present itself as part of it.
- **Don't** add illustrations, mascots or motion for its own sake.
- **Don't** shape a state like a control: no border, capitals or hover on a
  badge.
- **Don't** place actions as loose text links beside or under a value.
- **Don't** make a badge clickable, or draw a selected filter as a primary
  button.
- **Don't** use typed glyphs (arrows, check marks) where an icon belongs.
- **Don't** cast a shadow on anything at rest.
