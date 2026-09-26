import test from 'node:test';
import assert from 'node:assert/strict';
import {
  initialState,
  reduce,
  reachableSteps,
  canLeaveFind,
  canLeaveIdentify,
  canCreateEntry,
  nextStep,
  previousStep,
  describeBusy,
  buildButtonEntry,
  buildNumberEntry,
  buildSwitchEntry,
  buildCommandMapEntry,
  uniqueCommandKey,
  starterCommandMapForCodec,
  safetyForCodec,
  blockedSweepOpcodes,
  validateCommandMapJson,
  createWizard,
  restoreState,
  restoreAdvanced,
  persistAdvanced,
  STEP_ORDER,
  SCHEMA_VERSION,
  ADVANCED_STORAGE_KEY,
} from '../../custom_components/blueshark/panel/wizard.js';

function memoryStorage() {
  const store = new Map();
  return {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    raw: store,
  };
}

// --- state machine: steps, guards, reachability ---------------------------------

test('a fresh wizard starts on Find with only Find reachable', () => {
  const state = initialState();
  assert.equal(state.step, STEP_ORDER[0]);
  assert.deepEqual(reachableSteps(state), ['find']);
});

test('selecting a device advances to Identify and unlocks it in the stepper', () => {
  const s0 = initialState();
  const s1 = reduce(s0, { type: 'SELECT_DEVICE', address: 'AA:BB:CC:DD:EE:FF' });
  assert.equal(s1.step, 'identify');
  assert.ok(canLeaveFind(s1));
  assert.deepEqual(reachableSteps(s1), ['find', 'identify']);
});

test('Learn is only reachable once a write channel and codec are both chosen', () => {
  let s = reduce(initialState(), { type: 'SELECT_DEVICE', address: 'AA' });
  assert.equal(canLeaveIdentify(s), false);
  s = reduce(s, { type: 'ENUMERATE_SUCCESS', services: [{ uuid: 'svc', characteristics: [] }], suggested: null });
  assert.equal(canLeaveIdentify(s), false, 'services present but no characteristic chosen yet');
  s = reduce(s, { type: 'SELECT_CHANNEL', service: 'svc', characteristic: 'char1' });
  assert.equal(canLeaveIdentify(s), true, 'enumerate already auto-defaulted the codec to raw, so a channel was all that was missing');
  s = reduce(s, { type: 'SELECT_CODEC', codecId: null });
  assert.equal(canLeaveIdentify(s), false, 'explicitly clearing the codec blocks Learn even with a channel already chosen');
  s = reduce(s, { type: 'SELECT_CODEC', codecId: 'raw' });
  assert.equal(canLeaveIdentify(s), true);
  assert.deepEqual(reachableSteps(s), ['find', 'identify', 'learn', 'finish'], 'Learn never blocks moving on, so Finish is reachable too');
});

test('GO_TO_STEP is a no-op past an unmet guard, but works once the guard is satisfied', () => {
  const s0 = reduce(initialState(), { type: 'SELECT_DEVICE', address: 'AA' });
  const blocked = reduce(s0, { type: 'GO_TO_STEP', step: 'learn' });
  assert.equal(blocked, s0, 'unreachable target must return the identical state object (no-op)');
  const ready = reduce(s0, {
    type: 'ENUMERATE_SUCCESS',
    services: [{ uuid: 's', characteristics: [] }],
    suggested: { service: 's', characteristic: 'c', codec_id: 'raw' },
  });
  const moved = reduce(ready, { type: 'GO_TO_STEP', step: 'learn' });
  assert.equal(moved.step, 'learn');
});

test('nextStep/previousStep respect ordering and reachability', () => {
  const s0 = initialState();
  assert.equal(nextStep(s0), null, 'find alone cannot advance without a selected device');
  assert.equal(previousStep(s0), null);
  const s1 = reduce(s0, { type: 'SELECT_DEVICE', address: 'AA' });
  assert.equal(nextStep(s1), null, 'identify is active but its own guard (channel+codec) is unmet');
  assert.equal(previousStep(s1), 'find');
});

