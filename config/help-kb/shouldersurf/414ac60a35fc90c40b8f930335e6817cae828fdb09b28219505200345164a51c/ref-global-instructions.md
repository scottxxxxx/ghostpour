---
id: ref-global-instructions
title: Global instructions and prompt editing
category: Prompt Library and References
keywords: [global system instructions, instructions, rules, always answer in bullets, managed, own key, prompt editing, summary prompts, analysis prompt, context slots, advanced]
screen: settings/prompts
---

**Global System Instructions** are your standing rules for the AI: "answer in bullets," "never speculate," "I work in pharma, assume that context." Settings > Prompts & References > Quick Prompts > Advanced. They're added to every live session prompt, and to Meeting Chat and Project Chat.

**They only apply on your own key.** On Shoulder Surf AI, Apple Cloud and Apple On-Device the instructions are managed by Shoulder Surf, and the footer says so: anything you save there is kept but not used until you switch to a model with your own API key. The reason: on those models the prompt recipe is maintained server-side and tuned per model, and your rules would fight it.

**Quick Prompt text is yours on every model.** You can edit, add, reorder and delete Quick Prompts on Shoulder Surf AI too. See "Quick Prompts."

**Background prompts** (Full Summary, Delta Summary, Final Consolidation, Post-Session Analysis) live under Settings > Meetings > Summaries > Edit summary prompts. Editable on your own key; managed on Shoulder Surf AI ("Optimized for Shoulder Surf AI"). Switching to Shoulder Surf AI resets custom prompts to the managed defaults.

**Context slots.** What goes with a question: the transcript, the rolling summary, project info, conversation history, images, Context Quilt. Each Quick Prompt's Advanced section can override these per prompt (Default / On / Off). The global defaults are what the prompt inherits.

**Fetch Latest Default Prompts** pulls the current managed wording from the server. If you've customized the built-in prompts, they don't update on their own; this button, or "Reset to Defaults," is how you get the new ones.
