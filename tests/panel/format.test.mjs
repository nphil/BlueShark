import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseHex,
  displayHex,
  parseOpcode,
  buildPayloadFromOpcode,
  formatVerdict,
  formatElapsed,
  formatElapsedSince,
  formatConfidenceLabel,
  formatFamilyBadge,
  formatStarterCommandMessage,
  describeDedicatedIntegration,
  formatSafetyEntry,
  formatSafetyList,
  formatDecodedFacts,
  pickBestSource,
  slugify,
  formatByte,
  hasWriteNotifyPair,
  isWritable,
  isNotifiable,
  formatApiErrorMessage,
  sortByRssiDesc,
} from '../../custom_components/blueshark/panel/format.js';

// --- hex parsing/formatting ---------------------------------------------------

test('parseHex normalizes spaced, colon/dash separated and 0x-prefixed input to lowercase unspaced hex', () => {
  assert.equal(parseHex('01 0A FF').hex, '010aff');
  assert.equal(parseHex('01:0A-FF').hex, '010aff');
  assert.equal(parseHex('0x010AFF').hex, '010aff');
  assert.deepEqual([...parseHex('00FF').bytes], [0, 255]);
});

test('parseHex rejects incomplete bytes, non-hex characters and empty input', () => {
  assert.throws(() => parseHex('0'), /complete bytes/);
  assert.throws(() => parseHex('zz'), /0-9 and letters A-F/);
  assert.throws(() => parseHex(''), /Enter a hex payload/);
  assert.equal(parseHex('', { allowEmpty: true }).hex, '');
});

test('parseHex enforces the byte-length limit', () => {
  assert.throws(() => parseHex('00'.repeat(5), { maxBytes: 4 }), /4-byte limit/);
  assert.doesNotThrow(() => parseHex('00'.repeat(4), { maxBytes: 4 }));
});

test('displayHex renders spaced uppercase groups and never throws on garbage', () => {
  assert.equal(displayHex('010aff'), '01 0A FF');
  assert.equal(displayHex(null), '\u2014');
  assert.equal(displayHex('not hex'), '\u2014');
  assert.equal(displayHex(''), '\u2014');
});

test('parseOpcode accepts only whole numbers 0-255', () => {
  assert.equal(parseOpcode(8), 8);
  assert.equal(parseOpcode('8'), 8);
  assert.throws(() => parseOpcode(256), /0 to 255/);
  assert.throws(() => parseOpcode(-1), /0 to 255/);
  assert.throws(() => parseOpcode(1.5), /0 to 255/);
  assert.throws(() => parseOpcode(NaN), /0 to 255/);
});

test('buildPayloadFromOpcode matches the hardware-verified CoolLED encode input (opcode 0x08, arg 0xff)', () => {
  // codecs/coolled.py's CoolLedCodec.encode(payload) wraps whatever this produces; this function
  // only builds the bare payload (opcode + argument), which is bytes([0x08, 0xff]) here.
  assert.equal(buildPayloadFromOpcode(8, 'ff'), '08ff');
  assert.equal(buildPayloadFromOpcode(0x08, ''), '08');
});

test('formatByte renders a byte as hex-and-decimal, or an em dash out of range', () => {
  assert.equal(formatByte(33), '0x21 (33)');
  assert.equal(formatByte(256), '\u2014');
  assert.equal(formatByte(-1), '\u2014');
});

// --- verdict formatting --------------------------------------------------------

test('formatVerdict maps every real sweep.py verdict string to a distinct tone and readable label', () => {
  assert.deepEqual(formatVerdict('accepted'), { tone: 'success', label: 'Accepted', raw: 'accepted', inferred: false });
  assert.equal(formatVerdict('rejected_unknown_id').tone, 'error');
  assert.equal(formatVerdict('rejected_other').tone, 'error');
  assert.equal(formatVerdict('undecodable').tone, 'warning');
  assert.equal(formatVerdict('no_response').tone, 'neutral');
});

test('formatVerdict covers the client-side pseudo-verdicts (denied/pending/error) distinctly from device verdicts', () => {
  assert.equal(formatVerdict('denied').tone, 'error');
  assert.equal(formatVerdict('pending').tone, 'neutral');
  assert.equal(formatVerdict('error').tone, 'error');
  assert.equal(formatVerdict(undefined).label, 'Pending');
});

