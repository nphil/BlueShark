// Step 3: Learn. The prober: codec picker, "Try a command" (single opcode or raw bytes),
// "Sweep" (bounded, canary-guarded, destructive-gated) with a live results table, "Listen" for
// notify frames, and building the command map one control at a time. Raw GATT/codec-override/
// raw-send/JSON-editing tools and "include destructive" live behind state.advanced; nothing is
// ever silently skipped -- blocked destructive opcodes are always listed with their reason.

import { h, clear, uid, chip, liveRegion, textField, selectField, segmentedField, textareaField, withPreservedFocus } from '../components.js';
import { adoptSharedStyles } from '../styles.js';
import {
  CODEC_CATALOG,
  parseHex,
  parseOpcode,
  buildPayloadFromOpcode,
  formatVerdict,
  formatElapsed,
  formatElapsedSince,
  formatApiErrorMessage,
  formatByte,
  formatSafetyList,
  displayHex,
} from '../format.js';
import {
  buildCommandMapEntry,
  uniqueCommandKey,
  DEFAULT_LISTEN_SECONDS,
  CANARY_INTERVAL,
  describeBusy,
  safetyForCodec,
  blockedSweepOpcodes,
  validateCommandMapJson,
} from '../wizard.js';

class BsStepLearn extends HTMLElement {
  constructor() {
    super();
    this._api = null;
    this._wizard = null;
    this._unsubWizard = null;
    this._unsubSweep = null;
    this._unsubListen = null;
    this._listenTimer = null;

    this._tryMode = 'opcode';
    this._tryOpcode = '';
    this._tryArgumentHex = '';
    this._tryRawHex = '';
    this._sweepArgumentHexDraft = null;
    this._listenSeconds = DEFAULT_LISTEN_SECONDS;

    this._builder = null;
    this._builderError = null;

    this._commandMapJsonDraft = null;
    this._commandMapJsonError = null;

    this._tryOpcodeId = uid('learn-try-opcode');
    this._tryArgHexId = uid('learn-try-arg');
    this._tryRawHexId = uid('learn-try-raw');
    this._sweepStartId = uid('learn-sweep-start');
    this._sweepEndId = uid('learn-sweep-end');
    this._sweepArgHexId = uid('learn-sweep-arg');
    this._sweepDelayId = uid('learn-sweep-delay');
    this._sweepAwaitId = uid('learn-sweep-await');
    this._listenSecondsId = uid('learn-listen-seconds');
    this._builderNameId = uid('learn-builder-name');
    this._builderNoteId = uid('learn-builder-note');
    this._builderMinId = uid('learn-builder-min');
    this._builderMaxId = uid('learn-builder-max');
    this._builderSecondaryOpcodeId = uid('learn-builder-secondary-opcode');
    this._builderSecondaryArgId = uid('learn-builder-secondary-arg');
    this._commandMapJsonId = uid('learn-commandmap-json');

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
    this._stopSweep();
    this._stopListen();
  }

  _stopSweep() {
    if (typeof this._unsubSweep === 'function') {
      try {
        this._unsubSweep();
      } catch {
        // Connection already gone.
      }
    }
    this._unsubSweep = null;
  }

  _stopListen() {
    if (typeof this._unsubListen === 'function') {
      try {
        this._unsubListen();
      } catch {
        // Connection already gone.
      }
    }
    this._unsubListen = null;
    if (this._listenTimer) {
      clearTimeout(this._listenTimer);
      this._listenTimer = null;
    }
  }

  // --- Try a command -----------------------------------------------------------