test('picking a different device resets everything downstream, picking the same address is a no-op', () => {
  let s = reduce(initialState(), { type: 'SELECT_DEVICE', address: 'AA' });
  s = reduce(s, {
    type: 'ENUMERATE_SUCCESS',
    services: [{ uuid: 's', characteristics: [] }],
    suggested: { service: 's', characteristic: 'c', codec_id: 'raw' },
  });
  s = reduce(s, { type: 'ADD_COMMAND', key: 'foo', entry: { name: 'Foo', kind: 'button', opcode: 1, argument_hex: '' } });
  const sameAgain = reduce(s, { type: 'SELECT_DEVICE', address: 'AA' });
  assert.equal(sameAgain, s, 'reselecting the same address must not reset anything');
  const different = reduce(s, { type: 'SELECT_DEVICE', address: 'BB' });
  assert.equal(different.identify.selectedCharacteristic, null);
  assert.deepEqual(different.learn.commandMap, {}, 'command map built for the old device must not leak to the new one');
  assert.equal(different.step, 'identify');
});

test('enumerate defaults the write and notify channel to the suggested pair without clobbering a manual override', () => {
  let s = reduce(initialState(), { type: 'SELECT_DEVICE', address: 'AA' });
  s = reduce(s, { type: 'SELECT_CHANNEL', service: 'manual-svc', characteristic: 'manual-char' });
  s = reduce(s, {
    type: 'ENUMERATE_SUCCESS',
    services: [],
    suggested: { service: 'auto-svc', characteristic: 'auto-char', codec_id: null },
  });
  assert.equal(s.identify.selectedCharacteristic, 'manual-char', 'a prior manual choice must survive enumerate');
});

test('enumerate falls back through suggested codec_id, then the top family match, then the raw default', () => {
  const base = reduce(initialState(), { type: 'SELECT_DEVICE', address: 'AA' });
  const withMatch = reduce(base, { type: 'IDENTIFY_SUCCESS', matches: [{ id: 'coolled', codec_id: 'coolled' }], decoded: null });
  const s1 = reduce(withMatch, { type: 'ENUMERATE_SUCCESS', services: [], suggested: { service: 's', characteristic: 'c', codec_id: null } });
  assert.equal(s1.identify.codecId, 'coolled', 'suggested.codec_id is always null per the engine, so it falls back to the top match');
  const s2 = reduce(base, { type: 'ENUMERATE_SUCCESS', services: [], suggested: null });
  assert.equal(s2.identify.codecId, 'raw', 'no suggestion and no match falls back to the raw default');
});

test('canCreateEntry needs a name for a new device but not for one commands/get already found', () => {
  let s = reduce(initialState(), { type: 'SELECT_DEVICE', address: 'AA' });
  s = reduce(s, { type: 'ENUMERATE_SUCCESS', services: [{ uuid: 's', characteristics: [] }], suggested: { service: 's', characteristic: 'c', codec_id: 'raw' } });
  assert.equal(canCreateEntry(s), false, 'no name yet');
  const named = reduce(s, { type: 'SET_DEVICE_NAME', name: '  ' });
  assert.equal(canCreateEntry(named), false, 'whitespace-only name does not count');
  const withName = reduce(s, { type: 'SET_DEVICE_NAME', name: 'Bedroom Clock' });
  assert.equal(canCreateEntry(withName), true);
  const existing = reduce(s, { type: 'EXISTING_ENTRY_FOUND', commandMap: { a: { name: 'A', kind: 'button', opcode: 1, argument_hex: '' } } });
  assert.equal(canCreateEntry(existing), true, 'an already-configured device needs no name');
  assert.deepEqual(existing.learn.commandOrder, ['a'], 'its existing command map is seeded into learn');
});

