// Shared styling for the BlueShark wizard, built on Nitin's Lucent design language (v1) and its
// Home Assistant adapter (languages/lucent/ha.css, embedded verbatim below as the single token
// layer). Every colour, radius and motion value below is either a `--lu-*` role from that token
// layer (itself sourced from the host HA theme, never a palette of our own) or a fixed layout
// dimension (gaps, borders, breakpoints) that a theme has no opinion about. No hardcoded palette:
// this panel must look native in both a flat theme (Neumorphism) and a glass theme (Liquid Glass).

export const PANEL_CSS = `
/* Lucent v1 — Home Assistant adapter. Put this on the :host of every custom card/panel.
 *
 * Rule: inside HA the user's HA theme IS the palette. Nothing here names a colour of its own;
 * every Lucent role is an HA theme variable or a color-mix of one, so the card looks native in
 * Neumorphism, default, Liquid/Frosted Glass or any other theme and follows theme switches live.
 * Lucent contributes structure only: layering, hairline edges, concentric radii, lift, motion.
 * The card body itself must be <ha-card> so the theme's own card background, border, shadow and
 * any card-mod backdrop apply unchanged. */
:host {
  /* host-theme colour roles */
  --lu-accent: var(--primary-color);
  --lu-accent-ink: var(--text-primary-color, #fff);
  --lu-ink: var(--primary-text-color);
  --lu-ink-2: var(--secondary-text-color);
  --lu-ink-3: var(--disabled-text-color, var(--secondary-text-color));
  --lu-positive: var(--success-color, #43a047);
  --lu-warning: var(--warning-color, #ffa600);
  --lu-danger: var(--error-color, #db4437);
  --lu-info: var(--info-color, #039be5);
  --lu-live: var(--error-color, #db4437);

  /* glass levels derived from the theme's own card colour and text colour */
  --lu-card: var(--ha-card-background, var(--card-background-color));
  --lu-edge: var(--ha-card-border-color, var(--divider-color));
  --lu-tile: color-mix(in srgb, var(--primary-text-color) 6%, transparent);
  --lu-glass-raised: color-mix(in srgb, var(--primary-text-color) 12%, transparent);
  --lu-edge-raised: color-mix(in srgb, var(--primary-text-color) 22%, transparent);
  --lu-track-off: color-mix(in srgb, var(--primary-text-color) 16%, transparent);
  --lu-accent-soft: color-mix(in srgb, var(--primary-color) 18%, transparent);
  --lu-scrim: color-mix(in srgb, var(--primary-background-color) 55%, transparent);

  /* shape: concentric with whatever radius the theme gives cards */
  --lu-radius-card: var(--ha-card-border-radius, 24px);
  --lu-radius-tile: max(calc(var(--lu-radius-card) - 4px), 8px);
  --lu-radius-row: max(calc(var(--lu-radius-card) - 6px), 8px);
  --lu-radius-control: max(calc(var(--lu-radius-card) - 10px), 6px);
  --lu-radius-pill: 999px;
  --lu-target: 48px;

  /* material: soft and theme-relative; heavy lift only on raised elements */
  --lu-highlight-raised: inset 0 1px 0 color-mix(in srgb, #fff 18%, transparent);
  --lu-shadow-raised: 0 10px 24px color-mix(in srgb, #000 22%, transparent);
  --lu-shadow-pressed: 0 4px 10px color-mix(in srgb, #000 18%, transparent);

  /* motion and type */
  --lu-ease: cubic-bezier(.33, 1, .68, 1);
  --lu-motion-press: 90ms;
  --lu-motion-focus: 150ms;
  --lu-motion-card: 180ms;
  --lu-motion-layer: 220ms;
  --lu-hold: 1500ms;
  --lu-font: var(--ha-font-family-body, var(--paper-font-body1_-_font-family, inherit));
}

@media (prefers-reduced-motion: reduce) {
  :host { --lu-motion-focus: 0ms; --lu-motion-card: 0ms; --lu-motion-layer: 120ms; }
}

/* --- Local, non-Lucent additions --------------------------------------------------------
 * One token the Lucent vocabulary has no opinion about: the monospace stack used for hex byte
 * displays (sent/received frames, opcodes, the raw command-map JSON editor). Not a colour, so it
 * carries no palette risk; kept separate from the --lu-* block above so it reads as app-specific
 * rather than part of the ported design-system file. */
:host {
  --bs-font-mono: var(--code-font-family, ui-monospace, Menlo, Consolas, monospace);
}

*, *::before, *::after { box-sizing: inherit; }

:host {
  display: block;
  color: var(--lu-ink);
  background: var(--primary-background-color, var(--lu-card));
  font-family: var(--lu-font);
  -webkit-font-smoothing: antialiased;
  box-sizing: border-box;
  min-height: 100%;
}

a { color: var(--lu-accent); }
:focus-visible { outline: 2px solid var(--lu-accent); outline-offset: 2px; }

/* --- App bar (level 0 chrome, sits directly on the HA header colour) -------------------- */

.bs-appbar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 16px;
  min-height: var(--header-height, 56px);
  box-sizing: border-box;
  background: var(--app-header-background-color, var(--lu-accent));
  color: var(--app-header-text-color, var(--lu-accent-ink));
  position: sticky;
  top: 0;
  z-index: 2;
}

.bs-appbar-titles { flex: 1 1 auto; min-width: 0; }
.bs-appbar-titles h1 { margin: 0; font-size: 1.15em; font-weight: 500; line-height: 1.2; }
.bs-appbar-titles p { margin: 0; font-size: 0.8em; opacity: 0.85; }

.bs-menu-button {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--lu-target);
  height: var(--lu-target);
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
  transition: background var(--lu-motion-focus) var(--lu-ease), transform var(--lu-motion-press) var(--lu-ease);
}

.bs-menu-button[hidden] { display: none; }
/* currentColor-derived so the hover wash reads correctly whether the app bar (and therefore the
   button's inherited text colour) ends up light-on-dark or dark-on-light for this HA theme. */
.bs-menu-button:hover { background: color-mix(in srgb, currentColor 15%, transparent); }
.bs-menu-button:active { background: color-mix(in srgb, currentColor 25%, transparent); transform: scale(0.97); }
.bs-menu-button:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
.bs-menu-button svg { width: 24px; height: 24px; }

/* --- Shell / layout ---------------------------------------------------------------------- */

.bs-shell {
  display: flex;
  flex-direction: row;
  gap: 24px;
  align-items: flex-start;
  padding: 16px;
  max-width: 1100px;
  margin: 0 auto;
}
:host([narrow]) .bs-shell { flex-direction: column; padding: 12px; gap: 12px; }

/* --- Stepper: the Find/Identify/Learn/Finish nav, a segmented row of steps (Lucent section 8's
   segmented control, oriented as a sidebar on wide viewports and wrapped into a row on narrow
   ones) with the active step as a solid accent pill and reachable-but-inactive steps transparent. */

.bs-stepper {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 0 0 200px;
  position: sticky;
  top: 12px;
}
:host([narrow]) .bs-stepper { flex-direction: row; flex-wrap: wrap; position: static; width: 100%; }

.bs-stepper__step {
  display: flex;
  align-items: center;
  gap: 10px;
  border: none;
  background: transparent;
  color: var(--lu-ink-2);
  font: inherit;
  font-size: 0.95em;
  text-align: left;
  padding: 10px 12px;
  min-height: var(--lu-target);
  box-sizing: border-box;
  border-radius: var(--lu-radius-row);
  cursor: pointer;
  transition: background var(--lu-motion-focus) var(--lu-ease), color var(--lu-motion-focus) var(--lu-ease);
}
.bs-stepper__step[disabled] { color: var(--lu-ink-3); cursor: not-allowed; }
.bs-stepper__step[aria-current='step'] { background: var(--lu-accent); color: var(--lu-accent-ink); font-weight: 600; }
.bs-stepper__step:not([disabled]):hover { background: var(--lu-tile); }
.bs-stepper__step[aria-current='step']:hover { background: var(--lu-accent); }
.bs-stepper__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  border: 2px solid currentColor;
  flex: 0 0 auto;
  font-size: 0.8em;
}
.bs-stepper__step[data-status='complete'] .bs-stepper__badge { background: var(--lu-positive); border-color: var(--lu-positive); color: var(--lu-accent-ink); }

.bs-content { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 16px; }

/* A plain sub-section inside a card body: heading + content, no border or fill of its own.
   Lucent section 2's "exactly one fill per group" rule — the enclosing <bs-card> is the sheet;
   these are dense-list rows/groups within it, which section 8 explicitly allows to skip the
   hairline rather than stack box-in-box-in-box. */
.bs-section { display: flex; flex-direction: column; gap: 10px; }
.bs-section h3 { margin: 0; font-size: 1em; font-weight: 600; color: var(--lu-ink); }

/* --- Card / sheet (level 1 glass) ---------------------------------------------------------- */

.bs-card {
  display: block;
  background: var(--lu-card);
  border: 1px solid var(--lu-edge);
  border-radius: var(--lu-radius-card);
  overflow: hidden;
  transition: border-color var(--lu-motion-card) var(--lu-ease), box-shadow var(--lu-motion-card) var(--lu-ease);
}
/* The open (expanded) step reads as the raised/selected pane; collapsed steps stay flat. */
.bs-card[open] { border-color: var(--lu-edge-raised); box-shadow: var(--lu-shadow-raised); }
.bs-card__header {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  padding: 16px;
  cursor: pointer;
  transition: background var(--lu-motion-focus) var(--lu-ease);
}
.bs-card__header:disabled { cursor: default; }
.bs-card__header:not(:disabled):hover { background: var(--lu-tile); }
.bs-card__heading { font-size: 1.1em; font-weight: 600; margin: 0; color: var(--lu-ink); }
.bs-card__subheading { color: var(--lu-ink-2); font-size: 0.9em; margin: 2px 0 0; }
.bs-card__chevron { margin-left: auto; transition: transform var(--lu-motion-focus) var(--lu-ease); color: var(--lu-ink-2); }
.bs-card[open] .bs-card__chevron { transform: rotate(180deg); }
.bs-card__body { padding: 0 16px 16px; display: flex; flex-direction: column; gap: 14px; }
.bs-card__body[hidden] { display: none; }

/* A single family-match row inside Identify's card: divided by hairline, never its own fill
   (avoids the nested-card look of a second bordered box sitting inside the outer sheet). */
.bs-match { display: flex; flex-direction: column; gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--lu-edge); }
.bs-match:last-of-type { border-bottom: none; }

/* A quiet, hairline-only callout (the dedicated-integration notice, blocked-opcode notes): a
   tile, not a second sheet, so it never doubles the card's own fill. */
.bs-callout { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--lu-edge); border-radius: var(--lu-radius-tile); }

/* The "make this a control"/builder panel is actively being edited, i.e. Lucent's raised/selected
   tile state: it is the one place inside a step that legitimately earns its own fill. */
.bs-builder-panel {
  padding: 12px;
  border-radius: var(--lu-radius-tile);
  background: var(--lu-glass-raised);
  border: 1px solid var(--lu-edge-raised);
  box-shadow: var(--lu-highlight-raised);
}

/* --- Buttons: pill-shaped per section 8. Primary = solid accent; everything else (including the
   base .bs-btn) is the secondary glass treatment; destructive uses danger ink/edge, never a red
   slab, and anything that overwrites/destroys uses <bs-hold-button> instead of a plain click. */

.bs-btn {
  font: inherit;
  font-size: 0.95em;
  font-weight: 500;
  border-radius: var(--lu-radius-pill);
  border: 1px solid var(--lu-edge);
  background: color-mix(in srgb, var(--lu-glass-raised) 60%, transparent);
  color: var(--lu-ink);
  padding: 10px 20px;
  min-height: var(--lu-target);
  box-sizing: border-box;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: transform var(--lu-motion-press) var(--lu-ease), box-shadow var(--lu-motion-press) var(--lu-ease), background var(--lu-motion-focus) var(--lu-ease);
}
.bs-btn:disabled { color: var(--lu-ink-3); cursor: not-allowed; opacity: 0.7; }
.bs-btn:not(:disabled):active { transform: scale(0.97); box-shadow: var(--lu-shadow-pressed); }
.bs-btn--primary { background: var(--lu-accent); border-color: var(--lu-accent); color: var(--lu-accent-ink); font-weight: 600; }
.bs-btn--danger { border-color: var(--lu-danger); color: var(--lu-danger); }
.bs-btn--text { border-color: transparent; background: transparent; padding: 10px 12px; }
.bs-btn-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }

/* --- Hold-to-confirm: a danger pill whose fill sweeps over --lu-hold (1500ms); releasing early
   cancels the sweep with a quick 150ms snap-back instead of firing "confirm" (components.js
   drives the [data-holding] attribute from pointer/keyboard hold state). */

.bs-hold { position: relative; overflow: hidden; isolation: isolate; }
.bs-hold__fill {
  position: absolute;
  inset: 0;
  background: var(--lu-danger);
  opacity: 0.28;
  transform: scaleX(0);
  transform-origin: left center;
  transition: transform 150ms var(--lu-ease);
  pointer-events: none;
  z-index: 0;
}
.bs-hold[data-holding='true'] .bs-hold__fill { transform: scaleX(1); transition-duration: var(--lu-hold); transition-timing-function: linear; }
.bs-hold__label { position: relative; z-index: 1; }

/* --- Switch: track --lu-track-off -> accent when on, thumb near-white, 150ms. Used for the one
   Advanced toggle in the app bar. */

.bs-switch-row { display: flex; align-items: center; gap: 8px; font-size: 0.9em; white-space: nowrap; }
.bs-switch { position: relative; display: inline-flex; width: 44px; height: 26px; flex: 0 0 auto; }
.bs-switch__input { position: absolute; inset: 0; margin: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; z-index: 1; }
.bs-switch__track { position: absolute; inset: 0; border-radius: var(--lu-radius-pill); background: var(--lu-track-off); transition: background var(--lu-motion-focus) var(--lu-ease); }
.bs-switch__track::before {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--lu-accent-ink);
  box-shadow: var(--lu-shadow-pressed);
  transition: transform var(--lu-motion-focus) var(--lu-ease);
}
.bs-switch__input:checked + .bs-switch__track { background: var(--lu-accent); }
.bs-switch__input:checked + .bs-switch__track::before { transform: translateX(18px); }
.bs-switch__input:focus-visible + .bs-switch__track { outline: 2px solid var(--lu-accent); outline-offset: 2px; }
.bs-switch__input:disabled { cursor: not-allowed; }
.bs-switch__input:disabled + .bs-switch__track { opacity: 0.5; }

/* --- Segmented control: a row of large pills for a small mutually exclusive choice; the wrap
   is its own containment context so the row collapses to a stacked list -- the "stepper" register
   section 8 allows at narrow widths -- purely from its own available width (container query, not
   a viewport breakpoint), matching every other field around it whichever way this ends up laid out. */
.bs-segmented-wrap { container-type: inline-size; container-name: bs-segment-group; }
.bs-segmented { display: flex; gap: 6px; flex-wrap: wrap; }
.bs-segment {
  font: inherit;
  font-size: 0.9em;
  font-weight: 500;
  border-radius: var(--lu-radius-pill);
  border: 1px solid var(--lu-edge);
  background: color-mix(in srgb, var(--lu-glass-raised) 60%, transparent);
  color: var(--lu-ink);
  padding: 10px 16px;
  min-height: var(--lu-target);
  box-sizing: border-box;
  cursor: pointer;
  flex: 1 1 auto;
  transition: background var(--lu-motion-focus) var(--lu-ease), color var(--lu-motion-focus) var(--lu-ease);
}
.bs-segment:disabled { color: var(--lu-ink-3); cursor: not-allowed; }
.bs-segment:not(:disabled):hover { background: var(--lu-glass-raised); }
.bs-segment--selected { background: var(--lu-accent); border-color: var(--lu-accent); color: var(--lu-accent-ink); font-weight: 600; }
.bs-segment--selected:hover { background: var(--lu-accent); }
@container bs-segment-group (max-width: 260px) {
  .bs-segmented { flex-direction: column; }
}

/* --- Chips: pill, glass fill, 8px semantic status dot + ink label (never colour-washed text). */

.bs-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: var(--lu-radius-pill);
  padding: 3px 10px 3px 8px;
  font-size: 0.85em;
  font-weight: 600;
  line-height: 1.6;
  background: color-mix(in srgb, var(--lu-glass-raised) 60%, transparent);
  border: 1px solid var(--lu-edge);
  color: var(--lu-ink);
  white-space: nowrap;
}
.bs-chip::before { content: ''; width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; background: var(--lu-ink-3); }
.bs-chip--success::before { background: var(--lu-positive); }
.bs-chip--error::before { background: var(--lu-danger); }
.bs-chip--warning::before { background: var(--lu-warning); }
.bs-chip--neutral::before { background: var(--lu-ink-3); }

/* --- Form fields --------------------------------------------------------------------------- */

.bs-field { display: flex; flex-direction: column; gap: 4px; }
.bs-field label { font-size: 0.85em; color: var(--lu-ink-2); }
.bs-field input[type='text'],
.bs-field input[type='number'],
.bs-field input[type='search'],
.bs-field select,
.bs-field textarea {
  font: inherit;
  font-size: 0.95em;
  padding: 8px 10px;
  border-radius: var(--lu-radius-control);
  border: 1px solid var(--lu-edge);
  background: var(--lu-card);
  color: var(--lu-ink);
}
.bs-field textarea { resize: vertical; }
.bs-field__error { color: var(--lu-danger); font-size: 0.85em; margin: 0; }
.bs-field-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.bs-checkbox-row { display: flex; align-items: center; gap: 8px; font-size: 0.9em; }

.bs-hexfield input { font-family: var(--bs-font-mono); letter-spacing: 0.04em; }

.bs-mono { font-family: var(--bs-font-mono); }

/* --- Table ---------------------------------------------------------------------------------- */

.bs-table-wrap { overflow-x: auto; border: 1px solid var(--lu-edge); border-radius: var(--lu-radius-tile); }
.bs-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
.bs-table th, .bs-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--lu-edge); vertical-align: middle; }
.bs-table th { color: var(--lu-ink-2); font-weight: 600; position: sticky; top: 0; background: var(--lu-card); }
.bs-table tbody tr:last-child td { border-bottom: none; }
.bs-table tbody tr[data-canary='true'] { background: var(--lu-tile); }
.bs-table__empty { padding: 20px; text-align: center; color: var(--lu-ink-2); }

/* --- Badges, RSSI bar, banners, evidence, empty state ---------------------------------------- */

.bs-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: var(--lu-radius-pill);
  padding: 2px 10px;
  font-size: 0.8em;
  background: color-mix(in srgb, var(--lu-glass-raised) 60%, transparent);
  border: 1px solid var(--lu-edge);
  color: var(--lu-ink-2);
}

.bs-rssi-bar { width: 64px; height: 6px; border-radius: var(--lu-radius-pill); background: var(--lu-track-off); overflow: hidden; display: inline-block; }
.bs-rssi-bar__fill { height: 100%; background: var(--lu-accent); }
.bs-rssi-bar__fill[data-tone='weak'] { background: var(--lu-danger); }
.bs-rssi-bar__fill[data-tone='ok'] { background: var(--lu-warning); }
.bs-rssi-bar__fill[data-tone='strong'] { background: var(--lu-positive); }

.bs-banner {
  border-radius: var(--lu-radius-tile);
  padding: 10px 14px;
  font-size: 0.9em;
  border: 1px solid var(--lu-edge);
  background: color-mix(in srgb, var(--lu-glass-raised) 60%, transparent);
}
.bs-banner--error { border-color: var(--lu-danger); color: var(--lu-danger); background: color-mix(in srgb, var(--lu-danger) 10%, transparent); }
.bs-banner--busy { border-color: var(--lu-warning); color: var(--lu-ink); background: color-mix(in srgb, var(--lu-warning) 12%, transparent); }

.bs-evidence { margin: 0; padding-left: 18px; color: var(--lu-ink-2); font-size: 0.9em; }
.bs-evidence li { margin: 2px 0; }

.bs-empty { color: var(--lu-ink-2); font-style: italic; font-size: 0.9em; }

.bs-visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
`;

let sheet = null;

/** A single shared CSSStyleSheet instance, built once and reused by every shadow root. */
export function sharedStyleSheet() {
  if (sheet) return sheet;
  sheet = new CSSStyleSheet();
  sheet.replaceSync(PANEL_CSS);
  return sheet;
}

/** Adopt the shared styles onto `root` (a shadow root or document). Falls back to injecting a
 * plain <style> element when constructable stylesheets are unavailable. */
export function adoptSharedStyles(root) {
  if (!root) return;
  if ('adoptedStyleSheets' in root && typeof CSSStyleSheet === 'function') {
    root.adoptedStyleSheets = [...(root.adoptedStyleSheets ?? []), sharedStyleSheet()];
    return;
  }
  const style = document.createElement('style');
  style.textContent = PANEL_CSS;
  root.appendChild(style);
}