  async _runTry(state) {
    const address = state.find.selectedAddress;
    const characteristic = state.identify.selectedCharacteristic;
    const codecId = state.identify.codecId;
    let payloadHex;
    let promoteFields = null;
    let framed = false;
    try {
      if (this._effectiveTryMode(state) === 'opcode') {
        const opcode = parseOpcode(this._tryOpcode === '' ? NaN : Number(this._tryOpcode));
        payloadHex = buildPayloadFromOpcode(opcode, this._tryArgumentHex);
        const { hex: argumentHex } = parseHex(this._tryArgumentHex, { allowEmpty: true });
        promoteFields = { opcode, argumentHex };
      } else {
        payloadHex = parseHex(this._tryRawHex, { allowEmpty: false }).hex;
        framed = true;
      }
    } catch (err) {
      this._wizard.dispatch({ type: 'TRY_ERROR', error: { code: null, message: err.message } });
      return;
    }
    this._wizard.dispatch({ type: 'TRY_START' });
    try {
      const result = await this._api.send({
        address,
        characteristic,
        payloadHex,
        codecId,
        framed,
        awaitResponseMs: state.learn.sweep.config.awaitResponseMs,
      });
      this._wizard.dispatch({ type: 'TRY_RESULT', result: { ...result, ...promoteFields } });
    } catch (err) {
      this._wizard.dispatch({ type: 'TRY_ERROR', error: { code: err.code, message: err.message } });
    }
  }

  // --- Sweep ---------------------------------------------------------------------

  async _startSweep(state) {
    const cfg = state.learn.sweep.config;
    const argumentHex = this._sweepArgumentHexDraft ?? cfg.argumentHex;
    let start;
    let end;
    try {
      start = parseOpcode(Number(cfg.start));
      end = parseOpcode(Number(cfg.end));
      if (end < start) throw new Error('End opcode must be greater than or equal to the start opcode.');
      parseHex(argumentHex, { allowEmpty: true });
    } catch (err) {
      this._wizard.dispatch({ type: 'SWEEP_ERROR', error: { code: null, message: err.message } });
      return;
    }
    this._wizard.dispatch({ type: 'SWEEP_CONFIGURE', config: { argumentHex } });
    this._wizard.dispatch({ type: 'SWEEP_START', runId: null });
    try {
      const unsubscribe = await this._api.startSweep(
        {
          address: state.find.selectedAddress,
          characteristic: state.identify.selectedCharacteristic,
          codecId: state.identify.codecId,
          start,
          end,
          argumentHex,
          includeDestructive: cfg.includeDestructive,
          stepDelayMs: cfg.stepDelayMs,
          awaitResponseMs: cfg.awaitResponseMs,
        },
        (msg) => {
          if (!msg || typeof msg !== 'object') return;
          if ('run_id' in msg) {
            this._wizard.dispatch({ type: 'SWEEP_RUN_ID', runId: msg.run_id });
          } else if (msg.error) {
            this._wizard.dispatch({ type: 'SWEEP_ERROR', error: msg.error });
            this._stopSweep();
          } else if (msg.done) {
            this._wizard.dispatch({ type: 'SWEEP_DONE', summary: msg });
            this._stopSweep();
          } else {
            this._wizard.dispatch({ type: 'SWEEP_PROGRESS', row: msg });
          }
        },
      );
      this._unsubSweep = unsubscribe;
    } catch (err) {
      this._wizard.dispatch({ type: 'SWEEP_ERROR', error: { code: err.code, message: err.message } });
    }
  }

  async _stopSweepRun(state) {
    const runId = state.learn.sweep.runId;
    this._stopSweep();
    this._wizard.dispatch({ type: 'SWEEP_STOPPED' });
    if (runId) {
      try {
        await this._api.stopSweep(runId);
      } catch {
        // Best-effort: the panel has already dropped its own view of the run either way.
      }
    }
  }

  _effectiveTryMode(state) {
    return state.advanced ? this._tryMode : 'opcode';
  }

  // --- Listen ----------------------------------------------------------------------

  async _startListen(state) {
    const seconds = this._listenSeconds;
    this._wizard.dispatch({ type: 'LISTEN_START', seconds, startedAtMs: Date.now() });
    try {
      const unsubscribe = await this._api.listen(
        { address: state.find.selectedAddress, characteristic: state.identify.selectedNotifyCharacteristic, seconds },
        (msg) => {
          if (msg && msg.done) {
            this._wizard.dispatch({ type: 'LISTEN_STOP' });
            this._stopListen();
          } else {
            this._wizard.dispatch({ type: 'LISTEN_FRAME', frame: msg });
          }
        },
      );
      this._unsubListen = unsubscribe;
      // Safety net if the engine never sends {done:true} (e.g. a dropped connection).
      this._listenTimer = setTimeout(() => {
        this._wizard.dispatch({ type: 'LISTEN_STOP' });
        this._stopListen();
      }, (seconds + 5) * 1000);
    } catch (err) {
      this._wizard.dispatch({ type: 'LISTEN_ERROR', error: { code: err.code, message: err.message } });
    }
  }

