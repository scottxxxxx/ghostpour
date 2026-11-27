---
id: set-own-keys
title: Utiliser votre propre clé d'API
category: Settings
keywords: [votre propre clé, clé d'api, clé api, byok, openai, anthropic, claude, gemini, grok, deepseek, kimi, qwen, openrouter, tester la clé, trousseau, effort de raisonnement, facturé sur votre clé]
screen: settings/ai-model
---

Si vous payez déjà OpenAI, Anthropic, Google ou un autre fournisseur, vous pouvez brancher Shoulder Surf sur votre propre compte.

**La configurer.** Réglages > Modèle d'IA > choisissez le fournisseur sous Utilisez votre propre clé. Collez la clé dans le champ (l'icône en forme d'œil l'affiche ; "Obtenir une clé API" ouvre la page du fournisseur). Touchez **Tester la clé** pour la vérifier ; "Vérifié" veut dire qu'elle fonctionne. Ouvrez ensuite la ligne Modèle, choisissez un modèle (celui recommandé par défaut est marqué "Meilleur") et touchez Utiliser ce modèle. L'app vous prévient si une clé semble appartenir à un autre fournisseur.

**Où la clé est gardée.** Dans le trousseau de l'iPhone, le même stockage protégé qu'iOS utilise pour les mots de passe. Elle ne quitte jamais l'appareil, n'est pas synchronisée avec vos autres appareils, et ne survit qu'à une sauvegarde chiffrée de l'appareil. Avec un nouveau téléphone, il faut la coller de nouveau.

**Ce que vous payez.** Chaque question part directement du téléphone vers le fournisseur et est facturée sur votre clé ; Shoulder Surf ne la voit jamais. Tester la clé coûte une fraction de centime chez les fournisseurs de type OpenAI et rien chez Anthropic.

**Raisonnement.** Pour les modèles qui le prennent en charge, un sélecteur Effort de raisonnement (ou un interrupteur Activé/Désactivé pour Kimi, DeepSeek et Qwen) apparaît. Plus l'effort est élevé, plus le modèle réfléchit longtemps et plus il répond lentement. Enregistré pour chaque modèle.

**Ce qui fonctionne d'emblée :** les questions en direct, une photo par question sur les modèles qui lisent les images, les résumés continus à l'intervalle que vous choisissez, le Chat de réunion, vos propres instructions globales et les prompts d'arrière-plan modifiables.

**Avec le pack Extras : votre propre clé** (achat unique, Réglages > Modèle d'IA) : le Chat de projet avec votre clé, plusieurs photos par question, les PDF originaux envoyés aux modèles Claude, Gemini, OpenAI et OpenRouter, la recherche web via les outils de recherche d'Anthropic et d'OpenRouter, l'analyse après la réunion et les réponses en continu.

**Ce qui reste sur Shoulder Surf AI :** les rapports, la traduction, la Mémoire et les Personnes, la création de fichiers. Tout ça tourne sur le serveur et nécessite un compte connecté ; ces fonctions utilisent le quota mensuel de votre compte même quand vos questions passent par votre propre clé.
