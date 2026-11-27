---
id: ref-global-instructions
title: Instructions globales et modification des prompts
category: Prompt Library and References
keywords: [instructions système globales, instructions, règles, toujours répondre en puces, géré, votre propre clé, modifier les prompts, instructions de résumé, instruction d'analyse, emplacements de contexte, avancé]
screen: settings/prompts
---

Les **Instructions système globales** sont vos règles permanentes pour l'IA : "réponds en puces", "ne spécule jamais", "je travaille dans l'industrie pharmaceutique, pars de ce contexte". Réglages > Instructions et références > Instructions rapides > Avancé. Elles sont ajoutées à chaque prompt de la session en direct, ainsi qu'au Chat de réunion et au Chat de projet.

**Elles ne s'appliquent qu'avec votre propre clé.** Avec Shoulder Surf AI, Apple Cloud et Sur l'appareil (Apple), les instructions sont gérées par Shoulder Surf, et le bas de la page l'indique : ce que vous y enregistrez est conservé mais pas utilisé tant que vous ne passez pas à un modèle avec votre propre clé d'API. La raison : sur ces modèles, la recette du prompt est maintenue côté serveur et ajustée pour chaque modèle, et vos règles entreraient en conflit avec elle.

**Le texte des instructions rapides vous appartient sur tous les modèles.** Vous pouvez modifier, ajouter, réordonner et supprimer des instructions rapides avec Shoulder Surf AI aussi. Consultez "Instructions rapides : un toucher, une réponse."

**Les prompts d'arrière-plan** (Résumé complet, Résumé delta, Consolidation finale, Analyse après session) se trouvent dans Réglages > Réunions > Résumés > Modifier les instructions de résumé. Ils sont modifiables avec votre propre clé et gérés avec Shoulder Surf AI ("Optimisé pour Shoulder Surf AI"). Passer à Shoulder Surf AI remet les prompts personnalisés aux valeurs gérées par défaut.

**Les emplacements de contexte.** Ce qui accompagne une question : la transcription, le résumé continu, les infos du projet, l'historique de la conversation, les images, Context Quilt. La section Avancé de chaque instruction rapide peut les remplacer pour cette instruction (Par défaut / Activé / Désactivé). Les valeurs globales par défaut sont celles dont l'instruction hérite.

**Récupérer les dernières instructions par défaut** télécharge depuis le serveur la formulation gérée actuelle. Si vous avez personnalisé les instructions intégrées, elles ne se mettent pas à jour toutes seules ; ce bouton, ou "Réinitialiser les valeurs par défaut", est le moyen d'obtenir les nouvelles.
