# Dark-mode reading and inline annotation color research

**Date:** 2026-07-16
**Scope:** Dark palette for sustained Claread reading, including the multi-class
inline annotations on the analysis page. This is research and a token audit, not
an implementation specification.

## Decision brief

Dark mode should remain an available environment preference, not be described as
objectively better for long-form reading. The strongest directly relevant
polarity experiment found smaller pupils and better proofreading for positive
polarity (dark-on-light) than negative polarity (light-on-dark) [Piepenbrock,
Mayr & Buchner, 2014](https://pubmed.ncbi.nlm.nih.gov/25135324/) (DOI:
[10.1080/00140139.2014.948496](https://doi.org/10.1080/00140139.2014.948496)).
For a reader that nevertheless supports Dark, the sound objective is therefore
*controlled negative polarity*: neutral prose, a restrained luminance ladder,
and small-area semantic annotations rather than a white-on-black, all-colour
reading surface.

The screenshot's discomfort is consistent with its UI-white `#e8e8e8` being
used for continuous body text on a near-black stage. It is accessible, but
accessibility contrast is a floor, not evidence that the maximum available
contrast is the most comfortable long-session treatment. Keep the high-contrast
ink for headings, controls, and focus; introduce a dedicated, softer prose
ramp for continuous English text and translated text.

Inline analysis colours should **not** be removed. They communicate useful
different kinds of help. They should instead become a separate annotation
encoding system: each category has a low-area translucent `fill`, a
contrast-tested `ink` for a label/detail card, and a non-colour cue. Do not use
any annotation colour for global selected state, focus, or primary action.

## Evidence and its limits

### 1. Display polarity is conditional, not a universal dark-mode benefit

* In a proofreading experiment, participants had smaller pupils and better
  proofreading performance with positive polarity. The authors report results
  compatible with higher display luminance producing a smaller pupil and a
  sharper retinal image. This is directly relevant to text work, but it does
  not mean every reader must be light themed. It means Dark should not try to
  compensate by making all prose maximum-white. [Piepenbrock, Mayr & Buchner,
  *Ergonomics*, 2014](https://pubmed.ncbi.nlm.nih.gov/25135324/), DOI
  [10.1080/00140139.2014.948496](https://doi.org/10.1080/00140139.2014.948496).
* A 2024 negative-polarity, low-illumination study found text colour affected
  both eye-movement measures and self-reported fatigue, but also reports
  inconsistent subjective and objective measures and a restricted palette and
  illumination range. It is useful evidence against coloured *prose* and
  saturated red/green text, not a licence to set all body copy yellow.
  [Fan et al., *Sensors*, 2024](https://www.mdpi.com/1424-8220/24/11/3516), DOI
  [10.3390/s24113516](https://doi.org/10.3390/s24113516).
* In short reading passages, blue/red text-background combinations heightened
  accommodation; low-luminance-contrast white/yellow reduced reported
  legibility. There was no reading-speed difference in that short task. Treat
  this as support for neutral prose and against weak/chromatic text, not as an
  exact palette prescription. [Sethi & Ziat, *Vision Research*, 2019](https://pubmed.ncbi.nlm.nih.gov/31841707/),
  DOI [10.1016/j.visres.2019.11.006](https://doi.org/10.1016/j.visres.2019.11.006).
* A newer colour-comfort experiment with 20 participants found black backgrounds
  generally received higher subjective comfort for coloured text, and that
  comfort fell as text approached the background; hue was not significant in
  that experiment. This supports sufficient lightness separation and says hue
  alone is not the comfort lever. It does **not** establish a long-session
  comprehension result or a particular hex palette. [Li et al., *Vision
  Research*, 2025](https://pubmed.ncbi.nlm.nih.gov/39615413/), DOI
  [10.1016/j.visres.2024.108524](https://doi.org/10.1016/j.visres.2024.108524).

### 2. Accessibility is the non-negotiable floor

* WCAG 2.2 requires at least **4.5:1** for normal text and **3:1** for large
  text. W3C explains that 4.5:1 accounts for reduced acuity and contrast
  sensitivity; it is not an upper comfort target. [W3C Understanding 1.4.3](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum).
* Meaning cannot be conveyed with hue alone. Where annotations distinguish
  vocabulary, phrase, grammar, context, and structure, a label, icon, pattern,
  underline treatment, location/detail panel, or other additional cue is
  required. [W3C Understanding 1.4.1](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color).
* Meaningful graphical/state indicators—including focus indications and
  category chips—need at least **3:1** against their adjacent colour. Thin
  lines can appear weaker than their nominal computation, so token checks must
  use the actual composited fill/stage pair. [W3C Understanding 1.4.11](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html).

### 3. Annotation density and redundant encoding matter

* Guidance specifically for semi-automatic text analysis treats highlighting as
  a distinct visual encoding problem, rather than merely colouring words.
  [Strobelt et al., *IEEE TVCG*, 2016](https://pubmed.ncbi.nlm.nih.gov/26529715/),
  DOI [10.1109/TVCG.2015.2467759](https://doi.org/10.1109/TVCG.2015.2467759).
* In a comprehension experiment, relevant highlighting helped but irrelevant
  highlighting was worse than no highlighting. Claread should therefore not
  display every available class at equal salience in a dense viewport; make the
  active explanation stronger and the other classes quieter/filterable.
  [McDougald & Wogalter, *Applied Ergonomics*, 2014](https://pubmed.ncbi.nlm.nih.gov/23759792/),
  DOI [10.1016/j.apergo.2013.05.008](https://doi.org/10.1016/j.apergo.2013.05.008).

## Claread token audit: current Dark state

The current tokens declare canvas `#161616`, surface `#1e1e1e`, raised
`#2a2a2a`, UI ink `#e8e8e8`, muted `#9a9a9a`, and Reader paper `#1c1c1c`.
The screenshot confirms that the reading stage is very close to canvas while
body text often looks like UI ink.

The following ratios are WCAG 2 relative-luminance calculations against the
current Reader surface `#1c1c1c`; alpha fills must instead be calculated after
compositing over the real stage.

| Current category token | Value | Ratio on `#1c1c1c` | Consequence if used as small text |
|---|---:|---:|---|
| vocab amber | `#e4b000` | 8.53:1 | Text-safe by ratio; visually salient, so keep out of global current state. |
| phrase lavender | `#b9a8e6` | 7.96:1 | Text-safe by ratio; use sparingly as a label, not paragraph copy. |
| context blue | `#4c91c2` | 4.98:1 | Barely AA for small text; do not lower it for label ink. |
| grammar violet | `#746694` | 3.30:1 | Fails normal-text AA. Fill/icon only until a separate dark `grammar-ink` exists. |
| structure green | `#3c8c68` | 4.17:1 | Fails normal-text AA. Fill/icon only until a separate dark `structure-ink` exists. |
| UI ink | `#e8e8e8` | 13.91:1 | Reserve for headings, active controls, and short high-importance text. |

This does **not** mean amber/lavender should become the Reader's prose colour.
Their high contrast is useful in compact labels and icons; widespread chromatic
text competes with reading and makes the analysis page louder.

## Proposed Dark reading system (token direction, not yet values)

### A. Use a restrained four-surface ladder

| Role | Direction | Claread use |
|---|---|---|
| `canvas` | retain near-neutral `#161616` | app shell and unused surround |
| `reader-stage` | only slightly lighter than canvas, approximately `#191919` | continuous reading plane; no card or glow |
| `surface` | around current `#1e1e1e` | sidebar, toolbars, Ask chrome |
| `surface-raised` | around `#262626` | menu, input, citation/detail card, transient sheet |

The important constraint is monotonic elevation: a transient item is clearer
than the surface beneath it. It should not use gradients or blue/black glow.
Use borders and a restrained structural shadow only where a layer must detach.
The current Light `surface-raised #f2f2f2` is a recessed value, so a later
cross-theme pass should separate `surface-recessed` from actual elevation.

### B. Split UI ink from reading ink

| Proposed semantic role | Dark direction | Intended content |
|---|---|---|
| `ui-ink` | retain `#e8e8e8` | titles, actions, selected state, icons |
| `reading-ink` | neutral grey around `#d0d0d0` on reader stage | continuous English body text |
| `reading-ink-strong` | between body and UI ink, around `#dedede` | section title, selected sentence, key quote |
| `reading-muted` | neutral grey around `#b3b3b3` | translation and explanatory text |
| `ui-muted` | retain the lower UI metadata role | timestamps and inactive chrome only |

Indicative ratios on `#1c1c1c` are `#d0d0d0` 11.05:1 and `#b3b3b3`
8.13:1: both exceed AA/AAA while avoiding the visual weight of `#e8e8e8` for
every line. Values must be rendered and rechecked on the final stage rather
than adopted solely from this calculation.

### C. Treat annotation categories as fill + cue + detail, never prose colour

| Category | In-text treatment | Non-colour redundancy | Detail/label treatment | Change decision |
|---|---|---|---|---|
| Vocabulary amber | low-alpha amber fill; optional short solid underline | glossary/book icon; Vocabulary label | amber label ink may be used after actual-pair AA test | Keep category; do not use for tabs/focus/CTA. |
| Phrase lavender | low-alpha lavender fill or a thin dotted underline | phrase/quote icon and “短语” label | lavender `phrase-ink`, contrast-tested | Keep category; reduce fill saturation/area in Dark. |
| Grammar violet | subdued fill plus patterned/dashed underline | grammar icon and “语法解析” label | create a lighter `grammar-ink`; current `#746694` is fill/icon only | Must change for textual labels. |
| Context blue | low-alpha fill plus a left cue/underline | context icon and “长句拆析” label | current blue is only narrowly AA; define/test `context-ink` if small text appears | Keep distinct from action blue and do not use it for global focus. |
| Structure green | low-alpha fill plus a left rule/border | structure icon and “结构” label | create a lighter `structure-ink`; current `#3c8c68` is fill/icon only | Must change for textual labels. |

Implementation constraints:

1. For inline text, category *fill* is background only; preserve
   `reading-ink` as the foreground unless the resulting pair is explicitly
   tested. Do not turn whole phrases amber/lavender/green.
2. Every category must be exposed in the associated card/panel heading and
   accessible name; icons/patterns/underline geometry give a visible redundant
   cue. Colour alone is insufficient.
3. The active annotation may use a stronger fill/border; inactive classes need
   quiet fills and user filtering. This prevents simultaneous yellow, purple,
   blue, and green marks from fragmenting the text.
4. A coloured foreground in an annotation card is normal text and must meet
   4.5:1 against that card's *actual* background. Meaningful underline/border,
   icon and focus state must meet 3:1 against adjacent colour.
5. `action-primary` stays Claread blue (`#8cb2ff` in Dark): it owns focus,
   selection, keyboard focus and primary CTA. Semantic category colours retain
   their article-analysis meaning only.

## Recommended validation protocol

1. **Token tests:** calculate contrast after alpha compositing for prose,
   translations, annotation label inks, fills/underlines, focus rings, and
   category cards in both Light and Dark. Test normal text at 4.5:1 minimum and
   meaningful non-text marks at 3:1 minimum.
2. **Real-page screenshots:** use the same article in Light/Dark at the same
   zoom; inspect continuous prose, Chinese translation, one of each annotation
   type, active vs inactive annotation, More menu, Ask citation, and focus
   state. Do not judge a colour in an isolated swatch.
3. **Density states:** verify 0, one, and many simultaneous annotations. At
   high density, preserve visible category labels/filter controls and favour
   subdued fills over increasingly saturated foregrounds.
4. **User controls:** retain System/Light/Dark. Give Reader users a way to
   reduce annotation density or select an active analysis category; visual
   comfort depends on user, display brightness, and ambient lighting.

## Recommendation for the next implementation slice

First create the semantic roles (`reader-stage`, `reading-ink`,
`reading-ink-strong`, `reading-muted`, `surface-recessed`, and per-category
`fill`/`ink` pairs) and map current components deliberately. Do not globally
replace hex values or tweak all annotations at once. Make `grammar-ink` and
`structure-ink` mandatory before either is used as small text. Then evaluate a
single representative analysis page in both themes before widening the change.
