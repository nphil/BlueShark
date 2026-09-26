"""iLedClock: same CoolLED byte-stuffed framing as ``coolled``, but its own opcode table.

The vendor app (``com.jtkj.led1248``) picks a protocol per advertised BLE name
(``DeviceManager.java``, the ``updateDeviceInfo``/``onNotifySuccess`` dispatch around line
1366-1375 branches on ``bleDevice.getName().equalsIgnoreCase(ILED_CLOCK)``): a device named
exactly ``iLedClock`` is driven entirely by ``ILedClockUtils.java`` (jadx-decompiled sources
under ``/data/home/tmp/led1248/src/sources/com/jtkj/led1248/light/``), whose opcode table
shares zero opcodes in common with the CoolLEDX table the generic ``coolled`` codec's deny
list is partly built from - opcode ``0x08`` means "brightness" on CoolLEDX and is not a
defined command at all on iLedClock (brightness here is opcode ``0x04``). One shared deny
list was wrong for both; this codec is the iLedClock-specific one, and ``coolled`` no longer
claims a device that advertises exactly this name (see ``families.py``).

Framing is byte-for-byte identical to ``coolled`` (see that module's docstring for the
corrected escape rule), because ``ILedClockUtils.getSendDataWithInfo`` (line 2455) builds the
exact same start/length/escape/end frame every other CoolLED variant does.

Opcode table (``ILedClockUtils.java``, lines 4732-5337, one builder method per command; reply
parser in ``DeviceManager.java`` around line 4580-4665): ``01`` music data, ``02``
program-upload start, ``03`` program data chunk, ``04`` brightness, ``05`` power (01/00),
``06`` rhythm type, ``09`` sync time, ``0A`` SET timer switches (overwrites all), ``0B`` get
timer switches, ``0C`` mirror/rotate (0-3), ``0D`` check password (read-only), ``0E`` SET
password, ``0F`` countdown, ``10`` stopwatch, ``11`` scoreboard, ``13`` colour, ``14`` night
mode, ``15`` pomodoro, ``16`` alarms, ``19`` temp/humidity, ``1A`` reminders, ``1E`` device
settings, ``1F`` device info (read-only), ``FD`` firmware version (read-only), ``FE`` OTA
start, ``FF`` OTA data. Replies echo the opcode (and sub-op where present), e.g. a bare ``1f``
request replies ``1f 01 a3 00 ...`` (live capture,
``/data/home/ha-iledclock/tests/live_replies_2026-09-25.json``).
"""

from __future__ import annotations

from .coolled import CoolLedCodec


class IledClockCodec(CoolLedCodec):
    id = "iledclock"
    label = "iLedClock (CoolLED framing)"

    # Sub-op numbers below are the second payload byte. A sweep probe is `opcode + one
    # argument byte` (default 0x01); for a multi-sub-op opcode whose sub-op 01 is a SET and
    # whose sub-op 02 is a harmless read (14, 15, 16), that default probe hits the dangerous
    # SET path, not the read - so the opcode as a whole is blocked unless the caller opts into
    # `include_destructive`. 0x0A and 0x0E were in fact sent by an earlier, pre-split generic
    # probe against a real device with no lasting harm, before iLedClock had its own opcode
    # table and deny list instead of reusing CoolLEDX's.
    destructive_opcodes = frozenset(
        {0x02, 0x03, 0x09, 0x0A, 0x0E, 0x14, 0x15, 0x16, 0x1A, 0xFE, 0xFF}
    )
    destructive_reasons: dict[int, str] = {
        # ILedClockUtils.java:4805-4811 getStartOTAUpdate is unrelated; the program-upload
        # start is ILedClockUtils.java's "02" builder reached via getDataResult (line 4685+),
        # which begins a LZSS-compressed multi-packet transfer.
        0x02: (
            "Starts a program/animation upload; a bare sweep probe sends a malformed upload "
            "start and can leave the device wedged mid-upload."
        ),
        0x03: (
            "Sends one program-upload data chunk; used out of sequence it can corrupt an "
            "in-progress upload or overwrite clock content."
        ),
        # ILedClockUtils.java:4834-4888 getSynchronizeTime().
        0x09: "Sets the device's date and time; a sweep probe overwrites the clock with garbage date/time fields.",
        # ILedClockUtils.java:4995-5029 setTimerSwitch(list): passing no list still sends a
        # "0 timers" SET, overwriting whatever was configured.
        0x0A: (
            "Overwrites every scheduled on/off timer switch with sweep-probe garbage - this "
            "opcode was actually sent as a generic probe in a prior session, before codecs "
            "were split by device family."
        ),
        # ILedClockUtils.java:4786-4803 getSetPasswordData(str).
        0x0E: "Sets the device password; a wrong value can lock you out of the vendor app.",
        # ILedClockUtils.java:5298-5313 getSetNightMode(...) is sub-op 01; sub-op 02
        # (ILedClockUtils.java:5291-5296 getNightMode()) only reads the 10 bytes back.
        0x14: (
            "Sub-op 01 overwrites all 10 night-mode bytes; a bare sweep probe (opcode + "
            "0x01) hits that SET path with garbage instead of the harmless sub-op 02 read."
        ),
        # ILedClockUtils.java:5225-5235 getSetTomatoClockTime(list) is sub-op 01; sub-op 02
        # (ILedClockUtils.java:5237-5242 getTomatoClockTime()) only reads the list back.
        0x15: (
            "Sub-op 01 overwrites the whole pomodoro/tomato-clock list; a bare sweep probe "
            "hits that SET path instead of the harmless sub-op 02 read."
        ),
        # ILedClockUtils.java:5244-5275 getSetAlarmClockTime(list) is sub-op 01; sub-op 02
        # (ILedClockUtils.java:5277-5282 getAlarmClockTime()) only reads the alarms back.
        0x16: (
            "Sub-op 01 overwrites every alarm at once; a bare sweep probe hits that SET path "
            "instead of the harmless sub-op 02 read."
        ),
        # ILedClockUtils.java:5322-5328 getDeleteReminder(int) is sub-op 03; sub-op 01
        # (ILedClockUtils.java:5315-5320 getReminder()) only lists reminders.
        0x1A: (
            "Sub-op 03 deletes a reminder by index; a sweep probe can delete reminder 0x01 "
            "instead of hitting the harmless sub-op 01 list read."
        ),
        # ILedClockUtils.java:4805-4811 getStartOTAUpdate(list).
        0xFE: "Starts an OTA firmware update; an interrupted or malformed transfer can brick the device.",
        # ILedClockUtils.java:4813-4817 getOTAUpdate(list) (packetised OTA data, opcode ff per getDataPacket).
        0xFF: "Sends OTA firmware data; an interrupted or malformed transfer can brick the device.",
    }
    # ILedClockUtils.java:4732-4736 getDeviceInfo(): payload is the bare opcode with no
    # argument. Read-only, always replies (see module docstring), never changes the display -
    # DeviceManager.java:4580-4651 only ever reads fields out of the reply, it writes nothing
    # back to the device as a result.
    canary = (0x1F, b"")