  _stopListenNow() {
    this._stopListen();
    this._wizard.dispatch({ type: 'LISTEN_STOP' });
  }

  // --- "Make this a control" builder -----------------------------------------------

  _openBuilder(primary) {
    this._builder = { kind: 'button', name: '', note: '', primary, primaryRole: 'on', secondaryOpcode: '', secondaryArgumentHex: '', min: 0, max: 255 };
    this._builderError = null;
    this._render();
  }

  _closeBuilder() {
    this._builder = null;
    this._builderError = null;
    this._render();
  }

  _submitBuilder(state) {
    const b = this._builder;
    try {
      const common = { name: b.name, note: b.note };
      let entry;
      if (b.kind === 'button') {
        entry = buildCommandMapEntry('button', { ...common, opcode: b.primary.opcode, argumentHex: b.primary.argumentHex });
      } else if (b.kind === 'number') {
        entry = buildCommandMapEntry('number', { ...common, opcode: b.primary.opcode, min: Number(b.min), max: Number(b.max) });
      } else {
        const secondary = { opcode: parseOpcode(Number(b.secondaryOpcode)), argumentHex: b.secondaryArgumentHex };
        const on = b.primaryRole === 'on' ? b.primary : secondary;
        const off = b.primaryRole === 'on' ? secondary : b.primary;
        entry = buildCommandMapEntry('switch', { ...common, on, off });
      }
      const key = uniqueCommandKey(b.name, state.learn.commandOrder);
      this._wizard.dispatch({ type: 'ADD_COMMAND', key, entry });
      this._closeBuilder();
    } catch (err) {
      this._builderError = err.message;
      this._render();
    }
  }

  _renderPromoteButton(primary) {
    return h('button', { type: 'button', class: 'bs-btn bs-btn--text', onClick: () => this._openBuilder(primary) }, 'Make this a control');
  }

  _renderBuilderPanel(state) {
    if (!this._builder) return null;
    const b = this._builder;
    const fields = [];

    const nameField = textField({ label: 'Name', id: this._builderNameId, value: b.name, onInput: (v) => { b.name = v; } });
    const noteField = textField({ label: 'Note (optional)', id: this._builderNoteId, value: b.note, onInput: (v) => { b.note = v; } });
    const kindField = segmentedField({
      label: 'Control type',
      id: uid('learn-builder-kind'),
      value: b.kind,
      options: [
        { value: 'button', label: 'Button (fires once)' },
        { value: 'number', label: 'Number (adjustable value)' },
        { value: 'switch', label: 'Switch (on / off)' },
      ],
      onChange: (v) => {
        b.kind = v;
        this._render();
      },
    });
    fields.push(nameField.element, noteField.element, kindField.element);

    if (b.kind === 'button') {
      fields.push(h('p', { class: 'bs-empty' }, `Sends opcode ${formatByte(b.primary.opcode)} with argument ${displayHex(b.primary.argumentHex)}.`));
    } else if (b.kind === 'number') {
      const minField = textField({ label: 'Minimum', type: 'number', id: this._builderMinId, value: String(b.min), onInput: (v) => { b.min = v; } });
      const maxField = textField({ label: 'Maximum', type: 'number', id: this._builderMaxId, value: String(b.max), onInput: (v) => { b.max = v; } });
      fields.push(
        h('p', { class: 'bs-empty' }, `Opcode ${formatByte(b.primary.opcode)}; the live value becomes the second byte.`),
        h('div', { class: 'bs-field-row' }, [minField.element, maxField.element]),
      );
    } else {
      const roleField = selectField({
        label: `Opcode ${formatByte(b.primary.opcode)} (argument ${displayHex(b.primary.argumentHex)}) is`,
        id: uid('learn-builder-role'),
        value: b.primaryRole,
        options: [
          { value: 'on', label: 'the ON payload' },
          { value: 'off', label: 'the OFF payload' },
        ],
        onChange: (v) => { b.primaryRole = v; },
      });
      const otherOpcodeField = textField({
        label: `Other opcode (${b.primaryRole === 'on' ? 'off' : 'on'})`,
        id: this._builderSecondaryOpcodeId,
        value: b.secondaryOpcode,
        onInput: (v) => { b.secondaryOpcode = v; },
      });
      const otherArgField = textField({
        label: 'Other argument hex (optional)',
        id: this._builderSecondaryArgId,
        value: b.secondaryArgumentHex,
        onInput: (v) => { b.secondaryArgumentHex = v; },
      });
      fields.push(
        h('p', { class: 'bs-empty' }, 'A switch needs both payloads; look up the other opcode\u2019s row in the sweep table above.'),
        roleField.element,
        h('div', { class: 'bs-field-row' }, [otherOpcodeField.element, otherArgField.element]),
      );
    }

    if (this._builderError) fields.push(h('p', { class: 'bs-field__error' }, this._builderError));

    fields.push(
      h('div', { class: 'bs-btn-row' }, [
        h('button', { type: 'button', class: 'bs-btn bs-btn--primary', onClick: () => this._submitBuilder(state) }, 'Add control'),
        h('button', { type: 'button', class: 'bs-btn', onClick: () => this._closeBuilder() }, 'Cancel'),
      ]),
    );

    return h('section', { class: 'bs-section bs-builder-panel' }, [
      h('h3', {}, 'Make this a control'),
      ...fields,
    ]);
  }