test('describeBusy names whichever single operation currently holds the link', () => {
  const idle = initialState();
  assert.equal(describeBusy(idle), null);
  const sweeping = { ...idle, learn: { ...idle.learn, sweep: { ...idle.learn.sweep, running: true } } };
  assert.match(describeBusy(sweeping), /Sweep/);
  const listening = { ...idle, learn: { ...idle.learn, listen: { ...idle.learn.listen, active: true } } };
  assert.match(describeBusy(listening), /Listening/);
});

// --- devices merge (best-source RSSI tracking) ------------------------------------

test('UPSERT_DEVICE keeps the strongest RSSI and its source across repeated events for one address', () => {
  let s = reduce(initialState(), { type: 'UPSERT_DEVICE', device: { address: 'AA', name: 'Clock', rssi: -70, source: 'proxy1' } });
  s = reduce(s, { type: 'UPSERT_DEVICE', device: { address: 'AA', name: 'Clock', rssi: -85, source: 'proxy2' } });
  assert.equal(s.devices.AA.rssi, -70, 'a weaker second reading must not override the stronger first one');
  assert.equal(s.devices.AA.source, 'proxy1');
  const stronger = reduce(s, { type: 'UPSERT_DEVICE', device: { address: 'AA', name: 'Clock', rssi: -40, source: 'proxy3' } });
  assert.equal(stronger.devices.AA.rssi, -40);
  assert.equal(stronger.devices.AA.source, 'proxy3');
});

// --- command-map building ---------------------------------------------------------

test('buildButtonEntry matches command_map.py exactly: opcode + argument_hex, name, optional note/characteristic', () => {
  const entry = buildButtonEntry({ name: ' Power On ', note: 'flips relay', characteristic: 'char-uuid', opcode: 8, argumentHex: 'FF' });
  assert.deepEqual(entry, { name: 'Power On', note: 'flips relay', characteristic: 'char-uuid', kind: 'button', opcode: 8, argument_hex: 'ff' });
});

test('buildButtonEntry accepts a raw payload_hex form and omits optional fields when absent', () => {
  const entry = buildButtonEntry({ name: 'Raw', payloadHex: '0102' });
  assert.deepEqual(entry, { name: 'Raw', kind: 'button', payload_hex: '0102' });
  assert.ok(!('note' in entry) && !('characteristic' in entry));
});

test('buildButtonEntry rejects a blank name and an out-of-range opcode', () => {
  assert.throws(() => buildButtonEntry({ name: '  ', opcode: 1, argumentHex: '' }), /name/i);
  assert.throws(() => buildButtonEntry({ name: 'X', opcode: 300, argumentHex: '' }), /0 to 255/);
});

test('buildNumberEntry carries opcode + min/max only (no argument_hex) and enforces min < max', () => {
  const entry = buildNumberEntry({ name: 'Brightness', opcode: 3, min: 0, max: 100 });
  assert.deepEqual(entry, { name: 'Brightness', kind: 'number', opcode: 3, min: 0, max: 100 });
  assert.throws(() => buildNumberEntry({ name: 'Bad', opcode: 3, min: 10, max: 10 }), /less than/);
  assert.throws(() => buildNumberEntry({ name: 'Bad', opcode: 3, min: 50, max: 10 }), /less than/);
});

test('buildSwitchEntry nests on/off payload objects (not flat on_payload_hex/off_payload_hex)', () => {
  const entry = buildSwitchEntry({
    name: 'Relay',
    on: { opcode: 8, argumentHex: '01' },
    off: { opcode: 9, argumentHex: '00' },
  });
  assert.deepEqual(entry, {
    name: 'Relay',
    kind: 'switch',
    on: { opcode: 8, argument_hex: '01' },
    off: { opcode: 9, argument_hex: '00' },
  });
});

test('buildSwitchEntry accepts a payload_hex form per state and requires both states', () => {
  const entry = buildSwitchEntry({ name: 'Relay', on: { payloadHex: '01' }, off: { payloadHex: '00' } });
  assert.deepEqual(entry.on, { payload_hex: '01' });
  assert.throws(() => buildSwitchEntry({ name: 'Relay', on: { payloadHex: '01' } }), /on payload and an off payload/);
});

