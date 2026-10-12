# Aurum — design direction

**Subject:** a private, local-only ledger for one person's net worth, cards and budget (any currency). Job: answer "where do I stand, and what needs me this week?" in one glance, then let me dig.

**Concept — "the ledger."** Quiet, printed-feeling surfaces; one oversized number (net worth) is the memorable thing. Everything else is disciplined: flat panels, hairline rules, no glass, no glow, no gradient washes.

## Tokens
| Role | Light (bone) | Dark (ink) |
|---|---|---|
| bg | `#ECEEE9` | `#0F1411` |
| panel | `#F7F8F5` | `#161C18` |
| ink | `#12201A` | `#E9EFEB` |
| accent (ultramarine, used sparingly) | `#2C3FD8` | `#8E9BFF` |
| gain / loss / watch | `#0E7C5A` / `#C2352E` / `#B4690E` | `#4FD1A0` / `#FF7A70` / `#E8A94A` |

Accent is reserved for primary actions, the active nav item and the single "you" series in charts. Gain/loss colours only ever mean gain/loss.

## Type
- **Bricolage Grotesque** — display: net worth figure, page and section titles.
- **Geist** — everything else; tabular figures on globally (it's a finance app).
- Sentence case labels; no tracked all-caps eyebrows, no monospace data labels.

## Layout
Left sidebar (nav + actions) on desktop; compact top bar + bottom tab bar on mobile.
Dashboard reads top to bottom: **hero** (net worth + its history, safe/growth split) → **needs attention** (dues, anomalies, this week) → **where it sits** (class, geography, targets, FX) → **how it moves** (composition, categories, intensity, correlations) → goals and data freshness.

## Principles
1. One memorable element per screen; everything else quiet.
2. Structure is information: rules and borders separate meaning, not decorate.
3. Radii are hierarchical (controls 7px, panels 10px, dialogs 14px) — not one radius everywhere.
4. Motion answers actions; the only unprompted motion is a single hero reveal on load. `prefers-reduced-motion` is respected.
5. Charts: one accent series, neutral supporting series, direct labels where possible, same fonts as the UI.
