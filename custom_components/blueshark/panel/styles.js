// Shared styling for the BlueShark wizard, built only from Home Assistant theme variables (each
// with a sane fallback so the panel still reads correctly before any theme sets them). No
// hardcoded palette: every color, radius and spacing token below either comes from a
// `--…-color`/`--ha-card-…`/`--material-shape-…` custom property or is a fixed layout dimension
// (gaps, borders, breakpoints) that a theme has no opinion about.

export const PANEL_CSS = `
:host {
  --bs-primary: var(--primary-color, #03a9f4);
  --bs-on-primary: var(--text-primary-color, #fff);
  --bs-card-bg: var(--card-background-color, var(--primary-background-color, #fff));
  --bs-primary-text: var(--primary-text-color, #212121);
  --bs-secondary-text: var(--secondary-text-color, #727272);
  --bs-divider: var(--divider-color, rgba(0, 0, 0, 0.12));
  --bs-error: var(--error-color, #db4437);
  --bs-success: var(--success-color, #43a047);
  --bs-warning: var(--warning-color, #ff9800);
  --bs-disabled: var(--disabled-text-color, #9e9e9e);
  --bs-radius: var(--ha-card-border-radius, var(--material-shape-medium, 12px));
  --bs-radius-small: var(--material-shape-small, 8px);
  --bs-font: var(--paper-font-body1_-_font-family, Roboto, Noto, sans-serif);
  --bs-mono: var(--code-font-family, ui-monospace, Menlo, Consolas, monospace);
  display: block;
  color: var(--bs-primary-text);
  background: var(--bs-card-bg);
  font-family: var(--bs-font);
  -webkit-font-smoothing: antialiased;
  box-sizing: border-box;
  min-height: 100%;
}
*, *::before, *::after { box-sizing: inherit; }
a { color: var(--bs-primary); }
:focus-visible { outline: 2px solid var(--bs-primary); outline-offset: 2px; }

.bs-appbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  min-height: var(--header-height, 56px);
  box-sizing: border-box;
  background: var(--app-header-background-color, var(--primary-color));
  color: var(--app-header-text-color, var(--text-primary-color, #fff));
  position: sticky;
  top: 0;
  z-index: 2;
}

.bs-appbar-titles h1 { margin: 0; font-size: 1.15em; font-weight: 500; line-height: 1.2; }
.bs-appbar-titles p { margin: 0; font-size: 0.8em; opacity: 0.85; }

.bs-menu-button {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.bs-menu-button[hidden] { display: none; }
.bs-menu-button:hover { background: rgba(255, 255, 255, 0.12); }
.bs-menu-button:focus-visible { outline: 2px solid currentColor; outline-offset: 2px; }
.bs-menu-button svg { width: 24px; height: 24px; }

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
  color: var(--bs-secondary-text);
  font: inherit;
  font-size: 0.95em;
  text-align: left;
  padding: 10px 12px;
  border-radius: var(--bs-radius-small);
  cursor: pointer;
}
.bs-stepper__step[disabled] { color: var(--bs-disabled); cursor: not-allowed; }
.bs-stepper__step[aria-current='step'] { background: var(--bs-primary); color: var(--bs-on-primary); font-weight: 600; }
.bs-stepper__step:not([disabled]):hover { background: var(--bs-divider); }
.bs-stepper__step[aria-current='step']:hover { background: var(--bs-primary); }
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
.bs-stepper__step[data-status='complete'] .bs-stepper__badge { background: var(--bs-success); border-color: var(--bs-success); color: var(--bs-on-primary); }

.bs-content { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 16px; }

.bs-card {
  display: block;
  background: var(--bs-card-bg);
  border: 1px solid var(--bs-divider);
  border-radius: var(--bs-radius);
  overflow: hidden;
}
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
}
.bs-card__header:disabled { cursor: default; }
.bs-card__heading { font-size: 1.1em; font-weight: 600; margin: 0; }
.bs-card__subheading { color: var(--bs-secondary-text); font-size: 0.9em; margin: 2px 0 0; }
.bs-card__chevron { margin-left: auto; transition: transform 0.15s ease; color: var(--bs-secondary-text); }
.bs-card[open] .bs-card__chevron { transform: rotate(180deg); }
.bs-card__body { padding: 0 16px 16px; display: flex; flex-direction: column; gap: 14px; }
.bs-card__body[hidden] { display: none; }

.bs-btn {
  font: inherit;
  font-size: 0.95em;
  font-weight: 500;
  border-radius: var(--bs-radius-small);
  border: 1px solid var(--bs-divider);
  background: var(--bs-card-bg);
  color: var(--bs-primary-text);
  padding: 9px 16px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.bs-btn:disabled { color: var(--bs-disabled); cursor: not-allowed; opacity: 0.7; }
.bs-btn--primary { background: var(--bs-primary); border-color: var(--bs-primary); color: var(--bs-on-primary); }
.bs-btn--danger { background: var(--bs-error); border-color: var(--bs-error); color: var(--bs-on-primary); }
.bs-btn--text { border-color: transparent; background: transparent; padding: 9px 10px; }
.bs-btn-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }

.bs-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 0.85em;
  font-weight: 600;
  line-height: 1.6;
  border: 1px solid transparent;
  white-space: nowrap;
}
.bs-chip--success { background: color-mix(in srgb, var(--bs-success) 18%, transparent); color: var(--bs-success); border-color: var(--bs-success); }
.bs-chip--error { background: color-mix(in srgb, var(--bs-error) 18%, transparent); color: var(--bs-error); border-color: var(--bs-error); }
.bs-chip--warning { background: color-mix(in srgb, var(--bs-warning) 22%, transparent); color: var(--bs-warning); border-color: var(--bs-warning); }
.bs-chip--neutral { background: var(--bs-divider); color: var(--bs-secondary-text); border-color: transparent; }

.bs-field { display: flex; flex-direction: column; gap: 4px; }
.bs-field label { font-size: 0.85em; color: var(--bs-secondary-text); }
.bs-field input[type='text'],
.bs-field input[type='number'],
.bs-field input[type='search'],
.bs-field select,
.bs-field textarea {
  font: inherit;
  font-size: 0.95em;
  padding: 8px 10px;
  border-radius: var(--bs-radius-small);
  border: 1px solid var(--bs-divider);
  background: var(--bs-card-bg);
  color: var(--bs-primary-text);
}
.bs-field .bs-field__error { color: var(--bs-error); font-size: 0.85em; }
.bs-field-row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
.bs-checkbox-row { display: flex; align-items: center; gap: 8px; font-size: 0.9em; }

.bs-hexfield input { font-family: var(--bs-mono); letter-spacing: 0.04em; }

.bs-mono { font-family: var(--bs-mono); }

.bs-table-wrap { overflow-x: auto; border: 1px solid var(--bs-divider); border-radius: var(--bs-radius-small); }
.bs-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
.bs-table th, .bs-table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--bs-divider); vertical-align: middle; }
.bs-table th { color: var(--bs-secondary-text); font-weight: 600; position: sticky; top: 0; background: var(--bs-card-bg); }
.bs-table tbody tr:last-child td { border-bottom: none; }
.bs-table tbody tr[data-canary='true'] { background: var(--bs-divider); }
.bs-table__empty { padding: 20px; text-align: center; color: var(--bs-secondary-text); }

.bs-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-radius: var(--bs-radius-small);
  padding: 2px 8px;
  font-size: 0.8em;
  background: var(--bs-divider);
  color: var(--bs-primary-text);
}

.bs-rssi-bar { width: 64px; height: 6px; border-radius: 3px; background: var(--bs-divider); overflow: hidden; display: inline-block; }
.bs-rssi-bar__fill { height: 100%; background: var(--bs-primary); }
.bs-rssi-bar__fill[data-tone='weak'] { background: var(--bs-error); }
.bs-rssi-bar__fill[data-tone='ok'] { background: var(--bs-warning); }
.bs-rssi-bar__fill[data-tone='strong'] { background: var(--bs-success); }

.bs-banner {
  border-radius: var(--bs-radius-small);
  padding: 10px 14px;
  font-size: 0.9em;
  border: 1px solid var(--bs-divider);
  background: var(--bs-divider);
}
.bs-banner--error { border-color: var(--bs-error); color: var(--bs-error); background: color-mix(in srgb, var(--bs-error) 10%, transparent); }
.bs-banner--busy { border-color: var(--bs-warning); color: var(--bs-primary-text); background: color-mix(in srgb, var(--bs-warning) 12%, transparent); }

.bs-evidence { margin: 0; padding-left: 18px; color: var(--bs-secondary-text); font-size: 0.9em; }
.bs-evidence li { margin: 2px 0; }

.bs-empty { color: var(--bs-secondary-text); font-style: italic; font-size: 0.9em; }

dialog.bs-dialog {
  border: 1px solid var(--bs-divider);
  border-radius: var(--bs-radius);
  padding: 20px;
  max-width: 440px;
  width: calc(100vw - 48px);
  color: var(--bs-primary-text);
  background: var(--bs-card-bg);
  font-family: var(--bs-font);
}
dialog.bs-dialog::backdrop { background: rgba(0, 0, 0, 0.5); }
.bs-dialog__title { margin: 0 0 8px; font-size: 1.1em; font-weight: 600; }
.bs-dialog__message { margin: 0 0 18px; color: var(--bs-secondary-text); font-size: 0.95em; }
.bs-dialog__actions { display: flex; justify-content: flex-end; gap: 10px; }

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
