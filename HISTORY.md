# Golf4 CAN Bench

## Contexte
Ce projet émule un ECU et fournit une vue live CAN.
Dernière mise à jour: 2026-02-10

## Ce qui a été fait jusqu’ici

### Découverte
- SocketCAN / gs_usb
- Loopback CAN (`vcan0`)
- DBC étendu `golf4_ext.dbc`

### Documentation
- README aligné sur le chemin `/opt/golf4-can-bench`
- Installation via `requirements.txt`
- Variables d’environnement liveview listées
- Section systemd clarifiée (exemples `systemd/` + `can-tx.service` à créer)

### UI Debug
- Tableau en 2 colonnes: gauche = `Raw (DBC decode)`, droite = `Signals/Compat/Dev/Meta`
- Suppression des sections repliables (pas de collapse)
- Titres de catégories plus visibles (taille + gras + couleur claire)
- Colonnes compactées (Age moins large)
- Cache busting des assets (`debug.css` / `app.js`)

### Shell styling
- Starship prompt personnalisé (Tokyo Night)
- VS Code SSH / Codex ready

### Services systemd
- can0.service (vcan)
- can-tx.service
- liveview.service

### Etats validés
- vcan loopback fonctionne
- can-tx tourne au boot
- liveview tourne au boot
- Tests via `candump` OK

## Besoins futurs
- Mode `--no-can / --sim`
- Enrichissement DBC
- Debug UI amélioré
- Tests et CI
