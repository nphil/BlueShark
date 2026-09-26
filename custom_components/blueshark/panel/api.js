// Thin wrapper over hass.callWS / hass.connection.subscribeMessage for the `blueshark/*`
// WebSocket API. Every method here corresponds 1:1 to one command from the panel contract's
// "WebSocket API" section; the JSDoc on each documents the exact request/response shape so the
// engine implementation can be checked against it. camelCase JS arguments are mapped to the
// snake_case wire field names at the call site, nowhere else.

import { formatApiErrorMessage } from './format.js';

/** Normalized failure: `.code` is one of the six documented WS_ERROR_* strings (or null for a
 * transport-level failure), `.message` is human-facing (engine text plus a hint when the code is
 * known), and `.raw` keeps whatever hass.callWS/subscribeMessage actually rejected with. */
export class ApiError extends Error {
  constructor(raw) {
    const errorLike = raw && typeof raw === 'object' ? raw : { message: String(raw ?? 'Unknown error') };
    super(formatApiErrorMessage(errorLike));
    this.name = 'ApiError';
    this.code = errorLike.code ?? null;
    this.raw = raw;
  }
}

function normalizeError(err) {
  return err instanceof ApiError ? err : new ApiError(err);
}

const NOT_READY = { code: 'unsupported', message: 'Home Assistant is not connected yet.' };

export class BlueSharkApi {
  constructor(hass) {
    this.hass = hass;
  }

  async _call(message) {
    if (!this.hass || typeof this.hass.callWS !== 'function') {
      throw new ApiError(NOT_READY);
    }
    try {
      return await this.hass.callWS(message);
    } catch (err) {
      throw normalizeError(err);
    }
  }

  /** Resolves once the subscription is live, to an unsubscribe function. `onMessage` receives
   * every streamed payload verbatim (this layer does not interpret message shape). */
  async _subscribe(message, onMessage) {
    if (!this.hass?.connection || typeof this.hass.connection.subscribeMessage !== 'function') {
      throw new ApiError(NOT_READY);
    }
    try {
      return await this.hass.connection.subscribeMessage((result) => onMessage(result), message);
    } catch (err) {
      throw normalizeError(err);
    }
  }

  /**
   * blueshark/scan/subscribe {} -> stream of
   * {address, name, rssi, source, connectable, service_uuids, manufacturer_data: {id: hex},
   *  family: {id, name, confidence, confidence_label, evidence: [str]} | null}
   * Runs until unsubscribed by the caller (fed by HA's own advertisement callbacks).
   */
  subscribeScan(onEvent) {
    return this._subscribe({ type: 'blueshark/scan/subscribe' }, onEvent);
  }

  /**
   * blueshark/identify {address} ->
   * {matches: [{id, name, confidence, confidence_label, evidence, codec_id,
   *   characteristic_hints, driver_url, starter_command_map: {key: entry}|null,
   *   dedicated_integration: {domain, name, url, summary}|null,
   *   safety: [{opcode, reason}]}], decoded: {..}}
   * The three starter_command_map/dedicated_integration/safety fields are optional per match (an
   * older engine, or a family with nothing to offer, omits or nulls them); every consumer here
   * treats their absence as "nothing to offer" rather than an error.
   */
  identify(address) {
    return this._call({ type: 'blueshark/identify', address });
  }

  /**
   * blueshark/enumerate {address} ->
   * {services: [{uuid, characteristics: [{uuid, handle, properties: [str]}]}],
   *  suggested: {service, characteristic, codec_id} | null}
   * Connects over the freshest connectable route; suggested is treated as nullable even though
   * the contract shows it unconditionally, since a device may expose no write+notify pair.
   */
  enumerate(address) {
    return this._call({ type: 'blueshark/enumerate', address });
  }

  /**
   * blueshark/send {address, characteristic, payload_hex, codec_id, framed, await_response_ms} ->
   * {sent_hex, response_hex|null, verdict, status|null, elapsed_ms}
   * `framed: true` sends payloadHex verbatim, bypassing codec_id's encode() step.
   */
  send({ address, characteristic, payloadHex, codecId, framed = false, awaitResponseMs }) {
    return this._call({
      type: 'blueshark/send',
      address,
      characteristic,
      payload_hex: payloadHex,
      codec_id: codecId,
      framed,
      await_response_ms: awaitResponseMs,
    });
  }

  /**
   * blueshark/sweep/start {address, characteristic, codec_id, start, end, argument_hex,
   *  include_destructive, step_delay_ms, await_response_ms} -> one subscription that delivers,
   * in order: {run_id}, then repeated
   * {index, total, opcode, sent_hex, response_hex, verdict, status, elapsed_ms, canary}, then a
   * final {done: true, accepted: [...], unknown: [...], no_response: [...], aborted_reason|null}.
   * Resolves to an unsubscribe function once the subscription is live.
   */
  startSweep({ address, characteristic, codecId, start, end, argumentHex, includeDestructive, stepDelayMs, awaitResponseMs }, onMessage) {
    return this._subscribe(
      {
        type: 'blueshark/sweep/start',
        address,
        characteristic,
        codec_id: codecId,
        start,
        end,
        argument_hex: argumentHex,
        include_destructive: includeDestructive,
        step_delay_ms: stepDelayMs,
        await_response_ms: awaitResponseMs,
      },
      onMessage,
    );
  }

  /** blueshark/sweep/stop {run_id} -> {...} (ack; fields not documented, treated as opaque). */
  stopSweep(runId) {
    return this._call({ type: 'blueshark/sweep/stop', run_id: runId });
  }

  /**
   * blueshark/listen {address, characteristic, seconds} -> streamed {at_ms, hex} frames for
   * `seconds` seconds. Resolves to an unsubscribe function once the subscription is live.
   */
  listen({ address, characteristic, seconds }, onFrame) {
    return this._subscribe({ type: 'blueshark/listen', address, characteristic, seconds }, onFrame);
  }

  /** blueshark/commands/set {address, command_map} -> {...} (persists + rebuilds entities). */
  setCommands({ address, commandMap }) {
    return this._call({ type: 'blueshark/commands/set', address, command_map: commandMap });
  }

  /** blueshark/commands/get {address} -> {command_map, opcode_log_tail} */
  getCommands(address) {
    return this._call({ type: 'blueshark/commands/get', address });
  }

  /**
   * blueshark/create_entry {address, name, codec_id, characteristic, command_map} -> {entry_id}
   * Creates the config entry when the wizard runs before one exists.
   */
  createEntry({ address, name, codecId, characteristic, commandMap }) {
    return this._call({
      type: 'blueshark/create_entry',
      address,
      name,
      codec_id: codecId,
      characteristic,
      command_map: commandMap,
    });
  }
}
