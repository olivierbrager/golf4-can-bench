# CODEX.md

## 1) Contexte projet
Golf 4 CAN Bench: émulateur ECU (TX) + liveview (RX) avec UI web. Loopback OK, can-tx OK, liveview OK en Debug. DBC cible: `dbc/golf4_ext.dbc`.

## 2) Priorité absolue: stabilité Dev/Debug + invariants + zéro régression UI
Invariants concrets:
- Démarrage Dev/Debug sans erreur pour TX et liveview.
- Flux RX/WS stable (pas de crash, pas de reconnexion en boucle).
- UI inchangée: pas de régression visuelle ou fonctionnelle.
- DBC compatible: décodage stable, pas de champs cassés.

## 3) Règles d’utilisation de Codex (strict)
- Changements atomiques.
- 1 tâche = 1 commit.
- Toujours reviewer `git diff`.
- Interdire modifications UI/RX/WS sans tests/flags et sans métriques.
- Pas de refactor large sans plan.

## 4) Workflow recommandé (checklist)
- Créer une branche dédiée.
- Exécuter tests/lint.
- Vérifier le diff.
- Committer.
- Si un point est bloqué (tests indisponibles, outils manquants), documenter la raison dans le commit/PR.

## 5) Règle repo: `.vscode/` versionné
Le dossier `.vscode/` est versionné volontairement pour standardiser l’expérience VS Code et réduire les écarts de configuration entre contributeurs.

## 6) Exemples de prompts
OK:
- "Ajoute une tâche VS Code pour lancer liveview en Debug, sans toucher au code."
- "Documente les variables d’environnement de liveview dans un fichier de doc, sans modifier l’app."
- "Ajoute un script de test existant au tasks.json si un runner est déjà configuré."

Interdits:
- "Refactorise tout le pipeline RX pour simplifier le code."
- "Change l’UI pour un nouveau layout plus moderne."
- "Optimise le WebSocket sans tests ni métriques."

## 7) Definition of Done (PR touchant RX/WS/DBC)
- Tests/linters passent (ou justification explicite si indisponibles).
- Aucune régression UI constatée.
- Stabilité Dev/Debug vérifiée.
- DBC validé (décodage stable, champs clés intacts).
