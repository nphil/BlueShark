// Step 4: Finish. Review the command map, name the device, create its entities. For a device
// that already has an entry (found via commands/get back in Identify), this updates that entry's
// command map instead, since create_entry refuses an address that is already configured.

import { h, clear, uid, withPreservedFocus, textField } from '../components.js';
import { adoptSharedStyles } from '../styles.js';
import { formatApiErrorMessage, entryDashboardUrl } from '../format.js';
import { canCreateEntry, starterCommandMapForCodec } from '../wizard.js';

class BsStepFinish extends HTMLElement {
  constructor() {
    super();
    this._api = null;
    this._wizard = null;
    this._unsubWizard = null;
    this._nameId = uid('finish-name');
    this.attachShadow({ mode: 'open' });
    adoptSharedStyles(this.shadowRoot);
    this._contentRoot = h('div');
    this.shadowRoot.append(this._contentRoot);
  }

  set api(value) {
    this._api = value;
  }

  set wizard(value) {
    if (this._unsubWizard) this._unsubWizard();
    this._wizard = value;
    // No eager render: see the identical comment in identify.js's setter. connectedCallback()
    // renders once this element is actually mounted.
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

  async _runSubmit(state) {
    const address = state.find.selectedAddress;
    this._wizard.dispatch({ type: 'CREATE_ENTRY_START' });
    try {
      if (state.identify.isExistingDevice) {
        await this._api.setCommands({ address, commandMap: state.learn.commandMap });
        this._wizard.dispatch({ type: 'CREATE_ENTRY_SUCCESS', entryId: null });
      } else {
        const result = await this._api.createEntry({
          address,
          name: state.finish.name.trim(),
          codecId: state.identify.codecId,
          characteristic: state.identify.selectedCharacteristic,
          commandMap: state.learn.commandMap,
        });
        this._wizard.dispatch({ type: 'CREATE_ENTRY_SUCCESS', entryId: result?.entry_id ?? null });
      }
    } catch (err) {
      this._wizard.dispatch({ type: 'CREATE_ENTRY_ERROR', error: { code: err.code, message: err.message } });
    }
  }

  _renderReviewTable(state) {
    const table = document.createElement('bs-table');
    table.emptyMessage = 'No controls were added \u2014 you can still create the device and add controls later from Learn.';
    table.columns = [
      { key: 'name', label: 'Name', render: (key) => this._renderNameField(key, state) },
      { key: 'kind', label: 'Type', render: (key) => state.learn.commandMap[key]?.kind ?? '' },
      { key: 'note', label: 'Note', render: (key) => state.learn.commandMap[key]?.note ?? '' },
      {
        key: 'remove',
        label: '',
        render: (key) =>
          h(
            'button',
            {
              type: 'button',
              class: 'bs-btn bs-btn--text',
              disabled: state.finish.created,
              onClick: () => this._wizard.dispatch({ type: 'REMOVE_COMMAND', key }),
            },
            'Remove',
          ),
      },
    ];
    table.rows = state.learn.commandOrder;
    return table;
  }

  // A beginner arriving with a starter_command_map pre-filled (see SEED_STARTER_COMMANDS in
  // _doRender) can untick (Remove) or rename any of these, same as anything learned by hand.
  _renderNameField(key, state) {
    const { element } = textField({
      id: `finish-name-${key}`,
      value: state.learn.commandMap[key]?.name ?? key,
      disabled: state.finish.created,
      onChange: (value) => this._wizard.dispatch({ type: 'RENAME_COMMAND', key, name: value }),
    });
    return element;
  }

  _render() {
    if (!this._wizard) return;
    withPreservedFocus(this.shadowRoot, () => this._doRender());
  }

  _doRender() {
    const state = this._wizard.getState();
    clear(this._contentRoot);

    if (!state.find.selectedAddress) {
      this._contentRoot.append(h('p', { class: 'bs-empty' }, 'Pick a device in Find first.'));
      return;
    }

    // Beginner path: a brand-new device (never an already-configured one -- see the module
    // comment) gets its draft command map pre-filled from starter_command_map exactly once, as
    // soon as isExistingDevice resolves to false. Idempotent: SEED_STARTER_COMMANDS itself
    // no-ops once finish.starterSeeded is set, so a removed starter entry never comes back.
    if (!state.finish.starterSeeded && state.identify.isExistingDevice === false) {
      this._wizard.dispatch({
        type: 'SEED_STARTER_COMMANDS',
        starterCommandMap: starterCommandMapForCodec(state.identify.matches, state.identify.codecId),
      });
    }

    const isExisting = state.identify.isExistingDevice;
    const reviewFields = [this._renderReviewTable(state)];
    if (state.learn.commandOrder.length) {
      reviewFields.push(h('p', { class: 'bs-empty' }, 'Untick (Remove) or rename any control above before creating the device.'));
    }
    const sections = [h('div', { class: 'bs-section' }, [h('h3', {}, 'Review'), ...reviewFields])];

    if (isExisting === null) {
      sections.push(h('p', { class: 'bs-empty' }, 'Checking whether this device is already configured\u2026'));
    } else if (isExisting) {
      sections.push(h('p', { class: 'bs-empty' }, 'This device already has a BlueShark entry \u2014 saving updates its command map; no new device is created.'));
    } else {
      const nameField = textField({
        label: 'Device name',
        id: this._nameId,
        value: state.finish.name,
        disabled: state.finish.created,
        onInput: (value) => this._wizard.dispatch({ type: 'SET_DEVICE_NAME', name: value }),
      });
      sections.push(nameField.element);
    }

    if (state.finish.createError) {
      sections.push(h('div', { class: 'bs-banner bs-banner--error' }, formatApiErrorMessage(state.finish.createError)));
    }

    if (state.finish.created) {
      sections.push(
        h('div', { class: 'bs-banner' }, isExisting ? 'Command map saved.' : 'Device created.'),
        h('div', { class: 'bs-btn-row' }, [
          h('a', { href: entryDashboardUrl(), target: '_blank', rel: 'noopener noreferrer', class: 'bs-btn' }, 'Open BlueShark integration page'),
          h(
            'button',
            { type: 'button', class: 'bs-btn bs-btn--primary', onClick: () => this._wizard.dispatch({ type: 'RESTART' }) },
            'Onboard another device',
          ),
        ]),
      );
    } else {
      const canSubmit = canCreateEntry(state) && !state.finish.creating && isExisting !== null;
      sections.push(
        h('div', { class: 'bs-btn-row' }, [
          h(
            'button',
            { type: 'button', class: 'bs-btn bs-btn--primary', disabled: !canSubmit, onClick: () => this._runSubmit(state) },
            state.finish.creating ? 'Saving\u2026' : isExisting ? 'Save command map' : 'Create entities',
          ),
        ]),
      );
    }

    const card = document.createElement('bs-card');
    card.heading = 'Finish';
    card.subheading = 'Review, name, and create.';
    card.status = state.finish.created ? 'complete' : 'active';
    card.append(h('div', {}, sections));
    this._contentRoot.append(card);
  }
}

if (!customElements.get('bs-step-finish')) customElements.define('bs-step-finish', BsStepFinish);
