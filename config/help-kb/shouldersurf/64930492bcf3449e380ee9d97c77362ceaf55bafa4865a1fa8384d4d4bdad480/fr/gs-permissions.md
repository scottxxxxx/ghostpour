---
id: gs-permissions
title: Les autorisations, et pourquoi chacune est demandée
category: Getting started
keywords: [autorisations, micro, microphone, caméra, reconnaissance vocale, calendrier, rappels, photos, notifications, réseau local, contacts, refusé, réglages]
screen: settings/privacy
---

Shoulder Surf demande quelques autorisations iOS. Une seule est obligatoire. Vous pouvez voir l'état de chacune dans Réglages > Confidentialité > Autorisations.

**Microphone (obligatoire).** C'est une app d'enregistrement. Sans cette autorisation, elle n'entend rien. Si vous l'avez refusée, Réglages iOS > Shoulder Surf > Microphone la réactive.

**Reconnaissance vocale (nécessaire pour transcrire).** iOS la demande pour que le moteur vocal du téléphone puisse transformer l'audio en texte. La transcription se fait sur l'appareil ; cette autorisation n'envoie l'audio nulle part.

**Caméra (facultative).** Pour capturer des diapos et des écrans pendant une réunion, et pour le mode Lire l'écran. Refusez-la et vous gardez quand même l'audio et la transcription.

**Notifications (facultatives).** Le détecteur de silence ("Aucune parole détectée") et les avis "Enregistrement en arrière-plan" en ont besoin.

**Calendrier (facultatif, désactivé tant que vous ne l'activez pas).** Réglages > Réunions > Préparation > "Utiliser mon agenda". L'app lit votre calendrier sur l'appareil pour reconnaître votre prochaine réunion et vous y préparer. Les détails des événements ne quittent jamais l'appareil. Elle vous explique d'abord pourquoi, avec son propre message, pour que vous puissiez répondre "pas maintenant" sans gâcher l'unique demande système qu'iOS accorde.

**Rappels (facultatif).** Seulement quand vous enregistrez des actions à suivre dans l'app Rappels.

**Photos, ajout uniquement (facultatif).** Seulement quand vous choisissez Enregistrer dans Photos sur une image capturée. L'app ne lit jamais votre photothèque par ce biais ; choisir une photo à joindre passe par le sélecteur d'Apple, qui ne demande aucune autorisation.

**Réseau local (facultatif).** Seulement si vous utilisez le compagnon pour Mac ou pour Windows. L'app en a besoin pour trouver votre ordinateur sur le Wi-Fi.

**Contacts (facultatif, demandé seulement si vous le choisissez).** Personnes affiche une carte "Ajouter les photos de Contacts" ; touchez Autoriser et iOS pose la question une fois. L'app lit les Contacts sur le téléphone pour associer les personnes de vos réunions à leurs fiches et copier leurs photos (et, quand vous touchez Récupérer depuis Contacts sur une personne, son téléphone, son e-mail et son entreprise). Dites non, ou touchez "Pas maintenant", et vous pourrez toujours lier une personne à la fois avec Modifier > Choisir dans Contacts, qui utilise le sélecteur d'Apple et ne demande aucune autorisation.

Si quelque chose est "Refusé", la liste des autorisations dans Réglages > Confidentialité l'indique, et la solution passe toujours par Réglages iOS > Shoulder Surf.
