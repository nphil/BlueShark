// Step 2: Identify. Family match with evidence + decoded facts, then "Connect & enumerate" to
// list the GATT services/characteristics, marking the suggested write+notify channel and letting
// the operator pick another.

import { h, clear, withPreservedFocus } from '../components.js';
import { adoptSharedStyles } from '../styles.js';
import {
  formatFamilyBadge,
  formatEvidence,
  formatStarterCommandMessage,
  describeDedicatedIntegration,
  formatDecodedFacts,
  formatApiErrorMessage,
  shortUuid,
  formatProperties,
  isWritable,
  isNotifiable,
  describeCharacteristic,
} from '../format.js';

function findCharacteristic(services, uuid) {
  if (!Array.isArray(services) || !uuid) return null;
  for (const service of services) {
    const match = (service.characteristics ?? []).find((c) => c.uuid === uuid);
    if (match) return match;
  }
  return null;
}

class BsStepIdentify extends HTMLElement {
  constructor() {
    super();
    this._api = null;
    this._wizard = null;
    this._unsubWizard = null;
    // Addresses we've already auto-triggered identify()/getCommands() for. Deliberately plain
    // instance fields, not state-derived: a dispatch from inside _doRender can synchronously
    // re-enter _render() on this same element (it subscribes before its first render), which
    // would otherwise see a stale `state` snapshot once the outer call resumes and double-fire
    // the same effect. Set inside _runIdentify/_checkExisting themselves, as their very first
    // action, so it is visible to any re-entrant call regardless of which call sets it.
    this._identifyRequestedFor = null;
    this._existingCheckRequestedFor = null;
    this.attachShadow({ mode: 'open' });
    adoptSharedStyles(this.shadowRoot);
  }

  set api(value) {
    this._api = value;
  }

  set wizard(value) {
    if (this._unsubWizard) this._unsubWizard();
    this._wizard = value;
    // Deliberately no eager render here: this setter runs while the element may still be
    // detached (properties are assigned before append), and rendering can synchronously
    // dispatch (e.g. the existing-entry check), which would re-enter the panel's step-mounting
    // logic before it finishes bookkeeping the element being constructed right now.
    // connectedCallback() renders once mounting is actually complete.
    this._unsubWizard = value && this.isConnected ? value.subscribe(() => this._render()) : null;
  }

  get wizard() {
    return this._wizard;
  }

  connectedCallback() {
    if (this._wizard && !this._unsubWizard) this._unsubWizard = this._wizard.subscribe(() => this._render());
    this._render();
  }

  disconnectedCallback() {
    if (this._unsubWizard) {
      this._unsubWizard();
      this._unsubWizard = null;
    }
  }

  _runIdentify(address) {
    this._identifyRequestedFor = address;
    this._wizard.dispatch({ type: 'IDENTIFY_START' });
    this._api.identify(address).then(
      (result) => this._wizard.dispatch({ type: 'IDENTIFY_SUCCESS', matches: result.matches, decoded: result.decoded }),
      (err) => this._wizard.dispatch({ type: 'IDENTIFY_ERROR', error: { code: err.code, message: err.message } }),
    );
  }

  // A device already onboarded still advertises and can still be picked in Find (e.g. to add
  // more controls later). commands/get finding a map means Finish must update that entry
  // (commands/set) instead of creating a new one; any failure is treated as "no entry yet" so a
  // transient error never blocks the normal new-device path (create_entry will itself refuse
  // cleanly if an entry actually does exist).
  _checkExisting(address) {
    this._existingCheckRequestedFor = address;
    this._wizard.dispatch({ type: 'EXISTING_ENTRY_CHECK_START' });
    this._api.getCommands(address).then(
      (result) => this._wizard.dispatch({ type: 'EXISTING_ENTRY_FOUND', commandMap: result?.command_map }),
      () => this._wizard.dispatch({ type: 'EXISTING_ENTRY_NOT_FOUND' }),
    );
  }

  // Called by the panel when hass transitions from unavailable to ready, in case either
  // auto-triggered check above ran (and failed) before hass existed.
  retryConnection() {
    const address = this._wizard?.getState()?.find?.selectedAddress;
    if (!address) return;
    this._runIdentify(address);
    this._checkExisting(address);
  }

  _runEnumerate(address) {
    this._wizard.dispatch({ type: 'ENUMERATE_START' });
    this._api.enumerate(address).then(
      (result) => this._wizard.dispatch({ type: 'ENUMERATE_SUCCESS', services: result.services, suggested: result.suggested }),
      (err) => this._wizard.dispatch({ type: 'ENUMERATE_ERROR', error: { code: err.code, message: err.message } }),
    );
  }

