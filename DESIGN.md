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
  night-hairline: "oklch(34.5% 0.016 258)"
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
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.25rem
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
  label:
    fontFamily: "JetBrains Mono Variable, ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "0.6875rem"
    fontWeight: 400
    letterSpacing: "0.06em"
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
- **Inset Mist** / **Night Inset**: hover fill for quiet controls, table
  heads, skeletons.
- **Graphite Ink** / **Night Ink**: headings and values.
- **Slate Body** / **Night Body**: running text and quiet controls at rest.
- **Slate Meta** / **Night Meta**: row labels, timestamps, hints.
- **Hairline** / **Night Hairline**: card borders and row dividers.
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
button on a screen is a bug.

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
- **Title** (600, 0.875rem, 1.25rem): card headers.
- **Body** (400, 0.875rem, 1.25rem): values, running text, button labels on
  medium buttons.
- **Meta** (400, 0.75rem, 1rem): hints under values, small-button labels,
  timestamps.
- **Value Mono** (400, 0.75rem): ids, addresses, versions, checksums,
  commands.
- **Label** (400, 0.6875rem, 0.06em, uppercase): column headers and metadata
  keys in tables.

### Named Rules

**The Machine Value Rule.** If a machine produced it - an id, an address, a
hash, a version - it is set in mono. If a person reads it as language, it is
not.

**The Tabular Rule.** Figures are tabular everywhere, so a column of numbers
does not shift by a pixel per row.

## Layout

A fixed left rail (224px from the medium breakpoint, a menu button below it),
a thin top bar for identity and preferences, and a content column with 16px
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
- **Ghost:** no fill, no border, slate text; hover fills with Inset Mist and
  darkens the text. Row actions and the quietest toolbar action.
- **Danger:** Fault Red fill. Only the confirm button of a destructive dialog.
- **Icons:** Lucide, 1.75 stroke, 14px on small and 16px on medium buttons,
  always before the label. An icon-only button carries its label as tooltip
  and accessible name.
- **States:** colour change over 100ms on hover, a darker step on press,
  45% opacity and no pointer events when disabled, the global 2px Signal Blue
  focus outline with a 2px offset.

### Action Menu

The overflow for actions that are rare or destroy something: a ghost
icon-button with a horizontal ellipsis opens a 10px-radius surface with a
hairline border and the system's only shadow. Items are 32px rows with an
icon; destructive items come last, after a divider, in Fault Red text, and
open their own confirmation. Arrow keys move, Escape closes and returns focus,
and the menu is pinned to the viewport so no card can clip it.

### Badges

A state, never an action: flat tinted text on its soft background, 20px tall,
4px radius, 11px medium weight, sentence case, no border and no hover. A
neutral, healthy, caution, fault and accent tone, one meaning each.

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
- **Focus:** the global Signal Blue outline.
- **Error / Disabled:** a Fault Red line of meta text under the field; 50%
  opacity when disabled.

### Navigation

The left rail lists object types in body type; the active entry carries the
Signal Blue wash and text. Section labels use the uppercase Label style.
Below the medium breakpoint the rail becomes a menu button in the top bar.
Tabs are a row of text buttons over a hairline; the active one is ink with a
2px Signal Blue underline, and the row scrolls sideways on a phone.

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
- **Don't** cast a shadow on anything at rest.
