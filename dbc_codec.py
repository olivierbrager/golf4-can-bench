from __future__ import annotations

from dataclasses import dataclass, asdict, is_dataclass
from typing import Any, Dict, Optional, Tuple

import cantools


@dataclass(frozen=True)
class DecodedFrame:
    name: str
    arb_id: int
    signals: Dict[str, Any]
    ts: float = 0.0


class DbcCodec:
    """
    DBC codec compatible with:
      - can_reader.py expectations:
          decoded = codec.decode(..., ts=ts)
          decoded.arb_id / decoded.name / decoded.signals
          codec._by_id.get(decoded.arb_id) for units lookup
      - can_tx_emulator.py expectations:
          arb_id, data, dlc = codec.encode(dbc_message_name, signal_map, state)
    """

    def __init__(self, dbc_path: str, strict: bool = False) -> None:
        self.db = cantools.database.load_file(dbc_path, strict=strict)

        # Public-ish caches (used by can_reader.py for units)
        self._by_id: Dict[int, Any] = {int(m.frame_id): m for m in self.db.messages}
        self._by_name: Dict[str, Any] = {m.name: m for m in self.db.messages}

    # -------------------------
    # RX: Decode
    # -------------------------
    def decode(
        self,
        arbitration_id: int,
        data: bytes | bytearray,
        *,
        ts: float = 0.0,
        decode_choices: bool = False,
    ) -> Optional[DecodedFrame]:
        msg = self._by_id.get(int(arbitration_id))
        if msg is None:
            return None
        try:
            sigs = msg.decode(bytes(data), decode_choices=decode_choices)
            return DecodedFrame(name=msg.name, arb_id=int(arbitration_id), signals=sigs, ts=float(ts or 0.0))
        except Exception:
            # Don't kill the reader on a single bad frame/length mismatch.
            return None

    # -------------------------
    # TX: Encode
    # -------------------------
    def encode(
        self,
        dbc_message_name: str,
        signal_map: Dict[str, str],
        state_obj: Any,
    ) -> Tuple[int, bytes, int]:
        msg = self._by_name.get(dbc_message_name)
        if msg is None:
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
            if val is None:
                continue
            sig_values[sig_name] = val

        # Encode; cantools will raise if required signals missing
        data = msg.encode(sig_values)
        dlc = int(msg.length)
        arb_id = int(msg.frame_id)
        return arb_id, data, dlc