  _renderMatch(match) {
    const badge = formatFamilyBadge({ ...match, name: match.name ?? match.id });
    const evidence = formatEvidence(match.evidence);
    const starterMessage = formatStarterCommandMessage(match);
    const dedicated = describeDedicatedIntegration(match, this._api?.hass?.config?.components);
    return h('div', { class: 'bs-match' }, [
      h('div', { class: 'bs-field-row', style: { alignItems: 'center' } }, [
        h('strong', {}, match.name ?? match.id ?? 'Unknown family'),
        badge?.confidenceLabel ? h('span', { class: 'bs-badge' }, badge.confidenceLabel) : null,
      ]),
      evidence.length ? h('ul', { class: 'bs-evidence' }, evidence.map((line) => h('li', {}, line))) : null,
      match.driver_url ? h('a', { href: match.driver_url, target: '_blank', rel: 'noopener noreferrer' }, 'Public driver reference') : null,
      starterMessage ? h('p', { class: 'bs-empty' }, starterMessage) : null,
      dedicated ? this._renderDedicatedIntegration(dedicated) : null,
    ]);
  }

  // A beginner-path callout: "a full integration exists" with either an "Installed - open it"
  // link to that integration's own config-entry page, or an outbound link when it is not
  // installed yet. Rendered as a hairline tile (bs-callout), never a second card fill.
  _renderDedicatedIntegration(dedicated) {
    const cta = dedicated.ctaHref
      ? h(
          'a',
          {
            href: dedicated.ctaHref,
            class: 'bs-btn bs-btn--text',
            target: dedicated.installed ? null : '_blank',
            rel: dedicated.installed ? null : 'noopener noreferrer',
          },
          dedicated.ctaLabel,
        )
      : null;
    return h('div', { class: 'bs-callout' }, [h('p', { style: { margin: '0' } }, dedicated.headline), cta]);
  }

  _renderChannelButtons(row, state) {
    const canWrite = isWritable(row.properties);
    const canNotify = isNotifiable(row.properties);
    const isWrite = state.identify.selectedCharacteristic === row.uuid;
    const isNotify = state.identify.selectedNotifyCharacteristic === row.uuid;
    return h('div', { class: 'bs-btn-row' }, [
      h(
        'button',
        {
          type: 'button',
          class: isWrite ? 'bs-btn bs-btn--primary' : 'bs-btn',
          disabled: !canWrite,
          'aria-pressed': String(isWrite),
          onClick: () => this._wizard.dispatch({ type: 'SELECT_CHANNEL', service: row.serviceUuid, characteristic: row.uuid }),
        },
        isWrite ? 'Write \u2713' : 'Use for writes',
      ),
      h(
        'button',
        {
          type: 'button',
          class: isNotify ? 'bs-btn bs-btn--primary' : 'bs-btn',
          disabled: !canNotify,
          'aria-pressed': String(isNotify),
          onClick: () => this._wizard.dispatch({ type: 'SELECT_NOTIFY_CHANNEL', characteristic: row.uuid }),
        },
        isNotify ? 'Notify \u2713' : 'Use for notify',
      ),
    ]);
  }

  _renderGattTable(state) {
    const services = state.identify.services ?? [];
    const rows = services.flatMap((service) => (service.characteristics ?? []).map((ch) => ({ ...ch, serviceUuid: service.uuid })));
    const table = document.createElement('bs-table');
    table.emptyMessage = 'No services found.';
    table.columns = [
      { key: 'service', label: 'Service', render: (row) => shortUuid(row.serviceUuid) },
      { key: 'characteristic', label: 'Characteristic', render: (row) => h('span', { class: 'bs-mono' }, shortUuid(row.uuid)) },
      {
        key: 'properties',
        label: 'Properties',
        render: (row) => h('div', { class: 'bs-field-row' }, formatProperties(row.properties).map((p) => h('span', { class: 'bs-badge' }, p))),
      },
      { key: 'channel', label: 'Channel', render: (row) => this._renderChannelButtons(row, state) },
    ];
    table.rows = rows;
    return table;
  }

  _render() {
    if (!this._wizard) return;
    withPreservedFocus(this.shadowRoot, () => this._doRender());
  }

