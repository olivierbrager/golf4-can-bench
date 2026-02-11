# Golf4 CAN Bench History

Dernière mise à jour: 2026-02-11

## 2026-02-11

### Street UI (compteurs)
- Refonte Street vers un cluster double cadran (gauche RPM, droite vitesse).
- Cadran droit aligné sur une échelle non-linéaire 0..300 km/h:
  - 100 km/h au vertical.
  - Segmentation 0..100 puis 100..300.
- Remplacement de l’ancien affichage speed-ring/cards par une graduation type combiné (labels + ticks dynamiques).
- Aiguilles rouges harmonisées (même design triangulaire sur les deux cadrans).
- Base des aiguilles déplacée vers l’extérieur (`translate(112%, -50%)`).
- Valeur numérique centrée dans les deux centres de cadran, sans `km/h` au centre du cadran vitesse.
- Ajustements successifs des couronnes (diamètre/épaisseur) pour stabiliser le rendu visuel retenu.
- Ajout d’une aiguille complémentaire grise (même design) sur les deux cadrans:
  - Représente le maximum glissant.
  - Fenêtre: 1 seconde.
  - Non affichée si max ~= valeur courante (seuils RPM/Speed).
  - Style flou semi-transparent.
- Adoucissement global du rendu (micro blur sur anneaux/ticks + centre radial des compteurs).
- Style typo centrale (`.dial-gear`) ajusté vers un gris clair embossé.

### Documentation
- Création de la spec dédiée Street: `docs/street-counters-spec.md`.
- README enrichi avec le lien vers la spec Street.

## 2026-02-10

### Base projet / infra
- SocketCAN / gs_usb validés.
- Loopback CAN (`vcan0`) validé.
- DBC étendu `dbc/golf4_ext.dbc` (8 messages).

### Documentation / exploitation
- README aligné avec `/opt/golf4-can-bench`.
- Installation via `requirements.txt`.
- Variables d’environnement liveview listées.
- Section systemd clarifiée (`systemd/` + création `can-tx.service`).

### Debug UI
- Tableau 2 colonnes (`Raw (DBC decode)` vs `Signals/Compat/Dev/Meta`).
- Suppression des sections repliables.
- Lisibilité augmentée (titres, colonnes compactées).
- Cache busting assets (`debug.css`, `app.js`).

### État validé
- `vcan` loopback OK.
- `can-tx` au boot OK.
- `liveview` au boot OK.
- Vérifications `candump` OK.

## Backlog
- Mode `--no-can / --sim`.
- Enrichissement DBC.
- Tests automatisés / CI.
