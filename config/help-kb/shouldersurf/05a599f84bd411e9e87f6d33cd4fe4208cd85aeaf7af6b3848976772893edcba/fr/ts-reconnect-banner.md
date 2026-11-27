---
id: ts-reconnect-banner
title: La bannière de reconnexion (connexion perdue avec votre ordinateur)
category: Troubleshooting
keywords: [reconnexion, connexion perdue, compagnon, mac, windows, ordinateur non connecté, enregistrement avec le micro de l'iphone, ignorer, se déconnecte sans arrêt, pas démarré à temps, utiliser le micro de l'iphone]
screen: session
---

Vous enregistrez via le compagnon pour Mac ou pour Windows et une bannière apparaît : "Connexion à votre ordinateur perdue. Enregistrement avec le micro de l'iPhone pendant la reconnexion." Voici ce qui se passe et quoi faire.

**Rien n'est perdu.** Dès que la liaison tombe, le micro du téléphone prend le relais, donc l'enregistrement et la transcription continuent. La bannière a un bouton Ignorer ; il la masque et l'app continue d'essayer.

**La reconnexion se fait toute seule.** L'app réessaie au bout de quelques secondes, puis toutes les 10 secondes. Quand l'ordinateur revient, le mode repasse sur le compagnon et une courte note "Reconnecté à votre ordinateur" s'affiche. Une brève coupure du Wi-Fi se règle généralement d'elle-même en moins de dix secondes. Si l'ordinateur décroche trois fois en cinq minutes, l'app reste sur le micro du téléphone pendant cinq minutes avant de réessayer, et la bannière l'indique.

**Au début d'une réunion,** la règle est différente : le téléphone attend l'ordinateur jusqu'à huit secondes. S'il ne répond pas, l'enregistrement démarre sur le micro du téléphone et une fenêtre vous demande s'il faut continuer ainsi ("Continuer avec le micro de l'iPhone") ou arrêter.

**Autres bannières possibles,** chacune avec un bouton "Utiliser le micro de l'iPhone" : "Pas jumelé avec votre ordinateur", "Autorisez l'enregistrement de l'écran et de l'audio sur votre Mac", "Ordinateur non connecté", "Votre ordinateur n'envoie pas d'audio". Elles veulent toutes dire la même chose : le téléphone enregistre avec son propre micro, et le compagnon demande votre attention côté ordinateur.

**Ce qu'il faut vérifier sur l'ordinateur.** L'icône du compagnon est dans la barre des menus ou la zone de notification et indique Connecté ou Relais. L'enregistrement de l'écran et de l'audio du système et le micro sont autorisés. Le Mac et le téléphone sont sur le même Wi-Fi (pas l'un des deux sur un réseau invité). Sous Windows, le pare-feu a autorisé les réseaux privés.

**Jumeler à nouveau.** Réglages > Audio > Ordinateur > Shoulder Surf Companion affiche l'ordinateur jumelé et ceux présents sur le réseau. "Réinitialiser la clé partagée" et un nouveau code à six chiffres depuis le menu de l'ordinateur refont le jumelage de zéro.
