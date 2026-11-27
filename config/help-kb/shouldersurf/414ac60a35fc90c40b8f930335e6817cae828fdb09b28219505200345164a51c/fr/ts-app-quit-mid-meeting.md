---
id: ts-app-quit-mid-meeting
title: L'app s'est fermée pendant une réunion
category: Troubleshooting
keywords: [plantage, fermeture, app fermée, réunion interrompue, reprendre, sauvegarde automatique, enregistrement perdu, récupéré, session en danger, forcer à quitter]
screen: session
---

iOS a fermé l'app, ou elle a planté, pendant une réunion. Voici ce que vous allez trouver.

**Une carte "Réunion interrompue"** dans l'onglet Copilot, avec un bouton Reprendre. L'app sauvegarde automatiquement la réunion en cours toutes les 30 secondes (et immédiatement quand elle passe en arrière-plan), donc la transcription, les réponses, le résumé et l'audio jusqu'à la dernière sauvegarde sont là. Reprendre relance la réunion là où elle s'était arrêtée ; Fin enregistre ce qui a été capté.

**Si vous ne voyez pas la carte,** regardez dans Révision : une réunion qui était en train de se terminer quand l'app s'est fermée est enregistrée sous son nom d'origine au redémarrage de l'app.

**Ce qui peut manquer :** jusqu'à 30 secondes de transcription avant la fermeture, et la dernière photo si elle n'avait pas encore été écrite. Le fichier audio est écrit en continu, donc l'enregistrement va généralement jusqu'au moment exact où il s'est arrêté.

**La notification "Session en danger".** iOS était sur le point de couper le temps d'exécution en arrière-plan de l'app. Revenez dans l'app pour continuer l'enregistrement ; s'il était trop tard, la sauvegarde automatique décrite plus haut est ce que vous avez.

**Pourquoi ça arrive.** Les très longues réunions avec la caméra allumée et un téléphone qui chauffe sont la cause habituelle, et iOS est strict sur la mémoire. Éteindre la caméra quand vous n'avez pas besoin des diapos aide beaucoup. L'app éteint aussi la caméra d'elle-même quand le téléphone chauffe.

**Aidez-nous à corriger le problème.** Si l'app a planté, elle nous envoie d'elle-même le rapport de plantage d'Apple à sa prochaine ouverture, sans rien de vos réunions. Pour nous dire ce que vous faisiez à ce moment-là, écrivez à support@shouldersurf.com.
