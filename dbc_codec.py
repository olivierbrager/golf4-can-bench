# dbc_codec.py
# Minimal, robust DBC codec wrapper for both RX (decode) and TX (encode).
#
# - decode():  CAN frame -> (message_name, signals_dict) or (None, {})
# - encode():  (dbc_message_name, signal_map, state) -> (arb_id, data_bytes, dlc)
#
# Requirements:
#   pip install cantools
#
# Notes:
# - This wrapper intentionally keeps a small surface area and avoids app-specific logic.
# - It supports state being a dataclass, dict, or generic object with attributes.

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional, Tuple

import cantools


class DbcCodec:
    def __init__(self, dbc_path: str, strict: bool = False) -> None:
        """
        dbc_path: path to .dbc file
        strict:   pass-through to cantools (False is generally more forgiving)
        """
        self.db = cantools.database.load_file(dbc_path, strict=strict)

        # Optional fast lookups
        self._by_name: Dict[str, Any] = {m.name: m for m in self.db.messages}
        self._by_frame_id: Dict[int, Any] = {m.frame_id: m for m in self.db.messages}

    # -------------------------
    # RX: Decode
    # -------------------------
    def decode(
        self,
        arbitration_id: int,
        data: bytes | bytearray,
        *,
        decode_choices: bool = False,
    ) -> Tuple[Optional[str], Dict[str, Any]]:
        """
        Decode a CAN frame using the DBC.

        Returns:
          (message_name, signals) if frame_id exists in DBC
          (None, {}) otherwise

        decode_choices:
          If True, cantools will decode choice values to strings when possible.
        """
        msg = self._by_frame_id.get(arbitration_id)
        if msg is None:
            return None, {}

        try:
            # cantools expects bytes-like
            raw = bytes(data)
            signals = msg.decode(raw, decode_choices=decode_choices)
            return msg.name, signals
        except Exception:
            # Decode errors (length mismatch, scaling, etc.) -> treat as unknown/undecodable
            return msg.name, {}

    # -------------------------
    # TX: Encode
    # -------------------------
    def encode(
        self,
        dbc_message_name: str,
        signal_map: Dict[str, str],
        state_obj: Any,
    ) -> Tuple[int, bytes, int]:
        """
        Encode a DBC message from a state object.

        dbc_message_name: DBC message name, e.g. "ECU_Status"
        signal_map: dict mapping DBC signal name -> field name in state_obj
                    example: {"EngineSpeed": "rpm", "VehicleSpeed": "speed"}
        state_obj: dataclass instance, dict, or object with attributes.

        Returns:
          (arbitration_id, data_bytes, dlc)
        """
        msg = self._by_name.get(dbc_message_name)
        if msg is None:
            # Keep error explicit here: TX config must match DBC
            raise KeyError(f"DBC message not found: {dbc_message_name}")

        # Normalize state -> dict for easy access
        if is_dataclass(state_obj):
            st: Optional[Dict[str, Any]] = asdict(state_obj)
        elif isinstance(state_obj, dict):
            st = state_obj
        else:
            st = None  # fallback to getattr

        sig_values: Dict[str, Any] = {}
        for sig_name, field_name in (signal_map or {}).items():
            if st is not None:
                val = st.get(field_name)
            else:
                val = getattr(state_obj, field_name, None)

            # If missing, skip (cantools will use default if defined; otherwise may raise)
            if val is None:
                continue

            sig_values[sig_name] = val

        # Encode (cantools handles scaling/offset/range checks; may raise)
        data = msg.encode(sig_values)

        # dlc/length is defined by DBC message length
        dlc = int(msg.length)
        arb_id = int(msg.frame_id)

        return arb_id, data, dlc

    # -------------------------
    # Convenience helpers (optional)
    # -------------------------
    def message_names(self) -> list[str]:
        return [m.name for m in self.db.messages]

    def frame_ids(self) -> list[int]:
        return [int(m.frame_id) for m in self.db.messages]
