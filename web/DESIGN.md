# Argus web design system ("industrial safety, light")

Dense, form-like, projector-friendly. Tokens live in `app/globals.css` as CSS variables and are exposed to Tailwind v4 via `@theme inline`.

- **Surfaces:** `bg-base` (#F7F6F2 page), `bg-surface` (white cards), `bg-surface-2` (table headers, wells), `border-line`.
- **Ink:** `text-ink`, `text-muted` (6.4:1 on base), `text-faint` (decorative only).
- **Accent (safety orange):** `bg-accent text-accent-ink` for fills (#FF6B00 with ink text = 6.4:1). For orange *text* use `text-accent-text` (#B84700, 5.0:1 on base). Never put `#FF6B00` text on light surfaces. `bg-accent-soft` for highlights.
- **Classification chips:** `near-miss-*` (amber), `incident-*` (red), `ppe-*` (blue), `system-*` (grey), each with `-bg`, `-fg`, `-line`. Use `<ClassChip value="near_miss" />`.
- **Status chips:** `status-draft|signed|progress|failed-{bg,fg}`. Use `<StatusChip>`.
- **Risk 1-25:** `risk-1..5-{bg,fg}` = bands 1-4 green, 5-9 lime, 10-14 amber, 15-19 orange, 20-25 red. Use `<RiskChip score={n} />` or `riskBand(n)` from `lib/risk.ts`.
- **Type:** Inter (`font-sans`) for UI; JetBrains Mono (`font-mono`) for numbers. Use the `.num` class (mono + tabular numerals) on every metric, ID and timestamp column. `.field-label` is the small uppercase form label.
- **Density:** base font 15px, 6px radius, 1px borders instead of shadows, table rows ~36px. Print: `.no-print` hides chrome; base font drops to 12px.
- **Focus:** 2px orange outline (`--ring`) on `:focus-visible`.
- **Elevation:** cards use `shadow-card` (two low-alpha ink layers) with a 1px border kept for structure; popovers, toasts and dialogs use `shadow-pop`. Shadows are stripped in print.
- **Wordmark:** `components/shell/wordmark.tsx` (hard-hat glyph on orange tile + tracked caps). Pitfall: `text-base` is the *base surface colour* in this theme, not a font size; use `text-[1rem]`.
- **Projector:** root font-size steps from 15px to 17px at >=1800px wide.
- **Motion:** one curve `--ease-out-strong` (cubic-bezier(0.23,1,0.32,1)). Only for: new incident row (250ms slide + 2.4s orange fade), toast in 200ms / out 150ms, processing stepper step colour 200ms, banners and header (`.anim-banner`, 220ms), report reveal when ready (`.anim-reveal`, 280ms), button/filter press scale 0.96 in 150ms. Under `prefers-reduced-motion` movement and scale are dropped and only opacity fades remain.
- **Nav:** active page has an orange underline and `aria-current="page"`.
- **Components:** `components/ui/*` follow the shadcn/ui pattern (Button, Input, Label, Card) and read these tokens; `components/chips.tsx` holds the domain chips.