test('buildCommandMapEntry dispatches to the right builder and rejects an unknown kind', () => {
  assert.equal(buildCommandMapEntry('button', { name: 'A', opcode: 1, argumentHex: '' }).kind, 'button');
  assert.equal(buildCommandMapEntry('number', { name: 'A', opcode: 1, min: 0, max: 9 }).kind, 'number');
  assert.throws(() => buildCommandMapEntry('bogus', { name: 'A' }), /Unknown control kind/);
});

test('uniqueCommandKey slugifies the name and disambiguates against existing keys with -2, -3, ...', () => {
  assert.equal(uniqueCommandKey('Power On', []), 'power_on');
  assert.equal(uniqueCommandKey('Power On', ['power_on']), 'power_on-2');
  assert.equal(uniqueCommandKey('Power On', ['power_on', 'power_on-2']), 'power_on-3');
});

test('ADD_COMMAND stores an entry under its key exactly once and REMOVE_COMMAND deletes it cleanly', () => {
  const entry = buildButtonEntry({ name: 'Power On', opcode: 8, argumentHex: '' });
  let s = reduce(initialState(), { type: 'ADD_COMMAND', key: 'power_on', entry });
  s = reduce(s, { type: 'ADD_COMMAND', key: 'power_on', entry: { ...entry, note: 'updated' } });
  assert.deepEqual(s.learn.commandOrder, ['power_on'], 're-adding the same key must not duplicate the order list');
  assert.equal(s.learn.commandMap.power_on.note, 'updated');
  const removed = reduce(s, { type: 'REMOVE_COMMAND', key: 'power_on' });
  assert.deepEqual(removed.learn.commandMap, {});
  assert.deepEqual(removed.learn.commandOrder, []);
});

// --- beginner path: starter command pre-fill, destructive-opcode safety -----------

test('starterCommandMapForCodec returns the map for the matching codec, null otherwise or when empty', () => {
  const matches = [
    { id: 'iledclock', codec_id: 'iledclock', starter_command_map: { power: { name: 'Power', kind: 'button', opcode: 5, argument_hex: '01' } } },
    { id: 'coolled', codec_id: 'coolled', starter_command_map: {} },
  ];
  assert.deepEqual(starterCommandMapForCodec(matches, 'iledclock'), { power: { name: 'Power', kind: 'button', opcode: 5, argument_hex: '01' } });
  assert.equal(starterCommandMapForCodec(matches, 'coolled'), null, 'an empty starter map counts as none to offer');
  assert.equal(starterCommandMapForCodec(matches, 'raw'), null, 'no match uses this codec');
  assert.equal(starterCommandMapForCodec(null, 'iledclock'), null);
  assert.equal(starterCommandMapForCodec(matches, null), null);
});

test('safetyForCodec returns the safety list for the matching codec, [] otherwise', () => {
  const matches = [{ id: 'iledclock', codec_id: 'iledclock', safety: [{ opcode: 14, reason: 'Sets the device password' }] }];
  assert.deepEqual(safetyForCodec(matches, 'iledclock'), [{ opcode: 14, reason: 'Sets the device password' }]);
  assert.deepEqual(safetyForCodec(matches, 'raw'), []);
  assert.deepEqual(safetyForCodec(null, 'iledclock'), []);
});

test('blockedSweepOpcodes filters safety to the configured range and clears once destructive is included', () => {
  const safety = [{ opcode: 5, reason: 'a' }, { opcode: 14, reason: 'b' }, { opcode: 30, reason: 'c' }];
  assert.deepEqual(
    blockedSweepOpcodes(safety, { start: 0, end: 20, includeDestructive: false }),
    [{ opcode: 5, reason: 'a' }, { opcode: 14, reason: 'b' }],
    'only opcodes inside [start, end] are blocked',
  );
  assert.deepEqual(blockedSweepOpcodes(safety, { start: 0, end: 20, includeDestructive: true }), [], 'including destructive holds nothing back');
  assert.deepEqual(blockedSweepOpcodes([], { start: 0, end: 255, includeDestructive: false }), []);
});

