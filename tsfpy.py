"""
TinySoundFont Python wrapper via ctypes.

Usage:
    from tsfpy import TinySoundFont, TSF_STEREO_INTERLEAVED

    with TinySoundFont("piano.sf2") as synth:
        synth.set_output(sample_rate=44100)
        samples = synth.render_float(num_samples=1024)

Build the shared library first:
    Linux : gcc -shared -fPIC -o libtsf.so tsf.h -lm -lrt
    macOS  : gcc -shared -fPIC -o libtsf.dylib tsf.h -lm
    Windows: cl /LD tsf.h
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import struct
from typing import List, Optional, Union


# ---------------------------------------------------------------------------
# Load shared library
# ---------------------------------------------------------------------------

_LIB_NAMES: List[str] = [
    "tsf",
    "libtsf",
    "libtsf.so",
    "libtsf.dylib",
    "libtsf.dll",
]


def _find_lib() -> ctypes.CDLL:
    search_dirs = [os.path.dirname(__file__) or ".", "."]
    for directory in search_dirs:
        for name in _LIB_NAMES:
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                try:
                    return ctypes.CDLL(path)
                except OSError:
                    pass

    # Try system lookup
    soname = ctypes.util.find_library("tsf")
    if soname:
        try:
            return ctypes.CDLL(soname)
        except OSError:
            pass

    # Try loading by soname directly (ldconfig / dyld)
    for soname in ("libtsf.so", "libtsf.dylib", "libtsf.dll"):
        try:
            return ctypes.CDLL(soname)
        except OSError:
            pass

    raise ImportError(
        "TinySoundFont shared library not found.\n"
        "Build it first:\n"
        "  Linux : gcc -shared -fPIC -o libtsf.so tsf.h -lm -lrt\n"
        "  macOS  : gcc -shared -fPIC -o libtsf.dylib tsf.h -lm\n"
        "  Windows: cl /LD tsf.h\n"
        "Then ensure libtsf.so / libtsf.dylib is on your library path."
    )


_lib = _find_lib()

# C type aliases
tsf_handle = ctypes.c_void_p

# enum TSFOutputMode
TSF_STEREO_INTERLEAVED = 0
TSF_STEREO_UNWEAVED = 1
TSF_MONO = 2

_OUTPUT_MODE_CHANNELS = {
    TSF_MONO: 1,
    TSF_STEREO_INTERLEAVED: 2,
    TSF_STEREO_UNWEAVED: 2,
}


# ---------------------------------------------------------------------------
# Declare C function prototypes
# ---------------------------------------------------------------------------


def _proto(name, restype, argtypes):
    fn = getattr(_lib, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


# --- Loading / lifecycle ---------------------------------------------------
_lib.tsf_load_filename.restype = tsf_handle
_lib.tsf_load_filename.argtypes = [ctypes.c_char_p]

_lib.tsf_load_memory.restype = tsf_handle
_lib.tsf_load_memory.argtypes = [ctypes.c_void_p, ctypes.c_int]

_lib.tsf_load.restype = tsf_handle
_lib.tsf_load.argtypes = [ctypes.c_void_p]

_lib.tsf_copy.restype = tsf_handle
_lib.tsf_copy.argtypes = [tsf_handle]

_lib.tsf_close.argtypes = [tsf_handle]
_lib.tsf_reset.argtypes = [tsf_handle]

# --- Preset info -----------------------------------------------------------
_lib.tsf_get_presetindex.restype = ctypes.c_int
_lib.tsf_get_presetindex.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]

_lib.tsf_get_presetcount.restype = ctypes.c_int
_lib.tsf_get_presetcount.argtypes = [tsf_handle]

_lib.tsf_get_presetname.restype = ctypes.c_char_p
_lib.tsf_get_presetname.argtypes = [tsf_handle, ctypes.c_int]

_lib.tsf_bank_get_presetname.restype = ctypes.c_char_p
_lib.tsf_bank_get_presetname.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]

# --- Output setup ----------------------------------------------------------
_lib.tsf_set_output.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int, ctypes.c_float]
_lib.tsf_set_volume.argtypes = [tsf_handle, ctypes.c_float]
_lib.tsf_set_max_voices.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_set_max_voices.restype = ctypes.c_int

# --- Notes -----------------------------------------------------------------
_lib.tsf_note_on.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int, ctypes.c_float]
_lib.tsf_note_on.restype = ctypes.c_int

_lib.tsf_bank_note_on.argtypes = [
    tsf_handle,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_float,
]
_lib.tsf_bank_note_on.restype = ctypes.c_int

_lib.tsf_note_off.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]
_lib.tsf_bank_note_off.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int, ctypes.c_int]
_lib.tsf_bank_note_off.restype = ctypes.c_int

_lib.tsf_note_off_all.argtypes = [tsf_handle]
_lib.tsf_channel_note_off.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]
_lib.tsf_channel_note_on.argtypes = [
    tsf_handle,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_float,
]
_lib.tsf_channel_note_on.restype = ctypes.c_int
_lib.tsf_channel_sounds_off_all.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_note_off_all.argtypes = [tsf_handle, ctypes.c_int]

_lib.tsf_active_voice_count.argtypes = [tsf_handle]
_lib.tsf_active_voice_count.restype = ctypes.c_int

# --- Rendering -------------------------------------------------------------
_lib.tsf_render_short.argtypes = [
    tsf_handle,
    ctypes.POINTER(ctypes.c_short),
    ctypes.c_int,
    ctypes.c_int,
]
_lib.tsf_render_float.argtypes = [
    tsf_handle,
    ctypes.POINTER(ctypes.c_float),
    ctypes.c_int,
    ctypes.c_int,
]

# --- Channel parameters ----------------------------------------------------
_lib.tsf_channel_set_presetindex.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]
_lib.tsf_channel_set_presetindex.restype = ctypes.c_int

_lib.tsf_channel_set_presetnumber.argtypes = [
    tsf_handle,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
]
_lib.tsf_channel_set_presetnumber.restype = ctypes.c_int

_lib.tsf_channel_set_bank.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]
_lib.tsf_channel_set_bank.restype = ctypes.c_int

_lib.tsf_channel_set_bank_preset.argtypes = [
    tsf_handle,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
]
_lib.tsf_channel_set_bank_preset.restype = ctypes.c_int

_lib.tsf_channel_set_pan.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_float]
_lib.tsf_channel_set_pan.restype = ctypes.c_int

_lib.tsf_channel_set_volume.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_float]
_lib.tsf_channel_set_volume.restype = ctypes.c_int

_lib.tsf_channel_set_pitchwheel.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]
_lib.tsf_channel_set_pitchwheel.restype = ctypes.c_int

_lib.tsf_channel_set_pitchrange.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_float]
_lib.tsf_channel_set_pitchrange.restype = ctypes.c_int

_lib.tsf_channel_set_tuning.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_float]
_lib.tsf_channel_set_tuning.restype = ctypes.c_int

_lib.tsf_channel_set_sustain.argtypes = [tsf_handle, ctypes.c_int, ctypes.c_int]
_lib.tsf_channel_set_sustain.restype = ctypes.c_int

_lib.tsf_channel_midi_control.argtypes = [
    tsf_handle,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
]
_lib.tsf_channel_midi_control.restype = ctypes.c_int

# Channel getters
_lib.tsf_channel_get_preset_index.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_preset_index.restype = ctypes.c_int

_lib.tsf_channel_get_preset_bank.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_preset_bank.restype = ctypes.c_int

_lib.tsf_channel_get_preset_number.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_preset_number.restype = ctypes.c_int

_lib.tsf_channel_get_pan.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_pan.restype = ctypes.c_float

_lib.tsf_channel_get_volume.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_volume.restype = ctypes.c_float

_lib.tsf_channel_get_pitchwheel.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_pitchwheel.restype = ctypes.c_int

_lib.tsf_channel_get_pitchrange.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_pitchrange.restype = ctypes.c_float

_lib.tsf_channel_get_tuning.argtypes = [tsf_handle, ctypes.c_int]
_lib.tsf_channel_get_tuning.restype = ctypes.c_float


# ---------------------------------------------------------------------------
# Python wrapper
# ---------------------------------------------------------------------------


class TinySoundFont:
    """
    High-level Python wrapper around TinySoundFont (single-header C library).

    Parameters
    ----------
    filename : str, optional
        Path to a ``.sf2`` SoundFont file.
    data : bytes, optional
        Raw bytes of a SoundFont file (ignored if ``filename`` is given).
    stream : ctypes.Structure, optional
        Custom ``tsf_stream`` for alternative loading backends.

    Example
    -------
    ::

        with TinySoundFont("piano.sf2") as synth:
            synth.set_output(sample_rate=44100)
            samples = synth.render_float(num_samples=1024)
    """

    def __init__(
        self,
        filename: Optional[str] = None,
        data: Optional[bytes] = None,
        stream=None,
    ) -> None:
        if filename is not None:
            handle = _lib.tsf_load_filename(filename.encode("utf-8"))
        elif data is not None:
            handle = _lib.tsf_load_memory(
                ctypes.c_char_p(data), ctypes.c_int(len(data))
            )
        elif stream is not None:
            handle = _lib.tsf_load(ctypes.byref(stream))
        else:
            raise TypeError("One of filename, data or stream must be provided.")

        if not handle:
            raise RuntimeError("Failed to load SoundFont")

        self._handle: ctypes.c_void_p = handle
        self._output_mode: int = TSF_STEREO_INTERLEAVED
        self._sample_rate: int = 44100

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "TinySoundFont":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Free the SoundFont and release all associated memory."""
        if self._handle:
            _lib.tsf_close(self._handle)
            self._handle = None  # type: ignore[assignment]

    def copy(self) -> "TinySoundFont":
        """
        Create a new, independent instance that shares the same SoundFont data.

        The new instance can play notes while the original is still in use.
        """
        handle = _lib.tsf_copy(self._handle)
        if not handle:
            raise RuntimeError("tsf_copy failed")
        new = TinySoundFont.__new__(TinySoundFont)
        new._handle = handle
        new._output_mode = self._output_mode
        new._sample_rate = self._sample_rate
        return new

    def reset(self) -> None:
        """Stop all playing notes immediately and reset channel parameters."""
        _lib.tsf_reset(self._handle)

    # ------------------------------------------------------------------
    # Output configuration
    # ------------------------------------------------------------------

    def set_output(
        self,
        sample_rate: int = 44100,
        output_mode: int = TSF_STEREO_INTERLEAVED,
        global_gain_db: float = 0.0,
    ) -> None:
        """
        Configure the audio output format.

        Parameters
        ----------
        sample_rate:
            Samples per second (e.g. 44100, 48000).
        output_mode:
            One of ``TSF_STEREO_INTERLEAVED``, ``TSF_STEREO_UNWEAVED``, ``TSF_MONO``.
        global_gain_db:
            Overall volume in decibels (0 = unity, negative = quieter).
        """
        self._output_mode = output_mode
        self._sample_rate = sample_rate
        _lib.tsf_set_output(self._handle, output_mode, sample_rate, global_gain_db)

    def set_volume(self, volume: float) -> None:
        """Set global volume factor (1.0 = full volume)."""
        _lib.tsf_set_volume(self._handle, volume)

    def set_max_voices(self, max_voices: int) -> bool:
        """
        Pre-allocate voice slots to avoid allocations during playback.

        Returns ``True`` on success, ``False`` if allocation failed.
        """
        return bool(_lib.tsf_set_max_voices(self._handle, max_voices))

    # ------------------------------------------------------------------
    # Preset / program info
    # ------------------------------------------------------------------

    @property
    def preset_count(self) -> int:
        """Total number of presets available in the SoundFont."""
        return _lib.tsf_get_presetcount(self._handle)

    def get_preset_name(self, preset_index: int) -> str:
        """Return the name of a preset by its index."""
        name = _lib.tsf_get_presetname(self._handle, preset_index)
        return name.decode("utf-8") if name else ""

    def get_preset_index(self, bank: int, preset_number: int) -> int:
        """
        Look up preset index by bank and preset number.

        Returns -1 if not found.
        """
        return _lib.tsf_get_presetindex(self._handle, bank, preset_number)

    def get_bank_preset_name(self, bank: int, preset_number: int) -> str:
        """Return the name of a preset given its bank and preset number."""
        name = _lib.tsf_bank_get_presetname(self._handle, bank, preset_number)
        return name.decode("utf-8") if name else ""

    # ------------------------------------------------------------------
    # Note on / off
    # ------------------------------------------------------------------

    def note_on(
        self,
        preset_index: int,
        key: int,
        velocity: float,
        *,
        bank: Optional[int] = None,
        preset_number: Optional[int] = None,
    ) -> bool:
        """
        Start playing a note.

        Parameters
        ----------
        preset_index:
            Index of the preset to use (0 .. ``preset_count - 1``).
        key:
            MIDI note number (0-127, 60 = middle C).
        velocity:
            Note velocity from 0.0 (silent) to 1.0 (max).
        bank:
            Bank number (alternative to ``preset_index``).
        preset_number:
            Preset number within bank (used together with ``bank``).

        Returns
        -------
        True if the voice was allocated successfully.
        """
        if bank is not None and preset_number is not None:
            return bool(
                _lib.tsf_bank_note_on(self._handle, bank, preset_number, key, velocity)
            )
        return bool(_lib.tsf_note_on(self._handle, preset_index, key, velocity))

    def note_off(
        self,
        preset_index: int,
        key: int,
        *,
        bank: Optional[int] = None,
        preset_number: Optional[int] = None,
    ) -> bool:
        """Stop a previously started note (release phase applies)."""
        if bank is not None and preset_number is not None:
            return bool(_lib.tsf_bank_note_off(self._handle, bank, preset_number, key))
        _lib.tsf_note_off(self._handle, preset_index, key)
        return True

    def note_off_all(self) -> None:
        """Stop all notes with the release phase applied."""
        _lib.tsf_note_off_all(self._handle)

    def channel_note_on(self, channel: int, key: int, velocity: float) -> bool:
        """
        Start playing a note on a channel.

        The channel's preset must be set first via ``channel_set_preset_index``.
        """
        return bool(_lib.tsf_channel_note_on(self._handle, channel, key, velocity))

    def channel_note_off(self, channel: int, key: int) -> None:
        """Stop a specific note on a given channel."""
        _lib.tsf_channel_note_off(self._handle, channel, key)

    def channel_note_off_all(self, channel: int) -> None:
        """Stop all notes on a channel with release."""
        _lib.tsf_channel_note_off_all(self._handle, channel)

    def channel_sounds_off_all(self, channel: int) -> None:
        """Immediately stop all notes on a channel (no release)."""
        _lib.tsf_channel_sounds_off_all(self._handle, channel)

    @property
    def active_voice_count(self) -> int:
        """Number of currently active (playing) voices."""
        return _lib.tsf_active_voice_count(self._handle)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @property
    def _channel_count(self) -> int:
        return _OUTPUT_MODE_CHANNELS.get(self._output_mode, 2)

    def render_float(self, num_samples: int, mixing: bool = False) -> List[float]:
        """
        Render *num_samples* audio frames as 32-bit floats.

        Returns a flat list: for stereo modes each frame contributes
        ``[left, right, left, right, ...]``; for mono one value per frame.

        Parameters
        ----------
        num_samples:
            Number of frames to render.
        mixing:
            If ``True``, mix output into an existing buffer instead of clearing it.
            (Currently always returns a fresh list — mixing is handled in C.)

        Returns
        -------
        list of float
            Raw PCM float samples in the range [-1.0, 1.0].
        """
        channels = self._channel_count
        buf = (ctypes.c_float * (num_samples * channels))()
        _lib.tsf_render_float(self._handle, buf, num_samples, int(mixing))
        return list(buf)

    def render_short(self, num_samples: int, mixing: bool = False) -> List[int]:
        """
        Render *num_samples* audio frames as 16-bit signed integers.

        Returns a flat list of integers in the range [-32768, 32767].

        Parameters
        ----------
        num_samples:
            Number of frames to render.
        mixing:
            If ``True``, mix into existing content.

        Returns
        -------
        list of int
        """
        channels = self._channel_count
        buf = (ctypes.c_short * (num_samples * channels))()
        _lib.tsf_render_short(self._handle, buf, num_samples, int(mixing))
        return list(buf)

    def render_bytes(self, num_samples: int, mixing: bool = False) -> bytes:
        """
        Render and return raw PCM bytes (16-bit signed little-endian stereo/mono).
        """
        samples = self.render_short(num_samples, mixing=mixing)
        return struct.pack(f"<{len(samples)}h", *samples)

    # ------------------------------------------------------------------
    # Channel control
    # ------------------------------------------------------------------

    def channel_set_preset_index(self, channel: int, preset_index: int) -> bool:
        """Assign a preset to a channel by preset index."""
        return bool(
            _lib.tsf_channel_set_presetindex(self._handle, channel, preset_index)
        )

    def channel_set_preset_number(
        self, channel: int, preset_number: int, midi_drums: bool = False
    ) -> bool:
        """Assign a preset to a channel by (bank, preset_number) pair."""
        return bool(
            _lib.tsf_channel_set_presetnumber(
                self._handle, channel, preset_number, int(midi_drums)
            )
        )

    def channel_set_bank(self, channel: int, bank: int) -> bool:
        """Set the MIDI bank for a channel."""
        return bool(_lib.tsf_channel_set_bank(self._handle, channel, bank))

    def channel_set_bank_preset(
        self, channel: int, bank: int, preset_number: int
    ) -> bool:
        """Set both bank and preset number on a channel in one call."""
        return bool(
            _lib.tsf_channel_set_bank_preset(self._handle, channel, bank, preset_number)
        )

    def channel_set_pan(self, channel: int, pan: float) -> bool:
        """Set stereo panning (0.0 = full left, 0.5 = center, 1.0 = full right)."""
        return bool(_lib.tsf_channel_set_pan(self._handle, channel, pan))

    def channel_set_volume(self, channel: int, volume: float) -> bool:
        """Set channel volume factor (1.0 = full)."""
        return bool(_lib.tsf_channel_set_volume(self._handle, channel, volume))

    def channel_set_pitchwheel(self, channel: int, pitch_wheel: int) -> bool:
        """Set pitch wheel position (0-16383, 8192 = center)."""
        return bool(_lib.tsf_channel_set_pitchwheel(self._handle, channel, pitch_wheel))

    def channel_set_pitchrange(self, channel: int, pitch_range: float) -> bool:
        """Set pitch wheel range in semitones (default 2.0 → ±2 semitones)."""
        return bool(_lib.tsf_channel_set_pitchrange(self._handle, channel, pitch_range))

    def channel_set_tuning(self, channel: int, tuning: float) -> bool:
        """Set global tuning offset for the channel in semitones."""
        return bool(_lib.tsf_channel_set_tuning(self._handle, channel, tuning))

    def channel_set_sustain(self, channel: int, flag: bool) -> bool:
        """Enable (True) or disable (False) the sustain pedal."""
        return bool(_lib.tsf_channel_set_sustain(self._handle, channel, int(flag)))

    def channel_midi_control(
        self, channel: int, controller: int, control_value: int
    ) -> bool:
        """Send a MIDI Control Change message. Only some controllers are supported."""
        return bool(
            _lib.tsf_channel_midi_control(
                self._handle, channel, controller, control_value
            )
        )

    # --- Channel getters --------------------------------------------------

    def channel_get_preset_index(self, channel: int) -> int:
        return _lib.tsf_channel_get_preset_index(self._handle, channel)

    def channel_get_preset_bank(self, channel: int) -> int:
        return _lib.tsf_channel_get_preset_bank(self._handle, channel)

    def channel_get_preset_number(self, channel: int) -> int:
        return _lib.tsf_channel_get_preset_number(self._handle, channel)

    def channel_get_pan(self, channel: int) -> float:
        return _lib.tsf_channel_get_pan(self._handle, channel)

    def channel_get_volume(self, channel: int) -> float:
        return _lib.tsf_channel_get_volume(self._handle, channel)

    def channel_get_pitchwheel(self, channel: int) -> int:
        return _lib.tsf_channel_get_pitchwheel(self._handle, channel)

    def channel_get_pitchrange(self, channel: int) -> float:
        return _lib.tsf_channel_get_pitchrange(self._handle, channel)

    def channel_get_tuning(self, channel: int) -> float:
        return _lib.tsf_channel_get_tuning(self._handle, channel)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "TinySoundFont",
    "TSF_STEREO_INTERLEAVED",
    "TSF_STEREO_UNWEAVED",
    "TSF_MONO",
]