  // --- Section renderers -------------------------------------------------------------

  _renderCodecSection(state) {
    if (!state.advanced) return null;
    const field = selectField({
      label: 'Codec',
      id: uid('learn-codec'),
      value: state.identify.codecId ?? '',
      options: CODEC_CATALOG.map((entry) => ({ value: entry.id, label: entry.label })),
      onChange: (v) => this._wizard.dispatch({ type: 'SELECT_CODEC', codecId: v }),
    });
    return h('section', { class: 'bs-section' }, [h('h3', {}, 'Codec'), field.element]);
  }

  _renderTrySection(state) {
    const busy = Boolean(describeBusy(state));
    const mode = this._effectiveTryMode(state);
    const fields = [];

    if (state.advanced) {
      const modeField = segmentedField({
        label: 'Mode',
        id: uid('learn-try-mode'),
        value: this._tryMode,
        options: [
          { value: 'opcode', label: 'By opcode' },
          { value: 'raw', label: 'Raw bytes (skips codec framing)' },
        ],
        onChange: (v) => {
          this._tryMode = v;
          this._render();
        },
      });
      fields.push(modeField.element);
    }

    if (mode === 'opcode') {
      const opcodeField = textField({
        label: 'Opcode (0-255)',
        type: 'number',
        id: this._tryOpcodeId,
        value: this._tryOpcode,
        onInput: (v) => { this._tryOpcode = v; },
      });
      const argHexField = document.createElement('bs-hex-field');
      argHexField.id = this._tryArgHexId;
      argHexField.label = 'Argument hex (optional)';
      argHexField.allowEmpty = true;
      argHexField.value = this._tryArgumentHex;
      argHexField.addEventListener('value-change', (event) => { this._tryArgumentHex = event.detail.value; });
      fields.push(h('div', { class: 'bs-field-row' }, [opcodeField.element, argHexField]));
    } else {
      const rawField = document.createElement('bs-hex-field');
      rawField.id = this._tryRawHexId;
      rawField.label = 'Raw payload hex';
      rawField.allowEmpty = false;
      rawField.value = this._tryRawHex;
      rawField.addEventListener('value-change', (event) => { this._tryRawHex = event.detail.value; });
      fields.push(rawField);
    }

    fields.push(
      h('div', { class: 'bs-btn-row' }, [
        h(
          'button',
          { type: 'button', class: 'bs-btn bs-btn--primary', disabled: busy, onClick: () => this._runTry(state) },
          state.learn.tryPending ? 'Sending\u2026' : 'Try',
        ),
      ]),
    );

    if (state.learn.tryError) fields.push(h('div', { class: 'bs-banner bs-banner--error' }, formatApiErrorMessage(state.learn.tryError)));

    if (state.learn.tryResult) {
      const result = state.learn.tryResult;
      fields.push(
        h('div', { class: 'bs-field-row', style: { alignItems: 'center' } }, [
          h('span', { class: 'bs-mono' }, `sent ${displayHex(result.sent_hex)}`),
          h('span', { class: 'bs-mono' }, `reply ${displayHex(result.response_hex)}`),
          chip(formatVerdict(result.verdict, { inferred: state.identify.codecId === 'coolled' })),
          h('span', { class: 'bs-empty' }, formatElapsed(result.elapsed_ms)),
          result.opcode !== undefined ? this._renderPromoteButton({ opcode: result.opcode, argumentHex: result.argumentHex ?? '' }) : null,
        ]),
      );
    }

    return h('section', { class: 'bs-section' }, [h('h3', {}, 'Try a command'), ...fields]);
  }