test('SEED_STARTER_COMMANDS merges starter entries into the draft map exactly once, disambiguating key collisions', () => {
  let s = reduce(initialState(), { type: 'SELECT_DEVICE', address: 'AA' });
  s = reduce(s, { type: 'ADD_COMMAND', key: 'power', entry: buildButtonEntry({ name: 'My Power', opcode: 1, argumentHex: '' }) });
  const starter = {
    power: { name: 'Power', kind: 'button', opcode: 5, argument_hex: '01' },
    brightness: { name: 'Brightness', kind: 'number', opcode: 4, min: 0, max: 255 },
  };
  const seeded = reduce(s, { type: 'SEED_STARTER_COMMANDS', starterCommandMap: starter });
  assert.ok(seeded.finish.starterSeeded);
  assert.deepEqual(seeded.learn.commandOrder, ['power', 'power-2', 'brightness'], 'the colliding starter key is disambiguated, not dropped or overwritten');
  assert.equal(seeded.learn.commandMap.power.name, 'My Power', "the operator's own probed entry is never clobbered");
  assert.deepEqual(seeded.learn.commandMap['power-2'], starter.power);
  const seededAgain = reduce(seeded, { type: 'SEED_STARTER_COMMANDS', starterCommandMap: starter });
  assert.equal(seededAgain, seeded, 'already-seeded is a no-op, so removing a starter entry afterward cannot be undone by a re-render');
});

test('SEED_STARTER_COMMANDS with no starter map still marks starterSeeded so it never retries', () => {
  const s = reduce(initialState(), { type: 'SEED_STARTER_COMMANDS', starterCommandMap: null });
  assert.ok(s.finish.starterSeeded);
  assert.deepEqual(s.learn.commandMap, {});
});

test('RENAME_COMMAND updates only the name field of an existing entry, and no-ops on a blank name or missing key', () => {
  const entry = buildButtonEntry({ name: 'Power On', note: 'x', opcode: 8, argumentHex: '' });
  let s = reduce(initialState(), { type: 'ADD_COMMAND', key: 'power_on', entry });
  s = reduce(s, { type: 'RENAME_COMMAND', key: 'power_on', name: 'Main Power' });
  assert.equal(s.learn.commandMap.power_on.name, 'Main Power');
  assert.equal(s.learn.commandMap.power_on.note, 'x', 'renaming must not disturb other fields');
  const blank = reduce(s, { type: 'RENAME_COMMAND', key: 'power_on', name: '   ' });
  assert.equal(blank, s, 'a blank name is a no-op, not an empty name');
  const missing = reduce(s, { type: 'RENAME_COMMAND', key: 'nope', name: 'X' });
  assert.equal(missing, s, 'renaming a key that does not exist is a no-op');
});

test('SET_COMMAND_MAP replaces the whole draft map and rebuilds commandOrder from its keys', () => {
  let s = reduce(initialState(), { type: 'ADD_COMMAND', key: 'old', entry: buildButtonEntry({ name: 'Old', opcode: 1, argumentHex: '' }) });
  const next = { a: { name: 'A', kind: 'button', opcode: 1, argument_hex: '' }, b: { name: 'B', kind: 'button', opcode: 2, argument_hex: '' } };
  s = reduce(s, { type: 'SET_COMMAND_MAP', commandMap: next });
  assert.deepEqual(s.learn.commandMap, next);
  assert.deepEqual(s.learn.commandOrder, ['a', 'b']);
});

test('validateCommandMapJson accepts the same shapes buildCommandMapEntry produces', () => {
  const map = {
    power: buildButtonEntry({ name: 'Power', opcode: 5, argumentHex: '01' }),
    brightness: buildNumberEntry({ name: 'Brightness', opcode: 4, min: 0, max: 100 }),
    relay: buildSwitchEntry({ name: 'Relay', on: { opcode: 8, argumentHex: '01' }, off: { opcode: 8, argumentHex: '00' } }),
  };
  assert.equal(validateCommandMapJson(map), map);
});

