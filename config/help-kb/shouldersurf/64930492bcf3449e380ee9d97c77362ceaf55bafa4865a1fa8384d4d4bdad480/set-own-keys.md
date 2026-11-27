---
id: set-own-keys
title: Using your own API key
category: Settings
keywords: [own key, api key, byok, openai, anthropic, claude, gemini, grok, deepseek, kimi, qwen, openrouter, test key, keychain, reasoning effort, billed to your key]
screen: settings/ai-model
---

If you already pay OpenAI, Anthropic, Google or another provider, you can point Shoulder Surf at your own account.

**Set it up.** Settings > AI Model > pick the provider under Bring Your Own Key. Paste the key into the field (the eye toggle shows it; "Get API Key" opens the provider's page). Tap **Test Key** to check it; "Verified" means it works. Then open the Model row, pick a model (the served default is marked "Best"), and tap Use This Model. The app warns if a key looks like it belongs to a different provider.

**Where the key is kept.** In the iPhone's Keychain, the same protected storage iOS uses for passwords. It never leaves the device, it's not synced to your other devices, and it survives only an encrypted device backup. A new phone means pasting it again.

**What you pay.** Each question goes straight from the phone to the provider and is billed to your key; Shoulder Surf never sees it. Test Key costs a fraction of a cent on OpenAI-style providers and nothing on Anthropic.

**Reasoning.** For models that support it, a Reasoning Effort picker (or an On/Off switch for Kimi, DeepSeek and Qwen) appears. Higher effort thinks longer and answers slower. Saved per model.

**What works out of the box:** live questions, one photo per question on models that read images, rolling summaries at the interval you choose, Meeting Chat, your own global instructions and editable background prompts.

**With the Own API Key Extras pack** (one-time purchase, Settings > AI Model): Project Chat on your key, more than one photo per question, original PDFs sent to Claude, Gemini, OpenAI and OpenRouter models, web search through Anthropic's and OpenRouter's own search tools, post-meeting analysis, and streamed answers.

**What stays on Shoulder Surf AI:** reports, translation, Memory and People, file generation. Those run on the server and need a signed-in account; they use your account's monthly allowance even while your questions go to your own key.