  _renderSweepRowsTable(state) {
    const table = document.createElement('bs-table');
    table.emptyMessage = 'No results yet. Start a sweep to see live rows here.';
    table.columns = [
      { key: 'opcode', label: 'Opcode', render: (row) => formatByte(row.opcode) },
      { key: 'sent', label: 'Sent', render: (row) => h('span', { class: 'bs-mono' }, displayHex(row.sent_hex)) },
      { key: 'response', label: 'Response', render: (row) => h('span', { class: 'bs-mono' }, displayHex(row.response_hex)) },
      { key: 'verdict', label: 'Verdict', render: (row) => chip(formatVerdict(row.verdict, { inferred: state.identify.codecId === 'coolled' })) },
      { key: 'elapsed', label: 'Elapsed', render: (row) => formatElapsed(row.elapsed_ms) },
      { key: 'note', label: 'What happened', render: (row, index) => this._renderRowNoteInput(index, state) },
      {
        key: 'action',
        label: '',
        render: (row) =>
          row.canary
            ? h('span', { class: 'bs-badge' }, 'Canary check')
            : this._renderPromoteButton({ opcode: row.opcode, argumentHex: state.learn.sweep.config.argumentHex }),
      },
    ];
    table.rows = state.learn.sweep.rows;
    table.rowAttributes = (row) => ({ dataset: { canary: String(Boolean(row.canary)) } });
    return table;
  }

  _renderRowNoteInput(index, state) {
    const key = String(index);
    const { element } = textField({
      id: `learn-row-note-${index}`,
      value: state.learn.rowNotes[key] ?? '',
      placeholder: 'What did you observe?',
      onChange: (value) => this._wizard.dispatch({ type: 'SET_ROW_NOTE', rowKey: key, note: value }),
    });
    return element;
  }