test('validateCommandMapJson rejects a bad command id, an unknown kind, and an inverted number range', () => {
  assert.throws(() => validateCommandMapJson('not an object'), /must be a JSON object/);
  assert.throws(
    () => validateCommandMapJson({ 'bad id!': { name: 'X', kind: 'button', opcode: 1, argument_hex: '' } }),
    /"bad id!" must be 1-64 characters/,
  );
  assert.throws(() => validateCommandMapJson({ x: { name: 'X', kind: 'bogus' } }), /kind must be "button", "number" or "switch"/);
  assert.throws(() => validateCommandMapJson({ x: { name: 'X', kind: 'number', opcode: 1, min: 10, max: 5 } }), /min must be less than max/);
});

// --- persistence (localStorage restore) -------------------------------------------

test('createWizard restores step, selected device and command map from injected storage', () => {
  const storage = memoryStorage();
  const wizard1 = createWizard({ storage });
  wizard1.dispatch({ type: 'SELECT_DEVICE', address: 'AA:BB' });
  wizard1.dispatch({
    type: 'ENUMERATE_SUCCESS',
    services: [{ uuid: 's', characteristics: [] }],
    suggested: { service: 's', characteristic: 'c', codec_id: 'raw' },
  });
  wizard1.dispatch({ type: 'ADD_COMMAND', key: 'power_on', entry: buildButtonEntry({ name: 'Power On', opcode: 8, argumentHex: '' }) });

  const wizard2 = createWizard({ storage });
  const restored = wizard2.getState();
  assert.equal(restored.step, 'identify');
  assert.equal(restored.find.selectedAddress, 'AA:BB');
  assert.equal(restored.identify.selectedCharacteristic, 'c');
  assert.deepEqual(restored.learn.commandOrder, ['power_on']);
  assert.deepEqual(restored.devices, {}, 'the live device scan list is never persisted');
});

test('restoreState falls back to a fresh wizard on a schema-version mismatch or corrupt JSON', () => {
  const storage = memoryStorage();
  storage.setItem('blueshark-wizard-state-v1', JSON.stringify({ version: SCHEMA_VERSION + 1, step: 'finish' }));
  assert.equal(restoreState(storage).step, 'find');
  storage.setItem('blueshark-wizard-state-v1', '{not json');
  assert.equal(restoreState(storage).step, 'find');
});

test('restoreState never resurrects a live sweep/listen session across a reload', () => {
  const storage = memoryStorage();
  const wizard1 = createWizard({ storage });
  wizard1.dispatch({ type: 'SELECT_DEVICE', address: 'AA' });
  wizard1.dispatch({ type: 'ENUMERATE_SUCCESS', services: [{ uuid: 's', characteristics: [] }], suggested: { service: 's', characteristic: 'c', codec_id: 'raw' } });
  wizard1.dispatch({ type: 'GO_TO_STEP', step: 'learn' });
  wizard1.dispatch({ type: 'SWEEP_START', runId: 'run_1' });
  wizard1.dispatch({ type: 'SWEEP_PROGRESS', row: { index: 1, opcode: 1, verdict: 'accepted' } });

  const restored = restoreState(storage);
  assert.equal(restored.learn.sweep.running, false);
  assert.deepEqual(restored.learn.sweep.rows, []);
  assert.equal(restored.learn.sweep.runId, null);
});

test('dispatch only notifies subscribers and persists when state actually changes', () => {
  const storage = memoryStorage();
  const wizard = createWizard({ storage });
  let calls = 0;
  wizard.subscribe(() => { calls += 1; });
  wizard.dispatch({ type: 'SET_QUERY', query: 'clock' });
  assert.equal(calls, 1);
  const before = storage.raw.get('blueshark-wizard-state-v1');
  wizard.dispatch({ type: 'UNKNOWN_ACTION_TYPE' });
  assert.equal(calls, 1, 'an unhandled action type is a no-op and must not notify subscribers');
  assert.equal(storage.raw.get('blueshark-wizard-state-v1'), before);
});

