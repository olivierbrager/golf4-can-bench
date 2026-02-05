# DEBUG.md

## Activer le mode Debug
Définir la variable d’environnement `DEBUG=1` (ou `true`/`yes`).
Exemples:
- `DEBUG=1 python -m uvicorn liveview:app --host 0.0.0.0 --port 8010`
- `DEBUG=1 uvicorn can_tx_emulator:app --host 0.0.0.0 --port 8000`

Si `DEBUG` est absent ou à `0`, les métriques sont désactivées et les logs sont minimisés.

## Logs structurés (JSON)
Format commun:
- `component`, `event`, `level`
- Champs optionnels selon contexte: `can_id`, `dlc`, `bus`, `ws_clients`, `queue_len`, `dropped`, `err`

Exemples d’événements:
- `can_reader.bus_open`
- `can_reader.decode_unknown_id`
- `can_reader.rx_error`
- `liveview.ws_connected`
- `liveview.ws_send_error`

## Métriques (mémoire, Debug uniquement)
Les métriques sont maintenues en mémoire et exposées dans `GET /metrics` sous `debug_metrics`.

- `rx_frames_total`
- `rx_frames_per_sec` (moyenne glissante sur ~5s)
- `ws_clients_connected`
- `ws_updates_sent_total`
- `ws_updates_dropped_total`
- `decode_unknown_id_total`

Interprétation rapide:
- `rx_frames_per_sec` permet de vérifier le débit RX sans instrumenter la UI.
- `ws_updates_dropped_total` doit rester proche de 0 en situation nominale.
