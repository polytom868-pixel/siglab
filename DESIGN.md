---
name: SigLab
description: Research-to-action pipeline for crypto trading strategies
colors:
  evidence-green: "#4ade80"
  evidence-green-dim: "#2dd469"
  evidence-green-soft: "rgba(74, 222, 128, 0.10)"
  evidence-green-surface: "rgba(74, 222, 128, 0.06)"
  deep-black: "#080c0a"
  surface-dark: "#111916"
  surface-raised: "#162019"
  surface-hover: "#1a2820"
  surface-glass: "rgba(18, 28, 22, 0.78)"
  ink-primary: "#e2ebe5"
  ink-secondary: "#a3b5a8"
  ink-muted: "#7a8f7e"
  amber-warn: "#f0b456"
  amber-soft: "rgba(240, 180, 86, 0.12)"
  red-fail: "#f87171"
  red-soft: "rgba(248, 113, 113, 0.10)"
  border-subtle: "rgba(255, 255, 255, 0.06)"
  border-accent: "rgba(78, 204, 130, 0.12)"
typography:
  display:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "clamp(1.75rem, 4vw, 2.5rem)"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.3
  title:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "1rem"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "0.02em"
  mono:
    fontFamily: "'JetBrains Mono', 'Fira Code', monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "4px"
  md: "8px"
  lg: "12px"
  pill: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  xxl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.evidence-green}"
    textColor: "{colors.deep-black}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  button-primary-hover:
    backgroundColor: "{colors.evidence-green-dim}"
    textColor: "{colors.deep-black}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink-primary}"
    rounded: "{rounded.md}"
    padding: "10px 20px"
  card:
    backgroundColor: "{colors.surface-dark}"
    textColor: "{colors.ink-primary}"
    rounded: "{rounded.lg}"
    padding: "{spacing.lg}"
  input:
    backgroundColor: "{colors.surface-raised}"
    textColor: "{colors.ink-primary}"
    rounded: "{rounded.md}"
    padding: "10px 14px"
  chip:
    backgroundColor: "{colors.evidence-green-soft}"
    textColor: "{colors.evidence-green}"
    rounded: "{rounded.pill}"
    padding: "4px 12px"
---

# Design System: SigLab

## 1. Overview

**Creative North Star: "The Research Terminal"**

SigLab is a research tool for crypto strategy operators. The interface treats the operator as a peer: it shows the full data, the full uncertainty, and the full refusal when evidence is missing. It does not simplify for the sake of looking clean. It does not gamify for the sake of engagement. It shows what's real and lets the operator decide.

The visual system is dark-first, data-dense, and evidence-green. Every surface serves information. Every color carries meaning. The green accent appears only on active states, verified data, and confirmed actions — it is the color of "yes, this is real." Amber means "check this." Red means "this failed." The background is near-black because the operator is scanning data, not reading prose.

This system explicitly rejects the crypto-bro aesthetic (neon, laser eyes, rocket emojis, "to the moon" energy) and the generic SaaS dashboard (blue-gradient hero, card-grid layout, hero-metric template, "empower your workflow" copy). SigLab is a research terminal, not a marketing page and not a trading game.

**Key Characteristics:**
- Dark-first with tonal layering (depth through surface color, not shadows)
- Evidence-green accent on ≤15% of any screen (rarity is the point)
- Data-dense: information hierarchy through typography weight and size, not whitespace
- Honest: missing data says "No data available", refused actions say why
- Operator-grade: keyboard navigation, reduced motion, WCAG 2.2 AA

## 2. Colors

The palette is dark-grounded with a single accent. Depth comes from tonal layering (surface color variation), not from shadows or gradients.