test('formatVerdict appends "(inferred)" only when asked, and always carries text not just a tone', () => {
  const plain = formatVerdict('accepted');
  const inferred = formatVerdict('accepted', { inferred: true });
  assert.equal(plain.label, 'Accepted');
  assert.equal(inferred.label, 'Accepted (inferred)');
  assert.equal(inferred.tone, plain.tone);
});

test('formatVerdict humanizes an unrecognized verdict instead of dropping it', () => {
  const v = formatVerdict('some_future_verdict');
  assert.equal(v.tone, 'neutral');
  assert.equal(v.label, 'Some Future Verdict');
});

test('formatElapsed switches from ms to seconds at the 1000ms boundary', () => {
  assert.equal(formatElapsed(420), '420 ms');
  assert.equal(formatElapsed(999), '999 ms');
  assert.equal(formatElapsed(1000), '1.0 s');
  assert.equal(formatElapsed(1300), '1.3 s');
  assert.equal(formatElapsed(-5), '\u2014');
});

test('formatElapsedSince reports zero for the first frame and elapsed time thereafter', () => {
  assert.equal(formatElapsedSince(1000, null), '+0 ms');
  assert.equal(formatElapsedSince(1500, 1000), '+500 ms');
  assert.equal(formatElapsedSince(900, 1000), '+0 ms');
});

// --- evidence / confidence / decoded facts -------------------------------------

test('formatConfidenceLabel prefers the categorical grade over a fabricated percentage', () => {
  assert.equal(formatConfidenceLabel('likely'), 'Likely');
  assert.equal(formatConfidenceLabel('CERTAIN'), 'Certain');
  assert.equal(formatConfidenceLabel(null), null);
});

test('formatFamilyBadge uses confidence_label for display text and keeps numeric confidence separately', () => {
  const badge = formatFamilyBadge({ name: 'CoolLED', confidence: 0.7, confidence_label: 'likely', evidence: ['a', ' ', 'b'] });
  assert.equal(badge.confidenceLabel, 'Likely');
  assert.equal(badge.confidence, 0.7);
  assert.deepEqual(badge.evidence, ['a', 'b']);
});

test('formatFamilyBadge falls back to a numeric percentage only when confidence_label is missing', () => {
  const badge = formatFamilyBadge({ name: 'X', confidence: 0.4 });
  assert.equal(badge.confidenceLabel, '40%');
});

test('formatFamilyBadge returns null for no match, never a badge with an empty name', () => {
  assert.equal(formatFamilyBadge(null), null);
  assert.equal(formatFamilyBadge({ confidence: 1 }), null);
});

// --- beginner path: starter-command messaging, dedicated integration, safety list -------

test('formatStarterCommandMessage counts starter_command_map entries, singular/plural, null when absent or empty', () => {
  assert.equal(formatStarterCommandMessage({ starter_command_map: { a: {}, b: {} } }), 'BlueShark already knows 2 safe commands for this device.');
  assert.equal(formatStarterCommandMessage({ starter_command_map: { a: {} } }), 'BlueShark already knows 1 safe command for this device.');
  assert.equal(formatStarterCommandMessage({ starter_command_map: {} }), null);
  assert.equal(formatStarterCommandMessage({}), null);
  assert.equal(formatStarterCommandMessage(null), null);
});

test('describeDedicatedIntegration builds the "Installed - open it" CTA only when hass.config.components includes the domain', () => {
  const family = {
    dedicated_integration: {
      domain: 'iledclock',
      name: 'iLedClock',
      url: 'https://github.com/nphil/ha-iledclock',
      summary: 'Clock faces, pixel art and animations',
    },
  };
  const notInstalled = describeDedicatedIntegration(family, ['default_config']);
  assert.equal(notInstalled.installed, false);
  assert.equal(notInstalled.ctaLabel, 'View the integration');
  assert.equal(notInstalled.ctaHref, 'https://github.com/nphil/ha-iledclock');
  assert.match(notInstalled.headline, /^A full integration exists: iLedClock/);

  const installed = describeDedicatedIntegration(family, ['default_config', 'iledclock']);
  assert.equal(installed.installed, true);
  assert.equal(installed.ctaLabel, 'Installed \u2014 open it');
  assert.equal(installed.ctaHref, '/config/integrations/integration/iledclock');
});

test('describeDedicatedIntegration returns null when absent or missing a domain/name', () => {
  assert.equal(describeDedicatedIntegration({}, []), null);
  assert.equal(describeDedicatedIntegration({ dedicated_integration: { domain: 'x' } }, []), null, 'name is required');
  assert.equal(describeDedicatedIntegration(null, []), null);
});