  _doRender() {
    const state = this._wizard.getState();
    const address = state.find.selectedAddress;
    clear(this.shadowRoot);

    if (!address) {
      this.shadowRoot.append(h('p', { class: 'bs-empty' }, 'Pick a device in Find first.'));
      return;
    }

    const hassReady = Boolean(this._api?.hass?.callWS);
    if (hassReady && this._identifyRequestedFor !== address) {
      this._runIdentify(address);
    }
    if (hassReady && this._existingCheckRequestedFor !== address) {
      this._checkExisting(address);
    }

    const sections = [];

    if (state.identify.isExistingDevice) {
      sections.push(
        h(
          'div',
          { class: 'bs-banner' },
          `This device already has a BlueShark entry with ${state.learn.commandOrder.length} control${state.learn.commandOrder.length === 1 ? '' : 's'}. Continuing will let you add more; Finish will update the existing entry instead of creating a new one.`,
        ),
      );
    }

    if (state.identify.loadingIdentify) {
      sections.push(h('p', { class: 'bs-empty' }, 'Identifying\u2026'));
    } else if (state.identify.identifyError) {
      sections.push(
        h('div', { class: 'bs-banner bs-banner--error' }, formatApiErrorMessage(state.identify.identifyError)),
        h('button', { type: 'button', class: 'bs-btn', onClick: () => this._runIdentify(address) }, 'Retry'),
      );
    } else if (Array.isArray(state.identify.matches)) {
      if (state.identify.matches.length === 0) {
        sections.push(h('p', { class: 'bs-empty' }, 'No known family matched this device. You can still probe it manually in Learn.'));
      } else {
        sections.push(...state.identify.matches.map((match) => this._renderMatch(match)));
      }
      const decoded = formatDecodedFacts(state.identify.decoded);
      if (decoded.length) {
        sections.push(
          h(
            'dl',
            { class: 'bs-field-row' },
            decoded.flatMap((fact) => [
              h('dt', { class: 'bs-empty', style: { margin: '0' } }, `${fact.label}:`),
              h('dd', { style: { margin: '0 16px 0 4px' } }, fact.value),
            ]),
          ),
        );
      }
    }

    const device = state.devices[address];
    if (device?.connectable === false) {
      sections.push(h('div', { class: 'bs-banner' }, 'This advertisement did not report as connectable; enumerating may fail or take longer.'));
    }

    const canEnumerate = !state.identify.loadingEnumerate;
    sections.push(
      h(
        'button',
        { type: 'button', class: 'bs-btn bs-btn--primary', disabled: !canEnumerate, onClick: () => this._runEnumerate(address) },
        state.identify.loadingEnumerate ? 'Connecting\u2026' : state.identify.services ? 'Re-enumerate' : 'Connect & enumerate',
      ),
    );

    if (state.identify.enumerateError) {
      sections.push(h('div', { class: 'bs-banner bs-banner--error' }, formatApiErrorMessage(state.identify.enumerateError)));
    }

    if (state.identify.services) {
      const suggested = state.identify.suggested;
      if (suggested) {
        const ch = findCharacteristic(state.identify.services, suggested.characteristic);
        sections.push(
          h('p', { class: 'bs-empty' }, `Suggested channel: ${ch ? describeCharacteristic(ch) : shortUuid(suggested.characteristic)}`),
        );
      } else {
        sections.push(h('p', { class: 'bs-empty' }, 'No write+notify pair was found automatically \u2014 pick one below.'));
      }
      if (state.advanced || !suggested) {
        sections.push(this._renderGattTable(state));
      } else {
        sections.push(
          h('p', { class: 'bs-empty' }, 'Using the suggested channel automatically. Switch on Advanced (top of the page) to see the raw GATT table and choose a different one.'),
        );
      }
    }

    const canContinue = Boolean(state.identify.services && state.identify.selectedCharacteristic && state.identify.codecId);
    sections.push(
      h(
        'div',
        { class: 'bs-btn-row' },
        h(
          'button',
          {
            type: 'button',
            class: 'bs-btn bs-btn--primary',
            disabled: !canContinue,
            onClick: () => this._wizard.dispatch({ type: 'GO_TO_STEP', step: 'learn' }),
          },
          'Continue to Learn',
        ),
      ),
    );

    const card = document.createElement('bs-card');
    card.heading = 'Identify';
    card.subheading = 'What is this device, and how do we talk to it?';
    card.status = canContinue ? 'complete' : address ? 'active' : 'pending';
    card.append(h('div', {}, sections));
    this.shadowRoot.append(card);
  }
}

if (!customElements.get('bs-step-identify')) customElements.define('bs-step-identify', BsStepIdentify);