### Primary
- **Evidence Green** (#4ade80): The accent. Used on active states, verified data, confirmed actions, and the selection highlight. It is the color of "this is real." Appears on ≤15% of any screen — its rarity is the point.

### Neutral
- **Deep Black** (#080c0a): The ground. The body background. Near-black with a slight green tint.
- **Surface Dark** (#111916): The first layer. Cards, panels, containers. Slightly lighter than the ground.
- **Surface Raised** (#162019): The second layer. Inputs, elevated cards, active panels.
- **Ink Primary** (#e2ebe5): The main text color. Light with a green tint. High contrast against the dark ground.
- **Ink Secondary** (#a3b5a8): Secondary text. Labels, descriptions, supporting information.
- **Ink Muted** (#7a8f7e): The quietest text. Timestamps, metadata, placeholders.

### Semantic
- **Amber Warn** (#f0b456): Warnings, pending states, "check this."
- **Red Fail** (#f87171): Errors, failures, refused actions.
- **Evidence Green Soft** (rgba(74, 222, 128, 0.10)): The tinted background for green elements (chips, badges, selection).

### Named Rules
**The Rarity Rule.** The primary accent is used on ≤15% of any given screen. Its rarity is the point. If everything is green, nothing is green.

**The Tonal Layering Rule.** Depth is conveyed through surface color variation (surface-dark → surface-raised → surface-hover), not through box-shadows or gradients. The ground is deepest; each layer above is slightly lighter.

## 3. Typography

**Display Font:** Inter (with system fallbacks)
**Body Font:** Inter (with system fallbacks)
**Mono Font:** JetBrains Mono (with Fira Code fallback)

**Character:** One font family in multiple weights. The hierarchy comes from scale and weight contrast, not from competing typefaces. Inter is a workhorse: clear at small sizes, authoritative at large sizes. JetBrains Mono provides the terminal feel for data, code, and metrics.

### Hierarchy
- **Display** (700, clamp(1.75rem, 4vw, 2.5rem), line-height 1.15): Page titles. The hero heading. Used once per page.
- **Headline** (600, 1.25rem, line-height 1.3): Section headings. Panel titles.
- **Title** (600, 1rem, line-height 1.4): Card titles, list item headings.
- **Body** (400, 14px, line-height 1.5): The default text. Max line length 65–75ch.
- **Label** (500, 12px, line-height 1.4, letter-spacing 0.02em): Form labels, metadata, small annotations.
- **Mono** (400, 13px, line-height 1.5): Data values, metrics, code, terminal output.

### Named Rules
**The Weight Contrast Rule.** Hierarchy comes from weight contrast (400 → 500 → 600 → 700), not from size alone. A 14px/700 heading is more authoritative than a 18px/400 heading.

**The Mono-for-Data Rule.** Numbers, metrics, timestamps, and data values use JetBrains Mono. It signals "this is data, not prose."

## 4. Elevation

This system uses tonal layering, not shadows. Depth is conveyed through surface color variation: the ground (#080c0a) is deepest, each layer above is slightly lighter (#111916 → #162019 → #1a2820). Shadows exist but are subtle and structural, not decorative.

### Shadow Vocabulary
- **Ambient** (`0 4px 32px rgba(0, 0, 0, 0.4)`): Used sparingly. Only on elevated elements that need to float above the surface (dropdowns, modals, tooltips).
- **Glow** (`0 0 80px rgba(74, 222, 128, 0.06)`): The green glow. Used on the body background as a subtle ambient effect. Not on individual components.

### Named Rules
**The Flat-By-Default Rule.** Surfaces are flat at rest. No decorative shadows. Depth comes from color, not from elevation.

## 5. Components

### Buttons
- **Shape:** 8px radius. Compact padding (10px 20px).
- **Primary:** Evidence Green background, Deep Black text. The "do this" action.
- **Hover:** Evidence Green Dim background. Slight brightness shift.
- **Ghost:** Transparent background, Ink Primary text, subtle border. The "cancel" or "secondary" action.
- **Disabled:** Muted background, Muted text. No hover effect.

### Chips / Pills
- **Style:** Evidence Green Soft background, Evidence Green text, pill radius (9999px).
- **Use:** Status indicators, filter tags, family labels. Not for navigation.
- **State:** Selected = filled green. Unselected = muted background.

### Cards / Containers
- **Corner Style:** 12px radius.
- **Background:** Surface Dark (#111916). No border by default.
- **Shadow:** None. Depth from tonal layering.
- **Internal Padding:** 24px (lg).
- **Hover:** Surface Hover (#1a2820) on interactive cards.

### Inputs / Fields
- **Style:** Surface Raised background, 8px radius, 10px 14px padding.
- **Border:** Subtle border (rgba(255, 255, 255, 0.06)). No border by default; border appears on focus.
- **Focus:** Evidence Green border, subtle green glow.
- **Error:** Red Fail border, error text below.

### Navigation
- **Style:** Sticky top bar, 56px height, glassmorphism (rgba(8, 12, 10, 0.82) background with backdrop-filter).
- **Links:** Ink Secondary default, Ink Primary hover, Evidence Green active.
- **Mobile:** Hamburger menu, full-width dropdown.

### Data Table
- **Style:** No grid lines. Row hover = Surface Hover.
- **Header:** Label typography (500, 12px, uppercase, wide tracking).
- **Cells:** Body typography. Numbers in Mono.
- **Selection:** Evidence Green Soft background on selected row.

## 6. Do's and Don'ts

### Do:
- **Do** use Evidence Green only on active states, verified data, and confirmed actions. Its rarity is the point.
- **Do** use tonal layering for depth (surface-dark → surface-raised → surface-hover), not shadows.
- **Do** use Mono for numbers, metrics, timestamps, and data values.
- **Do** show "No data available" when data is missing. Never show a blank space.
- **Do** show why an action was refused (e.g., "signed execution refused — missing: account_id"). Never fail silently.
- **Do** use weight contrast (400 → 700) for hierarchy, not size alone.
- **Do** cap body text at 65–75ch line length.
- **Do** provide reduced-motion alternatives for all animations.

### Don't:
- **Don't** use the crypto-bro aesthetic (neon colors, laser eyes, rocket emojis, "to the moon" energy). PRODUCT.md says: "the demo script explicitly refuses hype and causal claims."
- **Don't** use the generic SaaS dashboard pattern (blue-gradient hero, card-grid layout, hero-metric template, "empower your workflow" copy). PRODUCT.md says: "SigLab is a research tool, not a productivity app."
- **Don't** use gradient text (background-clip: text with a gradient background).
- **Don't** use side-stripe borders (border-left > 1px as a colored accent).
- **Don't** use glassmorphism decoratively. The navbar glass is functional (transparency for content behind); don't add blur to cards or panels.
- **Don't** use border-radius > 16px on cards. Cards top out at 12–16px; pill is for chips only.
- **Don't** use shadows decoratively. Shadows are structural (modals, dropdowns), not decorative (cards, buttons).
- **Don't** use the hero-metric template (big number, small label, supporting stats, gradient accent). Show the data in context, not as a marketing stat.
