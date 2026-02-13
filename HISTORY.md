# Golf4 CAN Bench History

Dernière mise à jour: 2026-02-12

## 2026-02-12

### Street UI (warnings / fullscreen)
- Bandeau warnings haut finalisé (forme trapèze à raccords arrondis tangents), position/espacements affinés.
- Bandeau bas ajouté comme miroir géométrique du bandeau haut (sans contenu), avec réglages dédiés normal/fullscreen.
- Icônes warnings Street recentrées, redimensionnées et espacées (normal + fullscreen).
- Suppression d’éléments centraux non retenus (ligne `D?/A3`, pictos auxiliaires, `...` en tête).
- Ajout d’un mode fullscreen Street:
  - URL: `/webui/index.html?fullscreen=street`
  - Shell UI masqué (tabs/titres/cartes), dash seul à l’écran.
  - Bouton de lancement ajouté dans la Liveview: `Street Fullscreen`.
- Conversion des PNG warnings (`static/warning_icons/*.png`) en alpha transparent (fond noir retiré).
- Glow warnings/clignotants ajusté vers un halo de contour plus subtil (sans disque plein).
- Intégration d’une carte GPS dans le rectangle central Street (embed OpenStreetMap):
  - source prioritaire `meta.gps` dans le payload WS si disponible.
  - fallback `navigator.geolocation` côté navigateur.
  - overlay source/lat/lon + statut.
  - option de désactivation via URL: `?map=0`.
  - overlay carte ajusté en fullscreen (lisibilité).
- Ajout d’une source GPS fichier locale pour position temporaire:
  - fichier: `static/gps_position.json`
  - si `enabled=true` et coordonnées valides, cette source est prioritaire pour la carte.
  - fichier initialisé sur le rond-point des Champs-Elysees (Paris).
- Correctif lisibilité carte Street:
  - suppression du clignotement (la carte n’est plus reconstruite à chaque frame WS).
  - ancrage visuel de la carte sur le rectangle central via positionnement DOM persistant.
- Ajustements visuels carte Street:
  - rectangle central légèrement agrandi.
  - rendu carte basculé en style sombre (filtre visuel dark sur l’iframe).
- Carte Street:
  - suppression du cadre d’informations `GPS/LAT/LON`.
  - zoom augmenté (fenêtre de carte resserrée autour de la position).
  - passage en carte statique fortement zoomée (sans contrôles `+/-`).
  - suppression de la mention OpenStreetMap visible dans la carte.
  - correctif lien brisé: remplacement carte statique par Leaflet dynamique (zoom fixe, sans contrôles `+/-`).
  - zoom augmenté (niveau 19).
  - texte de statut remplacé par reverse geocoding (rue, sinon ville/commune).
  - mode simulation trajet ajouté via `static/gps_position.json`:
    - support `points[]` + `tick_s` + `loop`.
    - trajet de démonstration 5 minutes provisionné (300 positions, 1 point/seconde).
  - trajet de simulation recalculé sur réseau routier (routing OSRM, points sur routes).
  - déplacement rendu plus fluide via interpolation entre 2 points successifs.
  - bandeau bas enrichi avec métriques (gauche->droite): température huile, niveau carburant, suralimentation, température eau.

### Emulator
- Nouveau scénario TX `warning_blink`:
  - clignotement continu des warnings principaux pour validation cluster.
  - exposé dans l’UI Scenarios (`Warn blink`) et via API `/api/scenario/warning_blink`.

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
- Ports runtime/documentation harmonisés:
  - Emulator TX: `8001`
  - Liveview RX: `8011`

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