test('formatSafetyEntry falls back to the documented default reason and drops an out-of-range opcode', () => {
  assert.deepEqual(formatSafetyEntry({ opcode: 14, reason: 'Sets the device password' }), {
    opcode: 14,
    opcodeLabel: '0x0E (14)',
    reason: 'Sets the device password',
  });
  assert.equal(formatSafetyEntry({ opcode: 10 }).reason, 'Unknown effect; blocked to be safe.');
  assert.equal(formatSafetyEntry({ opcode: 999, reason: 'x' }), null);
});

test('formatSafetyList drops invalid entries and sorts the rest by opcode', () => {
  const rows = formatSafetyList([{ opcode: 20, reason: 'b' }, { opcode: 5, reason: 'a' }, { opcode: -1, reason: 'bad' }]);
  assert.deepEqual(rows.map((r) => r.opcode), [5, 20]);
  assert.deepEqual(formatSafetyList(null), []);
});

test('formatDecodedFacts flattens the real nested per-family decoded shape and hex-formats firmware', () => {
  const rows = formatDecodedFacts({ coolled_panel: { id_hex: 'bcdc07', width: 32, height: 16, colour: 4, firmware: 33 } });
  assert.deepEqual(rows, [
    { label: 'Id Hex', value: 'bcdc07' },
    { label: 'Width', value: '32' },
    { label: 'Height', value: '16' },
    { label: 'Colour', value: '4' },
    { label: 'Firmware', value: '0x21 (33)' },
  ]);
});

test('formatDecodedFacts handles a second family shape and returns nothing for an empty/absent decode', () => {
  const rows = formatDecodedFacts({ mibeacon: { frame_control: 1, encrypted: false } });
  assert.deepEqual(rows, [
    { label: 'Frame Control', value: '1' },
    { label: 'Encrypted', value: 'false' },
  ]);
  assert.deepEqual(formatDecodedFacts({}), []);
  assert.deepEqual(formatDecodedFacts(null), []);
});

// --- devices / RSSI / sources ---------------------------------------------------

test('pickBestSource returns the strongest (least negative) RSSI reading, or null when empty', () => {
  assert.deepEqual(pickBestSource({ proxy_a: -70, proxy_b: -50, proxy_c: -90 }), { source: 'proxy_b', rssi: -50 });
  assert.equal(pickBestSource({}), null);
  assert.equal(pickBestSource(null), null);
});

test('sortByRssiDesc orders strongest signal first and sends missing RSSI to the end', () => {
  const sorted = sortByRssiDesc([{ id: 1, rssi: -80 }, { id: 2, rssi: -40 }, { id: 3 }]);
  assert.deepEqual(sorted.map((d) => d.id), [2, 1, 3]);
});

// --- GATT property helpers -------------------------------------------------------

test('hasWriteNotifyPair requires both a write flavor and a notify flavor on the same characteristic', () => {
  assert.equal(hasWriteNotifyPair(['write', 'notify']), true);
  assert.equal(hasWriteNotifyPair(['write-without-response', 'indicate']), true);
  assert.equal(hasWriteNotifyPair(['write']), false);
  assert.equal(hasWriteNotifyPair(['read']), false);
});

test('isWritable and isNotifiable check a single flavor independently of the other', () => {
  assert.equal(isWritable(['write']), true);
  assert.equal(isWritable(['notify']), false);
  assert.equal(isNotifiable(['indicate']), true);
  assert.equal(isNotifiable(['write']), false);
});

// --- misc -------------------------------------------------------------------------

test('slugify produces a non-empty lowercase underscore key even from odd input', () => {
  assert.equal(slugify('Mode Toggle!'), 'mode_toggle');
  assert.equal(slugify('  '), 'command');
  assert.equal(slugify(''), 'command');
});

test('formatApiErrorMessage appends the known hint for a documented WS_ERROR_* code', () => {
  const msg = formatApiErrorMessage({ code: 'busy', message: 'device locked' });
  assert.match(msg, /^device locked \(/);
  assert.match(msg, /Another operation is using the connection/);
});

test('formatApiErrorMessage passes through an unknown code without a hint, and handles a missing message', () => {
  assert.equal(formatApiErrorMessage({ code: 'weird', message: 'oops' }), 'oops');
  assert.equal(formatApiErrorMessage({ code: 'busy' }), 'The engine reported an error without a message. (Another operation is using the connection to this device. Wait for it to finish, or stop it first.)');
  assert.equal(formatApiErrorMessage(null), 'Unknown error.');
});
