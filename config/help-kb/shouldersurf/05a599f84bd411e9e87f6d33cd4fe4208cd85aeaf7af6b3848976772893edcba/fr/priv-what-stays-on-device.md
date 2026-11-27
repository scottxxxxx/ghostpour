---
id: priv-what-stays-on-device
title: Ce qui reste sur votre téléphone
category: Privacy
keywords: [confidentialité, sur l'appareil, local, audio, enregistrements, transcription, id vocale, calendrier, clés d'api, contacts, photos, hors ligne]
screen: settings/privacy
---

L'essentiel de ce que fait Shoulder Surf se passe sur le téléphone, et une grande partie n'en sort jamais.

**Transcription.** La parole est convertie en texte par le moteur vocal d'Apple, sur l'appareil. Aucun audio n'est envoyé nulle part pour être transcrit, et ça fonctionne sans réseau.

**Séparation des intervenants et ID vocale.** Distinguer les voix, et reconnaître les voix enregistrées, se fait sur le téléphone avec des modèles sur l'appareil. L'audio de votre enregistrement vocal et les empreintes vocales restent sur l'appareil.

**Enregistrements audio.** Conservés sur le téléphone avec la réunion. L'app n'envoie jamais d'elle-même l'audio aux serveurs de Shoulder Surf. Les deux façons dont l'audio quitte le téléphone dépendent toutes deux de vous : votre propre iCloud (Synchronisation iCloud, activée par défaut, et Sauvegarde iCloud Drive, désactivée par défaut), qu'Apple chiffre et que Shoulder Surf ne peut pas lire ; et un partage que vous créez avec "Inclure l'audio" activé.

**Photos.** Conservées avec la réunion sur le téléphone. Une photo n'est envoyée à une IA que si vous la joignez à une question, et uniquement au modèle que vous avez choisi.

**Calendrier.** Lu sur l'appareil pour préparer les réunions. Les détails des événements ne sont jamais envoyés.

**Contacts.** Seulement si vous l'autorisez ("Ajouter les photos de Contacts" dans Personnes). L'app lit Contacts sur le téléphone pour retrouver les personnes de vos réunions, et ne garde que la photo, le téléphone, l'e-mail et l'entreprise de la personne retrouvée. Ces informations restent sur vos appareils et dans votre iCloud privé quand la synchronisation est activée ; rien de tout cela ne part sur les serveurs de Shoulder Surf. Sans cet accès, associer une personne passe par le sélecteur d'Apple et seule la fiche que vous choisissez est conservée.

**Clés d'API.** Dans le trousseau de l'iPhone, jamais synchronisées, jamais envoyées à Shoulder Surf.

**IA sur l'appareil.** Avec Sur l'appareil (Apple) sélectionné, les questions, les résumés et le Chat de réunion tournent sur le téléphone ; rien de la réunion ne quitte l'appareil pour ces fonctions.

**Les statistiques d'utilisation et les rapports de plantage** sont l'exception à "ça reste sur le téléphone" et sont décrits dans "Ce qui part sur le serveur, et quand". Ils ne contiennent jamais de transcriptions, d'enregistrements ni de noms.

Personne chez Shoulder Surf ne peut voir vos enregistrements. Aucun chemin dans l'app ne nous envoie d'audio, à part un lien de partage que vous créez.
