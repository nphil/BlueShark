// Pure formatting and validation helpers for the BlueShark onboarding wizard.
// No DOM access, no HA imports: safe to unit test under plain Node.

export const MAX_PAYLOAD_BYTES = 512;

/**
 * Parse operator-entered hex into canonical lowercase, unspaced wire form.
 * Accepts spaced ("01 0A FF"), colon/dash separated, or bare ("010AFF") input,
 * with an optional leading "0x". Throws a human-readable Error on anything
 * that is not complete hex bytes.
 */
export function parseHex(input, { allowEmpty = false, maxBytes = MAX_PAYLOAD_BYTES } = {}) {
  const cleaned = String(input ?? '')
    .trim()
    .replace(/0x/gi, '')
    .replace(/[\s:-]/g, '');
  if (!cleaned) {
    if (allowEmpty) return { hex: '', bytes: new Uint8Array(0), byteLength: 0 };
    throw new Error('Enter a hex payload, for example 01 0A FF.');
  }
  if (!/^[0-9a-f]+$/i.test(cleaned)) {
    throw new Error('Hex payload may only contain the digits 0-9 and letters A-F.');
  }
  if (cleaned.length % 2 !== 0) {
    throw new Error('Hex payload must contain complete bytes (an even number of hex digits).');
  }
  const byteLength = cleaned.length / 2;
  if (byteLength > maxBytes) {
    throw new Error(`Hex payload exceeds the ${maxBytes}-byte limit.`);
  }
  const hex = cleaned.toLowerCase();
  const bytes = new Uint8Array(byteLength);
  for (let i = 0; i < byteLength; i++) {
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return { hex, bytes, byteLength };
}

/** Render any wire-form or operator-entered hex as spaced, uppercase groups. Never throws. */
export function displayHex(value, { placeholder = '\u2014' } = {}) {
  if (value === null || value === undefined || value === '') return placeholder;
  let hex;
  try {
    ({ hex } = parseHex(value, { allowEmpty: true, maxBytes: Infinity }));
  } catch {
    return placeholder;
  }
  if (!hex) return placeholder;
  return hex.toUpperCase().match(/../g).join(' ');
}

/** RSSI (dBm, typically negative) -> a label, a 0-100 bar percentage, and a coarse tone. */
export function formatRssi(rssi) {
  const value = Number(rssi);
  if (!Number.isFinite(value)) {
    return { value: null, label: '\u2014', percent: 0, tone: 'unknown' };
  }
  const clamped = Math.min(-30, Math.max(-100, value));
  const percent = Math.round(((clamped + 100) / 70) * 100);
  const tone = value >= -60 ? 'strong' : value >= -80 ? 'ok' : 'weak';
  return { value, label: `${value} dBm`, percent, tone };
}

function humanizeToken(token) {
  return token
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ');
}

// Real vocabulary from sweep.py's verdict()/interpret_sweep(): accepted, no_response,
// undecodable, rejected_unknown_id, rejected_other. denied/pending/error are client-side
// pseudo-verdicts (destructive guard blocked it, row awaiting a reply, the API call itself
// failed) — they never come from the device but need the same chip treatment.
const VERDICT_TONES = {
  accepted: { tone: 'success', label: 'Accepted' },
  rejected_unknown_id: { tone: 'error', label: 'Rejected (unknown command)' },
  rejected_other: { tone: 'error', label: 'Rejected' },
  undecodable: { tone: 'warning', label: 'Undecodable reply' },
  no_response: { tone: 'neutral', label: 'No response' },
  denied: { tone: 'error', label: 'Denied' },
  pending: { tone: 'neutral', label: 'Pending' },
  error: { tone: 'error', label: 'Error' },
};

/** Verdict string -> chip {tone, label, raw, inferred}. Label always carries text, never color-only. */
export function formatVerdict(verdict, { inferred = false } = {}) {
  const key = String(verdict ?? 'pending').trim().toLowerCase().replace(/-/g, '_') || 'pending';
  const known = VERDICT_TONES[key];
  const base = known ?? { tone: 'neutral', label: humanizeToken(key) };
  const label = inferred ? `${base.label} (inferred)` : base.label;
  return { tone: base.tone, label, raw: verdict ?? null, inferred };
}

/** Passthrough for the low-level `status` field distinct from the human `verdict`. */
export function formatStatus(status) {
  if (status === null || status === undefined || status === '') return '\u2014';
  return String(status);
}

/** Milliseconds -> "420 ms" under a second, "1.3 s" at or beyond. */
export function formatElapsed(ms) {
  const value = Number(ms);
  if (!Number.isFinite(value) || value < 0) return '\u2014';
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

/** 0..1 confidence -> a rounded percentage string, or null when not a number. */
export function formatConfidence(confidence) {
  const value = Number(confidence);
  if (!Number.isFinite(value)) return null;
  const clamped = Math.min(1, Math.max(0, value));
  return `${Math.round(clamped * 100)}%`;
}

/** Normalize an evidence array to trimmed, non-empty strings. */
export function formatEvidence(evidence) {
  if (!Array.isArray(evidence)) return [];
  return evidence.map((item) => String(item ?? '').trim()).filter(Boolean);
}

const CONFIDENCE_LABELS = {
  certain: 'Certain',
  likely: 'Likely',
  possible: 'Possible',
};

/** Raw families.py confidence enum ("certain"|"likely"|"possible") -> a capitalized label,
 * or null when absent. Unknown strings still get a readable label instead of disappearing. */
export function formatConfidenceLabel(confidenceLabel) {
  const key = String(confidenceLabel ?? '').trim().toLowerCase();
  if (!key) return null;
  return CONFIDENCE_LABELS[key] ?? humanizeToken(key);
}

/** Family match object -> a display-ready badge, or null when there is no match.
 * Prefers the engine's categorical confidence_label for the visible text (the real evidence
 * grade); the numeric confidence (a fixed per-tier value, not a measured probability) is kept
 * alongside for sorting or a bar width, and only shown as a bare percentage when the categorical
 * label is missing. */
export function formatFamilyBadge(family) {
  if (!family || typeof family !== 'object') return null;
  const name = String(family.name ?? '').trim();
  if (!name) return null;
  const numericConfidence = Number(family.confidence);
  const confidence = Number.isFinite(numericConfidence) ? numericConfidence : null;
  return {
    id: family.id ?? null,
    name,
    confidence,
    confidenceLabel: formatConfidenceLabel(family.confidence_label) ?? formatConfidence(confidence),
    evidence: formatEvidence(family.evidence),
  };
}

/** "BlueShark already knows N safe commands for this device" when `starter_command_map` is a
 * non-empty object, else null. `starter_command_map` is an optional FamilyMatch field the engine
 * only sends once it has beginner-safe commands recorded for this family. */
export function formatStarterCommandMessage(family) {
  const starter = family?.starter_command_map;
  if (!starter || typeof starter !== 'object') return null;
  const count = Object.keys(starter).length;
  if (count === 0) return null;
  return `BlueShark already knows ${count} safe command${count === 1 ? '' : 's'} for this device.`;
}

/** One `{opcode, reason}` entry from a FamilyMatch's `safety` list -> a display-ready row.
 * Falls back to the contract's documented default when the engine sent no reason. */
export function formatSafetyEntry(entry) {
  const opcode = Number(entry?.opcode);
  if (!Number.isInteger(opcode) || opcode < 0 || opcode > 255) return null;
  const reason = String(entry?.reason ?? '').trim() || 'Unknown effect; blocked to be safe.';
  return { opcode, opcodeLabel: formatByte(opcode), reason };
}

/** A `safety` array -> display-ready rows: invalid entries dropped, sorted by opcode. */
export function formatSafetyList(safety) {
  if (!Array.isArray(safety)) return [];
  return safety.map(formatSafetyEntry).filter(Boolean).sort((a, b) => a.opcode - b.opcode);
}

/** manufacturer_data ({decimalId: lowercaseHex}) -> display rows. */
export function formatManufacturerData(manufacturerData) {
  if (!manufacturerData || typeof manufacturerData !== 'object') return [];
  return Object.entries(manufacturerData).map(([id, hexValue]) => {
    const numericId = Number(id);
    const hasNumericId = Number.isFinite(numericId);
    const idHex = hasNumericId ? `0x${Math.trunc(numericId).toString(16).toUpperCase().padStart(4, '0')}` : null;
    return {
      id,
      idLabel: hasNumericId ? `${Math.trunc(numericId)} (${idHex})` : String(id),
      hex: displayHex(hexValue),
    };
  });
}

const BASE_UUID_RE = /^([0-9a-f]{8})-0000-1000-8000-00805f9b34fb$/;

/** Full 128-bit UUID -> "0xFFF0" for SIG base UUIDs, else a truncated "aabbccdd\u2026eeff". */
export function shortUuid(uuid) {
  const value = String(uuid ?? '').trim().toLowerCase();
  if (!value) return '\u2014';
  const baseMatch = value.match(BASE_UUID_RE);
  if (baseMatch) {
    const head = baseMatch[1].replace(/^0000/, '') || baseMatch[1];
    return `0x${head.toUpperCase()}`;
  }
  if (value.length <= 8) return value.toUpperCase();
  return `${value.slice(0, 8)}\u2026${value.slice(-4)}`;
}

const PROPERTY_ORDER = [
  'read',
  'write',
  'write-without-response',
  'notify',
  'indicate',
  'broadcast',
  'authenticated-signed-writes',
  'extended-properties',
];

/** Sort a characteristic's raw properties into a stable, familiar order. */
export function formatProperties(properties) {
  if (!Array.isArray(properties)) return [];
  const set = new Set(properties.map((p) => String(p ?? '').trim().toLowerCase()).filter(Boolean));
  const ordered = PROPERTY_ORDER.filter((p) => set.has(p));
  const rest = [...set].filter((p) => !PROPERTY_ORDER.includes(p));
  return [...ordered, ...rest];
}

const WRITE_PROPERTIES = new Set(['write', 'write-without-response', 'authenticated-signed-writes']);
const NOTIFY_PROPERTIES = new Set(['notify', 'indicate']);

/** True when properties include any write flavor (write / write-without-response / signed-writes). */
export function isWritable(properties) {
  if (!Array.isArray(properties)) return false;
  return properties.some((raw) => WRITE_PROPERTIES.has(String(raw ?? '').trim().toLowerCase()));
}

/** True when properties include any notify flavor (notify / indicate). */
export function isNotifiable(properties) {
  if (!Array.isArray(properties)) return false;
  return properties.some((raw) => NOTIFY_PROPERTIES.has(String(raw ?? '').trim().toLowerCase()));
}

/** True when a single characteristic's properties combine a write flavor and a notify flavor. */
export function hasWriteNotifyPair(properties) {
  return isWritable(properties) && isNotifiable(properties);
}

/** Validate a byte 0-255 (a wire opcode). Throws the same friendly message as buildPayloadFromOpcode. */
export function parseOpcode(opcode) {
  const op = Number(opcode);
  if (!Number.isInteger(op) || op < 0 || op > 255) {
    throw new Error('Opcode must be a whole number from 0 to 255 (0x00-0xFF).');
  }
  return op;
}

/** Combine an opcode byte (0-255) with optional argument hex into one wire payload (lowercase, unspaced). */
export function buildPayloadFromOpcode(opcode, argumentHex = '') {
  const op = parseOpcode(opcode);
  const { hex } = parseHex(argumentHex, { allowEmpty: true, maxBytes: MAX_PAYLOAD_BYTES - 1 });
  return op.toString(16).padStart(2, '0') + hex;
}

/** A single byte -> "0x4A (74)", or an em dash when out of range. */
export function formatByte(value) {
  const num = Number(value);
  if (!Number.isInteger(num) || num < 0 || num > 255) return '\u2014';
  return `0x${num.toString(16).toUpperCase().padStart(2, '0')} (${num})`;
}

const ERROR_HINTS = {
  busy: 'Another operation is using the connection to this device. Wait for it to finish, or stop it first.',
  no_route: 'No proxy currently hears this device. It may be out of range, asleep, or held by another central such as a phone app.',
  not_found: 'Home Assistant does not know this device yet. Go back to Find and let it advertise again.',
  refused: 'The device rejected this request outright.',
  timeout: 'The device did not answer in time. It may be busy, out of range, or held by another central such as a phone app.',
  unsupported: 'The engine does not support this operation for this device or characteristic.',
};

/** Human hint for a known engine error code, or null when the code is not one of the six documented ones. */
export function apiErrorHint(code) {
  return ERROR_HINTS[String(code ?? '').trim().toLowerCase()] ?? null;
}

/** The engine's own message, verbatim, plus a parenthetical hint when the code is a known one. */
export function formatApiErrorMessage(error) {
  if (!error) return 'Unknown error.';
  const message =
    typeof error.message === 'string' && error.message.trim()
      ? error.message.trim()
      : 'The engine reported an error without a message.';
  const hint = apiErrorHint(error.code);
  return hint ? `${message} (${hint})` : message;
}

/** Lowercase, underscore-joined slug for command_map keys. Never empty. */
export function slugify(text) {
  const base = String(text ?? '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return base || 'command';
}

/** Best-effort link to the integration's config entry page (device-registry id is not returned by the API). */
export function entryDashboardUrl(domain = 'blueshark') {
  return `/config/integrations/integration/${domain}`;
}

/** `dedicated_integration` -> a display-ready callout, or null when absent or malformed.
 * `installedComponents` is `hass.config.components` (passed in already-extracted so this stays
 * hass-free and pure): when it includes the integration's domain the CTA opens that integration's
 * own config-entry page instead of linking out. */
export function describeDedicatedIntegration(family, installedComponents) {
  const info = family?.dedicated_integration;
  if (!info || typeof info !== 'object') return null;
  const domain = String(info.domain ?? '').trim();
  const name = String(info.name ?? '').trim();
  if (!domain || !name) return null;
  const summary = String(info.summary ?? '').trim();
  const installed = Array.isArray(installedComponents) && installedComponents.includes(domain);
  return {
    domain,
    name,
    summary,
    installed,
    headline: `A full integration exists: ${name}${summary ? ` \u2014 ${summary}` : ''}`,
    ctaLabel: installed ? 'Installed \u2014 open it' : 'View the integration',
    ctaHref: installed ? entryDashboardUrl(domain) : (String(info.url ?? '').trim() || null),
  };
}

/** Device's advertised name, or its address when nameless. */
export function formatDeviceLabel(device) {
  const name = String(device?.name ?? '').trim();
  return name || formatAddress(device?.address);
}

/** A MAC address, or an em dash when missing. */
export function formatAddress(address) {
  const value = String(address ?? '').trim();
  return value || '\u2014';
}

/** Case-insensitive substring match against a device's name or address. Empty query matches everything. */
export function matchesQuery(device, query) {
  const q = String(query ?? '').trim().toLowerCase();
  if (!q) return true;
  const name = String(device?.name ?? '').toLowerCase();
  const address = String(device?.address ?? '').toLowerCase();
  return name.includes(q) || address.includes(q);
}

/** Non-mutating sort of scan rows by RSSI, strongest (closest to 0) first. Missing RSSI sorts last. */
export function sortByRssiDesc(devices) {
  if (!Array.isArray(devices)) return [];
  return [...devices].sort((a, b) => {
    const ra = Number(a?.rssi);
    const rb = Number(b?.rssi);
    const va = Number.isFinite(ra) ? ra : -Infinity;
    const vb = Number.isFinite(rb) ? rb : -Infinity;
    return vb - va;
  });
}

/** Given a device's per-source RSSI readings ({source: rssi}), pick the strongest.
 * Returns {source, rssi} or null for an empty/invalid map. Used to show "which proxy hears
 * it best" when scan/subscribe delivers one event per proxy rather than one per device. */
export function pickBestSource(sources) {
  if (!sources || typeof sources !== 'object') return null;
  let best = null;
  for (const [source, rssi] of Object.entries(sources)) {
    const value = Number(rssi);
    if (!Number.isFinite(value)) continue;
    if (!best || value > best.rssi) best = { source, rssi: value };
  }
  return best;
}

/** `decoded` is keyed by which family-specific decoder produced it (e.g. `coolled_panel`:
 * {id_hex, width, height, colour, firmware}, or `mibeacon`: {frame_control, product_id, ...}) --
 * this flattens whichever group is present into ordered {label, value} rows, dropping the group
 * key itself since the inner field names already read naturally. A flat (non-nested) decoded
 * object is also accepted defensively. `firmware` gets the same "0x21 (33)" treatment as any
 * other wire byte, since that is how the spec/evidence for this device is written down. */
export function formatDecodedFacts(decoded) {
  if (!decoded || typeof decoded !== 'object') return [];
  const rows = [];
  const pushField = (key, value) => {
    if (value === null || value === undefined) return;
    if (key === 'firmware' && Number.isInteger(Number(value))) {
      rows.push({ label: humanizeToken(String(key)), value: formatByte(value) });
      return;
    }
    rows.push({ label: humanizeToken(String(key)), value: Array.isArray(value) ? value.join(', ') : String(value) });
  };
  for (const [group, value] of Object.entries(decoded)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [key, inner] of Object.entries(value)) pushField(key, inner);
    } else {
      pushField(group, value);
    }
  }
  return rows;
}

/** Milliseconds elapsed since a baseline -> "+0 ms" / "+1.3 s", for streamed Listen frames.
 * A missing baseline (the first frame in a session) always reads as "+0 ms". Never throws. */
export function formatElapsedSince(atMs, baselineMs) {
  const at = Number(atMs);
  if (!Number.isFinite(at)) return '\u2014';
  // Number.isFinite (unlike the coercing global isFinite) rejects null/undefined/strings
  // outright, so a genuinely absent baseline (Number(null) === 0 would otherwise sneak through
  // as "a valid zero baseline") correctly falls back to `at` itself, i.e. zero elapsed.
  const base = Number.isFinite(baselineMs) ? baselineMs : at;
  return `+${formatElapsed(Math.max(0, at - base))}`;
}

/** One-line "0xFFF1 \u2014 write, notify" label for a GATT characteristic picker. */
export function describeCharacteristic(characteristic) {
  const uuid = shortUuid(characteristic?.uuid);
  const properties = formatProperties(characteristic?.properties);
  return properties.length ? `${uuid} \u2014 ${properties.join(', ')}` : uuid;
}

// Fixed codec catalog for the Learn step's picker: ids/labels mirror codecs/__init__.py's
// list_codecs() exactly. There is no WS command to fetch this (the WS API only ever takes a
// codec_id, never returns the catalog), so it is small and stable enough to keep in sync by hand.
export const CODEC_CATALOG = [
  { id: 'raw', label: 'Raw (no framing)' },
  { id: 'coolled', label: 'CoolLED (CoolLEDX / iLedClock)' },
  { id: 'iledclock', label: 'iLedClock (CoolLED framing)' },
  { id: 'prefix_suffix', label: 'Header / trailer / checksum' },
];

/** codec_id -> its catalog label, or the id itself when unrecognized (forward-compatible). */
export function describeCodec(codecId) {
  return CODEC_CATALOG.find((entry) => entry.id === codecId)?.label ?? String(codecId ?? '\u2014');
}
