// Entry point for the BlueShark custom panel. Registered by the integration as a `_panel_custom`
// with `module_url` pointing at this file (it uses import/export, so it must load as an ES
// module, not a classic script). Home Assistant creates one `<blueshark-panel>` and assigns
// `hass`, `narrow`, `route` and `panel` as plain properties on every relevant update.

import { adoptSharedStyles } from './styles.js';
import { createWizard, STEP_ORDER, reachableSteps } from './wizard.js';
import { BlueSharkApi } from './api.js';
import { h, clear } from './components.js';
import './steps/find.js';
import './steps/identify.js';
import './steps/learn.js';
import './steps/finish.js';

/** Inline SVG so the panel needs none of HA's internal icon elements. */
function svgIcon(path) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('aria-hidden', 'true');
  const d = document.createElementNS(ns, 'path');
  d.setAttribute('d', path);
  d.setAttribute('fill', 'currentColor');
  svg.append(d);
  return svg;
}

const STEP_LABELS = { find: 'Find', identify: 'Identify', learn: 'Learn', finish: 'Finish' };
const STEP_TAGS = { find: 'bs-step-find', identify: 'bs-step-identify', learn: 'bs-step-learn', finish: 'bs-step-finish' };

class BlueSharkPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._narrow = false;
    this._route = null;
    this._panel = null;
    this._wizard = createWizard();
    this._api = new BlueSharkApi(null);
    this._mountedStep = null;
    this._unsubWizard = null;

    this.attachShadow({ mode: 'open' });
    adoptSharedStyles(this.shadowRoot);

    this._stepper = document.createElement('bs-stepper');
    this._stepper.addEventListener('step-select', (event) => {
      this._wizard.dispatch({ type: 'GO_TO_STEP', step: event.detail.id });
    });

    this._stepHost = h('div', { class: 'bs-content' });
    // Home Assistant renders a custom panel full-bleed: the panel itself owns the top bar, and on
    // a narrow viewport it is the ONLY thing that can reopen the sidebar (the companion apps hide
    // it, and there is no browser chrome to escape through). The documented mechanism is the
    // window-level `hass-toggle-menu` event that `ha-menu-button` fires, so the button below is a
    // real sidebar toggle rather than a decorative icon - and it is hidden when HA is already
    // showing a persistent sidebar, where a second control would be meaningless.
    this._menuButton = h(
      'button',
      {
        class: 'bs-menu-button',
        type: 'button',
        title: 'Open the Home Assistant sidebar',
        'aria-label': 'Open the Home Assistant sidebar',
        onclick: () => this._toggleSidebar(),
      },
      // mdi:menu, inlined: a panel must not depend on HA's internal icon components.
      [svgIcon('M3 6h18v2H3V6m0 5h18v2H3v-2m0 5h18v2H3v-2z')],
    );
    const header = h('header', { class: 'bs-appbar' }, [
      this._menuButton,
      h('div', { class: 'bs-appbar-titles' }, [
        h('h1', {}, 'BlueShark'),
        h('p', {}, 'Add any BLE device through a guided, evidence-first wizard.'),
      ]),
    ]);
    const shell = h('div', { class: 'bs-shell' }, [this._stepper, this._stepHost]);
    this.shadowRoot.append(header, shell);
  }

  /**
   * Asks Home Assistant to open its sidebar. `hass-toggle-menu` is the event the frontend listens
   * for on the window; it must bubble out of the shadow root, hence `composed: true`.
   */
  _toggleSidebar() {
    this.dispatchEvent(new CustomEvent('hass-toggle-menu', { bubbles: true, composed: true }));
  }

  set hass(value) {
    const wasReady = Boolean(this._hass && typeof this._hass.callWS === 'function');
    this._hass = value;
    this._api.hass = value;
    const isReady = Boolean(value && typeof value.callWS === 'function');
    if (!wasReady && isReady && typeof this._mountedStep?.retryConnection === 'function') {
      this._mountedStep.retryConnection();
    }
  }

  get hass() {
    return this._hass;
  }

  set narrow(value) {
    this._narrow = Boolean(value);
    if (this._narrow) this.setAttribute('narrow', '');
    else this.removeAttribute('narrow');
    // HA sets `narrow` exactly when it has collapsed the sidebar out of view.
    if (this._menuButton) this._menuButton.hidden = !this._narrow;
  }

  get narrow() {
    return this._narrow;
  }

  set route(value) {
    this._route = value;
  }

  get route() {
    return this._route;
  }

  set panel(value) {
    this._panel = value;
  }

  get panel() {
    return this._panel;
  }

  connectedCallback() {
    if (!this._unsubWizard) this._unsubWizard = this._wizard.subscribe(() => this._renderStepper());
    this._renderStepper();
  }

  disconnectedCallback() {
    if (this._unsubWizard) {
      this._unsubWizard();
      this._unsubWizard = null;
    }
  }

  _renderStepper() {
    const state = this._wizard.getState();
    const reachable = reachableSteps(state);
    const currentIndex = STEP_ORDER.indexOf(state.step);
    this._stepper.steps = STEP_ORDER.map((id, index) => ({
      id,
      label: STEP_LABELS[id],
      reachable: reachable.includes(id),
      status: id === state.step ? 'active' : reachable.includes(id) && index < currentIndex ? 'complete' : 'pending',
    }));
    this._stepper.active = state.step;
    this._syncActiveStep(state);
  }

  // Only replaces the mounted step element when the active step itself changes; any other
  // wizard-state change is handled by that element's own subscription, so it never loses its own
  // in-progress UI (typed-but-not-yet-submitted form fields, open dialogs, etc.).
  _syncActiveStep(state) {
    const tag = STEP_TAGS[state.step];
    if (this._mountedStep && this._mountedStep.tagName.toLowerCase() === tag) return;
    clear(this._stepHost);
    const el = document.createElement(tag);
    el.api = this._api;
    el.wizard = this._wizard;
    // Assigned before append: append() connects el synchronously, which runs its
    // connectedCallback, which can render and dispatch (e.g. the existing-entry check),
    // which notifies this panel's own subscriber and re-enters _syncActiveStep before this
    // call would otherwise have returned. That re-entrant call must see the real _mountedStep
    // (and take the early-return above) instead of racing to mount a second, duplicate element.
    this._mountedStep = el;
    this._stepHost.append(el);
  }
}

if (!customElements.get('blueshark-panel')) customElements.define('blueshark-panel', BlueSharkPanel);
