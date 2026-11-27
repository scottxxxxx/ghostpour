---
id: ref-references
title: References: documents the AI reads with your question
category: Prompt Library and References
keywords: [references, reference documents, attach, pdf, docx, pptx, xlsx, txt, markdown, paste text, keep original formatting, add as text, status report format, sow, faq, sync]
screen: settings/prompts
---

References are documents you keep in the app so the AI can read them alongside a question: a status report format to follow, a statement of work to check against, an FAQ, a brief.

**Where.** Settings > Prompts & References > References. Three ways to add one: **Write or Paste Text**, **Add as Text** (text and Markdown go as written; PDF, Word and RTF are turned into editable text), or **Keep Original Formatting** (the file itself is stored, for formats the server can read natively).

**Using one.** In Project Chat and Meeting Chat, + > References & Prompts, tick the ones you want, and they go with that conversation until you remove them. In the live session's Query sheet there's a References section too; a reference attached there goes with every question in that meeting. A Quick Prompt can also carry a reference every time it runs (edit the prompt > References). A project-scoped reference ("Only these projects") attaches itself automatically to every question in those projects; "All projects" means offered everywhere, never automatic.

**Who reads the original file.** On Shoulder Surf AI with a Plus or Pro plan, original PDFs are read page by page (up to a few hundred pages), and Word, PowerPoint and Excel originals are read on the server. On other models, below that plan, or for plain-text formats, the extracted text is what goes with the question. On your own key with the Extras pack, original PDFs go straight to Claude, Gemini, OpenAI or OpenRouter models. A reference that can't ride on the current model says so in the picker ("Shoulder Surf AI only") rather than silently degrading.

**Files the AI made** (from Project Chat or Meeting Chat) land in References automatically, so you can attach them back later.

**Across devices.** References sync through your private iCloud when iCloud Sync is on.

Keynote, Pages and Numbers files aren't readable; export to PDF first. A scanned PDF with no text layer is fine on Shoulder Surf AI (it's read visually) and empty elsewhere.