  _renderSweepSection(state) {
    const cfg = state.learn.sweep.config;
    const running = state.learn.sweep.running;
    const busy = Boolean(describeBusy(state));

    const startField = textField({
      label: 'Start opcode',
      type: 'number',
      id: this._sweepStartId,
      value: String(cfg.start),
      onChange: (v) => this._wizard.dispatch({ type: 'SWEEP_CONFIGURE', config: { start: Number(v) } }),
    });
    const endField = textField({
      label: 'End opcode',
      type: 'number',
      id: this._sweepEndId,
      value: String(cfg.end),
      onChange: (v) => this._wizard.dispatch({ type: 'SWEEP_CONFIGURE', config: { end: Number(v) } }),
    });
    const delayField = textField({
      label: 'Delay between steps (ms)',
      type: 'number',
      id: this._sweepDelayId,
      value: String(cfg.stepDelayMs),
      onChange: (v) => this._wizard.dispatch({ type: 'SWEEP_CONFIGURE', config: { stepDelayMs: Number(v) } }),
    });
    const awaitField = textField({
      label: 'Await response (ms)',
      type: 'number',
      id: this._sweepAwaitId,
      value: String(cfg.awaitResponseMs),
      onChange: (v) => this._wizard.dispatch({ type: 'SWEEP_CONFIGURE', config: { awaitResponseMs: Number(v) } }),
    });

    const argHexField = document.createElement('bs-hex-field');
    argHexField.id = this._sweepArgHexId;
    argHexField.label = 'Shared argument hex (optional, appended after every opcode)';
    argHexField.allowEmpty = true;
    argHexField.value = this._sweepArgumentHexDraft ?? cfg.argumentHex;
    argHexField.addEventListener('value-change', (event) => { this._sweepArgumentHexDraft = event.detail.value; });

    const startStopBtn = running
      ? h('button', { type: 'button', class: 'bs-btn bs-btn--danger', onClick: () => this._stopSweepRun(state) }, 'Stop sweep')
      : h(
          'button',
          { type: 'button', class: 'bs-btn bs-btn--primary', disabled: busy, onClick: () => this._startSweep(state) },
          'Start sweep',
        );

    const announce = liveRegion('polite');
    if (running) {
      const last = state.learn.sweep.rows[state.learn.sweep.rows.length - 1];
      announce.textContent = last ? `Sweep running: opcode ${last.opcode}, ${last.index}/${last.total}` : 'Sweep starting\u2026';
    }

    const sections = [
      h('h3', {}, 'Sweep'),
      h('div', { class: 'bs-field-row' }, [startField.element, endField.element, delayField.element, awaitField.element]),
      argHexField,
      h('p', { class: 'bs-empty' }, `A canary check runs every ${CANARY_INTERVAL} probes either way, confirming the device is still responsive.`),
      this._renderDestructiveSection(state, cfg),
      h('div', { class: 'bs-btn-row' }, [startStopBtn]),
    ];

    if (state.learn.sweep.error) sections.push(h('div', { class: 'bs-banner bs-banner--error' }, formatApiErrorMessage(state.learn.sweep.error)));
    if (state.learn.sweep.summary) {
      const s = state.learn.sweep.summary;
      const parts = [`${s.accepted?.length ?? 0} accepted`, `${s.unknown?.length ?? 0} unknown`, `${s.no_response?.length ?? 0} no response`];
      sections.push(h('p', { class: 'bs-empty' }, `Sweep finished: ${parts.join(', ')}.${s.aborted_reason ? ` Stopped early: ${s.aborted_reason}.` : ''}`));
    }

    sections.push(announce, this._renderSweepRowsTable(state));

    return h('section', { class: 'bs-section' }, sections);
  }

  // Shows exactly which opcodes in the configured range are being held back for safety (from
  // `safety`, never a silent skip). Behind Advanced only: the hold-to-confirm control that
  // includes them anyway. Turning inclusion back off needs no confirmation -- it is the safe
  // direction.
  _renderDestructiveSection(state, cfg) {
    const safety = safetyForCodec(state.identify.matches, state.identify.codecId);
    const blockedRows = formatSafetyList(blockedSweepOpcodes(safety, { start: cfg.start, end: cfg.end, includeDestructive: cfg.includeDestructive }));
    const parts = [];

    if (blockedRows.length) {
      parts.push(
        h('div', { class: 'bs-callout' }, [
          h(
            'p',
            { style: { margin: '0' } },
            `${blockedRows.length} opcode${blockedRows.length === 1 ? '' : 's'} in this range ${blockedRows.length === 1 ? 'is' : 'are'} destructive and will be skipped:`,
          ),
          h(
            'ul',
            { class: 'bs-evidence' },
            blockedRows.map((row) => h('li', {}, [h('span', { class: 'bs-mono' }, row.opcodeLabel), ` \u2014 ${row.reason}`])),
          ),
        ]),
      );
    }

    if (state.advanced) {
      if (cfg.includeDestructive) {
        parts.push(
          h('div', { class: 'bs-field-row', style: { alignItems: 'center' } }, [
            chip({ tone: 'warning', label: 'Destructive opcodes included' }),
            h(
              'button',
              {
                type: 'button',
                class: 'bs-btn bs-btn--text',
                onClick: () => this._wizard.dispatch({ type: 'SWEEP_CONFIGURE', config: { includeDestructive: false } }),
              },
              'Exclude them again',
            ),
          ]),
        );
      } else {
        const holdBtn = document.createElement('bs-hold-button');
        holdBtn.label = 'Hold to include destructive opcodes';
        holdBtn.disabled = state.learn.sweep.running;
        holdBtn.addEventListener('confirm', () => this._wizard.dispatch({ type: 'SWEEP_CONFIGURE', config: { includeDestructive: true } }));
        parts.push(holdBtn);
      }
    }

    return h('div', { style: { display: 'flex', flexDirection: 'column', gap: '10px' } }, parts);
  }

