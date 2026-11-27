---
id: ref-references
title: Références : les documents que l'IA lit avec votre question
category: Prompt Library and References
keywords: [références, documents de référence, joindre, pdf, docx, pptx, xlsx, txt, markdown, coller du texte, conserver la mise en forme, ajouter en texte, modèle de rapport d'avancement, cahier des charges, faq, synchronisation]
screen: settings/prompts
---

Les références sont des documents que vous gardez dans l'app pour que l'IA puisse les lire avec une question : un modèle de rapport d'avancement à suivre, un cahier des charges à vérifier, une FAQ, un brief.

**Où.** Réglages > Instructions et références > Références. Trois façons d'en ajouter une : **Écrire ou coller du texte**, **Ajouter en texte** (le texte et le Markdown sont gardés tels quels ; les PDF, Word et RTF sont convertis en texte modifiable), ou **Conserver la mise en forme** (le fichier lui-même est stocké, pour les formats que le serveur sait lire directement).

**En utiliser une.** Dans le Chat de projet et le Chat de réunion, + > Références et instructions, cochez celles que vous voulez, et elles accompagnent cette conversation jusqu'à ce que vous les retiriez. Dans la feuille Requête de la session en direct, il y a aussi une section Références ; une référence jointe là accompagne chaque question de cette réunion. Une instruction rapide peut aussi emporter une référence à chaque fois qu'elle s'exécute (modifiez l'instruction > Références). Une référence limitée à certains projets ("Uniquement ces projets") se joint automatiquement à chaque question dans ces projets ; "Tous les projets" veut dire qu'elle est proposée partout, jamais ajoutée automatiquement.

**Qui lit le fichier original.** Avec Shoulder Surf AI et une formule Plus ou Pro, les PDF originaux sont lus page par page (jusqu'à quelques centaines de pages), et les originaux Word, PowerPoint et Excel sont lus sur le serveur. Avec les autres modèles, en dessous de cette formule, ou pour les formats en texte brut, c'est le texte extrait qui accompagne la question. Avec votre propre clé et le pack Extras, les PDF originaux sont envoyés directement aux modèles Claude, Gemini, OpenAI ou OpenRouter. Une référence qui ne peut pas être utilisée avec le modèle actuel le signale dans le sélecteur ("Shoulder Surf AI uniquement") au lieu de se dégrader en silence.

**Les fichiers créés par l'IA** (depuis le Chat de projet ou le Chat de réunion) arrivent automatiquement dans Références, pour que vous puissiez les joindre de nouveau plus tard.

**Sur tous vos appareils.** Les références se synchronisent via votre iCloud privé quand la Synchronisation iCloud est activée.

Les fichiers Keynote, Pages et Numbers ne sont pas lisibles ; exportez-les d'abord en PDF. Un PDF scanné sans couche de texte passe très bien avec Shoulder Surf AI (il est lu visuellement) et arrive vide ailleurs.