test('RESTART clears persisted state and returns a brand-new wizard', () => {
  const storage = memoryStorage();
  const wizard = createWizard({ storage });
  wizard.dispatch({ type: 'SELECT_DEVICE', address: 'AA' });
  assert.ok(storage.raw.has('blueshark-wizard-state-v1'));
  wizard.dispatch({ type: 'RESTART' });
  assert.equal(wizard.getState().step, 'find');
  assert.equal(wizard.getState().find.selectedAddress, null);
  assert.equal(storage.raw.has('blueshark-wizard-state-v1'), false);
});

// --- advanced flag persistence (its own storage key, independent of wizard progress) ----------

test('SET_ADVANCED toggles the flag and is a no-op when set to the same value', () => {
  const s0 = initialState();
  assert.equal(s0.advanced, false);
  const on = reduce(s0, { type: 'SET_ADVANCED', advanced: true });
  assert.equal(on.advanced, true);
  const same = reduce(on, { type: 'SET_ADVANCED', advanced: true });
  assert.equal(same, on, 'setting the same value is a no-op');
});

test('RESTART preserves the advanced flag even though it clears everything else', () => {
  let s = reduce(initialState(), { type: 'SET_ADVANCED', advanced: true });
  s = reduce(s, { type: 'SELECT_DEVICE', address: 'AA' });
  const restarted = reduce(s, { type: 'RESTART' });
  assert.equal(restarted.advanced, true);
  assert.equal(restarted.step, 'find');
  assert.equal(restarted.find.selectedAddress, null);
});

test('restoreAdvanced/persistAdvanced round-trip through injected storage and default to false', () => {
  const storage = memoryStorage();
  assert.equal(restoreAdvanced(storage), false, 'nothing stored yet defaults to off');
  persistAdvanced(storage, true);
  assert.equal(restoreAdvanced(storage), true);
  assert.ok(storage.raw.has(ADVANCED_STORAGE_KEY));
});

test('createWizard persists SET_ADVANCED under its own key, independent of and surviving RESTART on the wizard-progress key', () => {
  const storage = memoryStorage();
  const wizard1 = createWizard({ storage });
  wizard1.dispatch({ type: 'SELECT_DEVICE', address: 'AA' });
  wizard1.dispatch({ type: 'SET_ADVANCED', advanced: true });
  assert.ok(storage.raw.has(ADVANCED_STORAGE_KEY));

  wizard1.dispatch({ type: 'RESTART' });
  assert.equal(storage.raw.has('blueshark-wizard-state-v1'), false, 'wizard progress is cleared as usual');
  assert.ok(storage.raw.has(ADVANCED_STORAGE_KEY), 'the advanced preference survives RESTART');
  assert.equal(wizard1.getState().advanced, true);

  const wizard2 = createWizard({ storage });
  assert.equal(wizard2.getState().advanced, true, 'a fresh wizard instance still picks up the persisted preference');
});

// --- panel chrome: the sidebar toggle is the only way back on a narrow screen -----------------
import { test as chromeTest } from 'node:test';
import chromeAssert from 'node:assert/strict';

chromeTest('the panel source wires the menu button to HA\'s own sidebar event', async () => {
  const { readFile } = await import('node:fs/promises');
  const src = await readFile(
    new URL('../../custom_components/blueshark/panel/blueshark-panel.js', import.meta.url),
    'utf8',
  );
  // HA listens for `hass-toggle-menu` on the window; it must escape the shadow root to get there.
  chromeAssert.match(src, /hass-toggle-menu/);
  chromeAssert.match(src, /composed:\s*true/);
  // A decorative icon would be worse than none: the button only exists while HA hides its sidebar.
  chromeAssert.match(src, /_menuButton\.hidden = !this\._narrow/);
});