  _renderListenSection(state) {
    const listen = state.learn.listen;
    const busy = Boolean(describeBusy(state));
    const notifyChar = state.identify.selectedNotifyCharacteristic;

    const secondsField = textField({
      label: 'Seconds to listen (0-600)',
      type: 'number',
      id: this._listenSecondsId,
      value: String(this._listenSeconds),
      disabled: listen.active,
      onChange: (v) => { this._listenSeconds = Math.min(600, Math.max(0, Number(v) || 0)); },
    });

    const startStopBtn = listen.active
      ? h('button', { type: 'button', class: 'bs-btn bs-btn--danger', onClick: () => this._stopListenNow() }, 'Stop watching')
      : h(
          'button',
          { type: 'button', class: 'bs-btn bs-btn--primary', disabled: busy || !notifyChar, onClick: () => this._startListen(state) },
          'Start listening',
        );

    const framesTable = document.createElement('bs-table');
    framesTable.emptyMessage = 'No frames yet.';
    const firstAtMs = listen.frames[0]?.at_ms ?? null;
    framesTable.columns = [
      { key: 'at', label: 'Time', render: (frame) => formatElapsedSince(frame.at_ms, firstAtMs) },
      { key: 'hex', label: 'Bytes', render: (frame) => h('span', { class: 'bs-mono' }, displayHex(frame.hex)) },
    ];
    framesTable.rows = listen.frames;

    const announce = liveRegion('polite');
    if (listen.active) announce.textContent = `Listening: ${listen.frames.length} frame${listen.frames.length === 1 ? '' : 's'} received`;

    const sections = [h('h3', {}, 'Listen'), h('div', { class: 'bs-field-row', style: { alignItems: 'flex-end' } }, [secondsField.element, startStopBtn])];
    if (!notifyChar) sections.push(h('p', { class: 'bs-empty' }, 'No notify channel is selected in Identify \u2014 listening is unavailable until one is.'));
    if (listen.active) {
      sections.push(
        h('p', { class: 'bs-empty' }, 'Listening holds this device\u2019s one BLE operation slot until the timer ends; "Stop watching" only stops updating this view.'),
      );
    }
    if (listen.error) sections.push(h('div', { class: 'bs-banner bs-banner--error' }, formatApiErrorMessage(listen.error)));
    sections.push(announce, framesTable);

    return h('section', { class: 'bs-section' }, sections);
  }

  _describeCommandEntry(entry) {
    if (!entry) return '';
    if (entry.kind === 'button') {
      return entry.payload_hex ? `payload ${displayHex(entry.payload_hex)}` : `opcode ${formatByte(entry.opcode)} + ${displayHex(entry.argument_hex)}`;
    }
    if (entry.kind === 'number') return `opcode ${formatByte(entry.opcode)}, range ${entry.min}\u2013${entry.max}`;
    if (entry.kind === 'switch') {
      const onOp = entry.on.opcode !== undefined ? formatByte(entry.on.opcode) : displayHex(entry.on.payload_hex);
      const offOp = entry.off.opcode !== undefined ? formatByte(entry.off.opcode) : displayHex(entry.off.payload_hex);
      return `on ${onOp} / off ${offOp}`;
    }
    return '';
  }

  _renderCommandMapSection(state) {
    const table = document.createElement('bs-table');
    table.emptyMessage = 'No controls added yet. Use "Make this a control" on a Try result or sweep row above.';
    table.columns = [
      { key: 'name', label: 'Name', render: (key) => state.learn.commandMap[key]?.name ?? key },
      { key: 'kind', label: 'Type', render: (key) => state.learn.commandMap[key]?.kind ?? '' },
      { key: 'detail', label: 'Detail', render: (key) => h('span', { class: 'bs-mono' }, this._describeCommandEntry(state.learn.commandMap[key])) },
      { key: 'note', label: 'Note', render: (key) => state.learn.commandMap[key]?.note ?? '' },
      {
        key: 'remove',
        label: '',
        render: (key) =>
          h('button', { type: 'button', class: 'bs-btn bs-btn--text', onClick: () => this._wizard.dispatch({ type: 'REMOVE_COMMAND', key }) }, 'Remove'),
      },
    ];
    table.rows = state.learn.commandOrder;
    const sections = [h('h3', {}, 'Command map'), table];
    if (state.advanced) sections.push(this._renderCommandMapJsonEditor(state));
    return h('section', { class: 'bs-section' }, sections);
  }

  // Advanced-only: the same command map as raw JSON. A local draft (like the sweep argument hex
  // above) so an in-progress, possibly-invalid edit never fights the table's own live view of
  // wizard state; "Apply" parses + validates (the same shape command_map.py enforces) before
  // dispatching SET_COMMAND_MAP, and any failure names the offending key instead of failing
  // silently.
  _renderCommandMapJsonEditor(state) {
    const draft = this._commandMapJsonDraft ?? JSON.stringify(state.learn.commandMap, null, 2);
    const field = textareaField({
      label: 'Command map JSON',
      id: this._commandMapJsonId,
      value: draft,
      mono: true,
      rows: 12,
      onInput: (v) => { this._commandMapJsonDraft = v; },
    });
    const fields = [
      field.element,
      h('div', { class: 'bs-btn-row' }, [
        h('button', { type: 'button', class: 'bs-btn bs-btn--primary', onClick: () => this._applyCommandMapJson() }, 'Apply JSON'),
        h(
          'button',
          {
            type: 'button',
            class: 'bs-btn bs-btn--text',
            onClick: () => { this._commandMapJsonDraft = null; this._commandMapJsonError = null; this._render(); },
          },
          'Reset to current',
        ),
      ]),
    ];
    if (this._commandMapJsonError) fields.push(h('p', { class: 'bs-field__error' }, this._commandMapJsonError));
    return h('div', { class: 'bs-callout' }, fields);
  }

  _applyCommandMapJson() {
    let parsed;
    try {
      parsed = JSON.parse(this._commandMapJsonDraft ?? '{}');
    } catch (err) {
      this._commandMapJsonError = `Invalid JSON: ${err.message}`;
      this._render();
      return;
    }
    try {
      validateCommandMapJson(parsed);
    } catch (err) {
      this._commandMapJsonError = err.message;
      this._render();
      return;
    }
    this._commandMapJsonError = null;
    this._commandMapJsonDraft = null;
    this._wizard.dispatch({ type: 'SET_COMMAND_MAP', commandMap: parsed });
  }

  // --- Top level ---------------------------------------------------------------------

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
    if (!state.identify.selectedCharacteristic || !state.identify.codecId) {
      this._contentRoot.append(h('p', { class: 'bs-empty' }, 'Finish Identify first: pick a write channel and connect.'));
      return;
    }

    const busy = describeBusy(state);
    const sections = [];
    if (busy) sections.push(h('div', { class: 'bs-banner bs-banner--busy' }, `Busy: ${busy}`));

    sections.push(
      this._renderCodecSection(state),
      this._renderTrySection(state),
      this._renderBuilderPanel(state),
      this._renderSweepSection(state),
      this._renderListenSection(state),
      this._renderCommandMapSection(state),
      h('div', { class: 'bs-btn-row' }, [
        h('button', { type: 'button', class: 'bs-btn bs-btn--primary', onClick: () => this._wizard.dispatch({ type: 'GO_TO_STEP', step: 'finish' }) }, 'Continue to Finish'),
      ]),
    );

    const card = document.createElement('bs-card');
    card.heading = 'Learn';
    card.subheading = 'Probe the device and build its command map.';
    card.status = state.learn.commandOrder.length ? 'complete' : 'active';
    card.append(h('div', {}, sections));
    this._contentRoot.append(card);
  }
}

if (!customElements.get('bs-step-learn')) customElements.define('bs-step-learn', BsStepLearn);
