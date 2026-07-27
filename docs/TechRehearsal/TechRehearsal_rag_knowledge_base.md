# Shoulder Surf: RAG Knowledge Base for Conversation Rehearsal and Evaluation

**Purpose:** A practical source and implementation guide for building retrieval-augmented generation (RAG) into an iOS application that helps users rehearse difficult conversations and evaluate completed conversations.

**Prepared for:** Shoulder Surf product, design, iOS, backend, AI, and evaluation teams  
**Last reviewed:** July 2026

---

## 1. Executive Summary

Shoulder Surf can use RAG to make mock conversations more realistic, scenario-specific, and grounded in established communication practices. The same knowledge base can also support post-conversation evaluation by identifying observable behaviors in a transcript and explaining how those behaviors likely helped or hindered the conversation.

The recommended design is not a single large vector database of communication books. It is a modular system with:

1. **Stable communication principles** from authoritative or open-access sources.
2. **Original Shoulder Surf playbooks** that convert those principles into structured, product-specific guidance.
3. **Scenario facts** supplied by the user or retrieved from current external sources when facts can change.
4. **Annotated example conversations** showing effective and ineffective behaviors.
5. **Separate retrieval paths** for the simulated counterpart, the user coach, and the transcript evaluator.

The most useful initial namespaces are:

- `universal_conversation_skills`
- `difficult_personal_conversations`
- `child_and_family_conversations`
- `relationship_repair`
- `negotiation_general`
- `salary_negotiation`
- `large_purchase_negotiation`
- `proposal_and_persuasion`
- `presentation_delivery`
- `safety_and_escalation`

---

## 2. Product Use Cases

### 2.1 Before the conversation: preparation

The app should help the user:

- clarify the intended outcome;
- identify the other person's likely goals, concerns, and emotional state;
- choose an appropriate opening;
- anticipate objections and difficult questions;
- avoid language likely to create defensiveness;
- rehearse several realistic conversation paths;
- plan boundaries, concessions, or next steps;
- distinguish known facts from assumptions;
- prepare for emotional reactions without scripting or controlling the other person.

### 2.2 During rehearsal: simulated counterpart

The simulated counterpart should:

- behave consistently with the assigned persona and scenario;
- ask realistic questions;
- show plausible resistance rather than immediate agreement;
- react to the user's actual wording;
- avoid exaggerated distress or hostility unless the scenario calls for it;
- preserve uncertainty and knowledge boundaries;
- change position only when the user's behavior or evidence reasonably warrants it.

### 2.3 After rehearsal: coaching

The app should provide:

- a concise summary of what happened;
- specific strengths tied to transcript evidence;
- one to three high-impact improvements;
- stronger alternative wording;
- missed opportunities;
- a recommended next practice round;
- a confidence-calibrated explanation rather than a clinical judgment.

### 2.4 After a real conversation: evaluation

The evaluator should assess observable communication behavior, not diagnose either participant. It can evaluate:

- clarity of the ask or message;
- listening and reflection;
- emotional acknowledgment;
- interruption and turn-taking;
- defensiveness;
- use of questions;
- quality of apology or repair;
- handling of objections;
- negotiation preparation and discipline;
- whether commitments and next steps were confirmed.

The evaluator should say **“the transcript suggests”** rather than asserting hidden motives or psychological states.

---

## 3. Recommended Source Library

> **Important:** A source being publicly accessible does not automatically permit full-text ingestion into a commercial RAG system. Review copyright, license, terms of use, and redistribution restrictions before ingestion. Government works and clearly open-access research are often easier to use, but each source still requires review.

| Category | Source | How It Can Help Shoulder Surf | URL | Suggested Use |
|---|---|---|---|---|
| Universal conversation evaluation | Motivational Interviewing Treatment Integrity Code 4.2.1 (MITI) | Defines observable behaviors such as empathy, partnership, persuasion, questions, reflections, affirmations, and support for autonomy. Useful for building transcript rubrics without relying only on a vague “empathy” score. | https://casaa.unm.edu/assets/docs/miti4_21.pdf | Adapt selected behavioral dimensions into original nonclinical scoring cards. |
| Universal conversation evaluation | MITI 4 research article | Explains the development and psychometric reasoning behind MITI 4. Helpful for understanding what the coding dimensions do and do not measure. | https://pmc.ncbi.nlm.nih.gov/articles/PMC5539964/ | Research reference; avoid copying the instrument without legal review. |
| Difficult conversations | Conflict Management: Difficult Conversations with Difficult People | Open-access review of conflict resolution and difficult-conversation skills, including preparation, listening, and managing disagreement. | https://pmc.ncbi.nlm.nih.gov/articles/PMC3835442/ | Source for original conflict-preparation and de-escalation cards. |
| Emotional communication | CDC Crisis and Emergency Risk Communication Manual | Covers empathy, trust, uncertainty, message construction, audience needs, and communicating under stress. Although designed for public emergencies, many principles transfer to personal disclosures. | https://www.cdc.gov/cerc/php/cerc-manual/index.html | Use selected principles; do not apply emergency-specific advice mechanically to private relationships. |
| Emotional communication | CERC: Psychology of a Crisis | Explains how fear, uncertainty, helplessness, and information-processing constraints affect communication. | https://stacks.cdc.gov/view/cdc/60292 | Build cards about pacing, uncertainty, cognitive load, and emotional acknowledgment. |
| Emotional communication | CERC: Messages and Audiences | Covers message clarity, audience adaptation, empathy, and acknowledging pain and uncertainty. | https://www.cdc.gov/cerc/media/pdfs/CERC_Messages_and_Audiences.pdf | Useful for opening statements and message-clarity rubrics. |
| Trauma-sensitive communication | SAMHSA’s Concept of Trauma and Guidance for a Trauma-Informed Approach | Defines safety, trustworthiness, peer support, collaboration, empowerment, and cultural considerations. | https://library.samhsa.gov/sites/default/files/sma14-4884.pdf | Translate principles into nonclinical communication safeguards. |
| Trauma-sensitive communication | SAMHSA Trauma-Informed Approaches and Programs | Current overview of trauma-informed principles and organizational practices. | https://www.samhsa.gov/mental-health/trauma-violence/trauma-informed-approaches-programs | Reference for safety language and escalation design. |
| Trauma-sensitive communication | SAMHSA Six Guiding Principles Infographic | Concise summary of the six trauma-informed principles. | https://www.samhsa.gov/resource/dbhis/infographic-6-guiding-principles-trauma-informed-approach | Useful as a compact source for structured cards. |
| Clear communication | Federal Plain Language Guidelines | Guidance on audience-centered writing, organization, word choice, sentence construction, and clarity. | https://www.plainlanguage.gov/ | Build clarity, jargon, structure, and cognitive-load metrics. |
| Active listening | Active listening research and conceptual review | Open-access research can support behaviors such as clarification, reflection, summarization, and demonstrating attention. | https://pmc.ncbi.nlm.nih.gov/articles/PMC4844478/ | Use as background for an original active-listening rubric. |
| Child and family conversations | When a Pet Dies: How to Help Your Child Cope | Age-oriented guidance for explaining pet illness and death honestly and supportively. | https://www.healthychildren.org/English/healthy-living/emotional-wellness/Building-Resilience/Pages/when-a-pet-dies-how-to-help-your-child-cope.aspx | Use as a reviewed reference; confirm reuse rights before ingestion. |
| Child and family conversations | How Children Understand Death: What to Say When a Loved One Dies | Describes age-related understanding of death and age-appropriate explanations. | https://www.healthychildren.org/English/healthy-living/emotional-wellness/Building-Resilience/Pages/How-Children-Understand-Death-What-You-Should-Say.aspx | Build age-band scenario cards; review rights before ingestion. |
| Child and family conversations | Talking With Children About Tragedies and Other Traumatic News Events | General guidance for presenting difficult facts in a way a child can understand and process. | https://www.healthychildren.org/English/family-life/Media/Pages/Talking-To-Children-About-Tragedies-and-Other-News-Events.aspx | Use as a secondary expert reference, not as a substitute for pediatric advice. |
| Child and family conversations | Spanish-language AAP guidance on talking about tragedies | Spanish-language source for bilingual scenario development and terminology review. | https://www.healthychildren.org/spanish/family-life/Media/paginas/Talking-To-Children-About-Tragedies-and-Other-News-Events.aspx | Useful for Spanish-language QA and terminology alignment. |
| Relationship repair | The Role of Apology and Restitution in Forgiveness | Research examining how apology and corrective action relate to empathy, forgiveness, and negative emotion. | https://pmc.ncbi.nlm.nih.gov/articles/PMC7082420/ | Build repair cards that distinguish apology from restitution. |
| Relationship repair | Apology — Internet Encyclopedia of Philosophy | Detailed discussion of apology, acknowledgment, remorse, responsibility, and interpersonal repair. | https://iep.utm.edu/apology/ | Use as conceptual background for original content. |
| Relationship repair | Gallaudet University: How to Write Apologies | Simple practical guidance for acknowledging a mistake and describing corrective action. | https://gallaudet.edu/student-success/tutorial-center/english-center/writing/how-to-write-letters/how-to-write-apologies/ | Useful for concise example patterns; review reuse terms. |
| Negotiation | Harvard Program on Negotiation: Principled Negotiation | Covers interests versus positions, objective criteria, option creation, BATNA, and walking away when an agreement is inferior to the alternative. | https://www.pon.harvard.edu/daily/negotiation-skills-daily/principled-negotiation-focus-interests-create-value/ | Use as a reference; create original playbooks rather than copying articles. |
| Negotiation | Harvard Program on Negotiation: BATNA resources | Collection of articles on alternatives, leverage, disclosure, and preparation. | https://www.pon.harvard.edu/tag/batna/ | Build BATNA, reservation-point, and walk-away preparation cards. |
| Negotiation | Harvard Program on Negotiation: Using Principled Negotiation to Resolve Disagreements | Focuses on objective criteria and underlying interests during disagreement. | https://www.pon.harvard.edu/daily/dispute-resolution/principled-negotiation-resolve-disagreements/ | Useful for dispute-resolution scenarios. |
| Salary negotiation | U.S. Department of Labor Salary Negotiation Participant Guide (2026) | Covers salary research, preparation, value articulation, negotiating an offer, and communicating a decision. | https://www.dol.gov/sites/dolgov/files/VETS/files/SalaryNegotiation_PG_Interactive_Feb2026.pdf | Strong source for salary-specific preparation cards and fixtures. |
| Salary negotiation | U.S. Department of Labor Salary Negotiations Guide | Earlier detailed participant guide with exercises and negotiation examples. | https://www.dol.gov/sites/dolgov/files/VETS/files/OBTT-PG-SalaryNegotiations-JAN2022.pdf | Supplementary source; prefer the latest edition when content conflicts. |
| Salary negotiation | U.S. Bureau of Labor Statistics | Current occupational wage and employment data. | https://www.bls.gov/ | Retrieve live or periodically refreshed data; do not treat static embeddings as current salary truth. |
| Large purchase negotiation | FTC: Financing or Leasing a Car | Explains financing, total cost, preapproval, contract review, and add-ons. | https://consumer.ftc.gov/articles/financing-or-leasing-car | Build car-purchase preparation and objection simulations. |
| Large purchase negotiation | FTC: Buying a Used Car From a Dealer | Covers the Buyers Guide, warranties, inspections, financing, and add-ons. | https://consumer.ftc.gov/articles/buying-used-car-dealer | Scenario-specific factual grounding. |
| Large purchase negotiation | FTC: Dealer Ads and Promotions — Know Before You Go | Recommends obtaining the out-the-door price in writing and checking availability and financing before visiting. | https://consumer.ftc.gov/articles/car-dealer-ads-and-promotions-know-you-go | Build “ask for written terms” and hidden-fee evaluation cards. |
| Large purchase negotiation | FTC: Understanding Car Add-ons | Explains optional dealer products and how they can affect total cost. | https://consumer.ftc.gov/media/79917 | Use in car negotiation fixtures and fact checks. |
| Proposal and persuasion | Toastmasters Paths and Core Competencies | Maps competencies involving communication, persuasive influence, leadership, planning, and presentation. | https://content.toastmasters.org/image/upload/8077-paths-and-core-competencies.pdf | Use as a taxonomy reference; review copyright before ingestion. |
| Proposal and persuasion | Toastmasters: Present a Proposal | Practical proposal-structure and delivery guidance. | https://mandurahtoastmasters.com.au/wp-content/uploads/2022/07/pw_desc_present_a_proposal.pdf | Reference only unless rights are confirmed. |
| Proposal and persuasion | Toastmasters: Give a Sales Pitch With Purpose | Practical guidance on building a focused, purposeful sales pitch. | https://www.toastmasters.org/magazine/magazine-issues/2021/may/sales-pitch-with-purpose | Create original sales-pitch cards; do not reproduce full article text. |
| Proposal and persuasion | Toastmasters: My Point Is This | Guidance on identifying and reinforcing the main point of a presentation. | https://www.toastmasters.org/magazine/magazine-issues/2022/feb/my-point-is | Useful for “main point” and “explicit ask” evaluation dimensions. |
| Presentation delivery | Toastmasters: Are You Presenting or Performing? | Discusses audience connection, purpose, eye contact, pausing, delivery, and conviction. | https://www.toastmasters.org/magazine/magazine-issues/2022/nov/performing-or-presenting | Reference for presentation-delivery rubric design. |

---

## 4. Commercial Books and Proprietary Frameworks

The following materials are highly relevant but should **not** be ingested in full without explicit licensing or legal approval:

- *Difficult Conversations*
- *Getting to Yes*
- *Crucial Conversations*
- *Nonviolent Communication*
- *Never Split the Difference*
- Gottman Institute books, courses, and assessment materials
- *SPIN Selling*
- *The Challenger Sale*
- proprietary negotiation, sales, leadership, and counseling programs

Safer alternatives:

1. License the content.
2. Ask qualified experts to create original Shoulder Surf playbooks informed by broad research.
3. Use government and open-access sources.
4. Store short factual metadata and citations rather than copyrighted prose.
5. Link users to the original source instead of reproducing it.
6. Obtain legal review before using excerpts, examples, diagrams, assessments, or named proprietary frameworks.

---

## 5. Recommended RAG Architecture

### 5.1 Three content layers

#### Layer A: Stable principles

Examples:

- reflective listening;
- emotional acknowledgment;
- clarity;
- handling uncertainty;
- interests versus positions;
- objective criteria;
- apology and restitution;
- proposal structure;
- closing and confirming next steps.

These should be versioned and reviewed periodically.

#### Layer B: Shoulder Surf playbooks

These are original, structured records derived from reviewed sources and expert input. They should be optimized for retrieval and product behavior, not written as long essays.

Example:

```json
{
  "card_id": "repair_apology_001",
  "namespace": "relationship_repair",
  "scenario_types": ["apology", "trust_repair", "relationship_conflict"],
  "phase": "opening",
  "goal": "Acknowledge harm without defensiveness",
  "recommended_behaviors": [
    "Name the specific action",
    "Acknowledge the likely impact",
    "Accept responsibility",
    "Pause and allow a response"
  ],
  "avoid": [
    "Conditional apologies",
    "Using intent to invalidate impact",
    "Demanding immediate forgiveness",
    "Explaining before acknowledging"
  ],
  "evaluation_signals": [
    "Specific acknowledgment",
    "Responsibility language",
    "Impact recognition",
    "No immediate self-justification"
  ],
  "source_refs": [
    "relationship_apology_research_001"
  ],
  "review_status": "expert_reviewed",
  "version": "1.0"
}
```

#### Layer C: Situation-specific facts

Examples:

- the veterinarian's confirmed diagnosis;
- the user's target salary;
- current market compensation;
- vehicle pricing;
- financing APR;
- warranty terms;
- proposal cost;
- project timeline;
- organizational constraints.

Facts that change should come from a live source, API, user upload, or verified current search rather than a stale vector index.

---

### 5.2 Separate indexes or namespaces

Do not place every document into one undifferentiated index.

Recommended namespaces:

```text
universal_conversation_skills
difficult_personal_conversations
child_and_family_conversations
relationship_repair
negotiation_general
salary_negotiation
large_purchase_negotiation
proposal_and_persuasion
presentation_delivery
safety_and_escalation
annotated_examples
source_metadata
```

A request can retrieve from multiple namespaces. For example:

```text
Scenario: Tell a 10-year-old that the family dog may not recover
Retrieve:
- universal_conversation_skills
- difficult_personal_conversations
- child_and_family_conversations
- safety_and_escalation
```

---

### 5.3 Separate retrieval by agent role

#### Counterpart simulator retrieval

Retrieve:

- persona;
- emotional state;
- goals;
- objections;
- private concerns;
- knowledge boundaries;
- likely questions;
- resistance level;
- conditions under which the counterpart may change position.

Do not give the simulator the full evaluation rubric. Otherwise, it may generate artificially “gradable” dialogue.

#### User coach retrieval

Retrieve:

- preparation checklist;
- recommended opening;
- questions to ask;
- wording to avoid;
- likely reactions;
- alternative approaches;
- scenario-specific facts;
- safety limits.

#### Evaluator retrieval

Retrieve:

- behavioral rubric;
- definitions;
- transcript evidence requirements;
- scenario-specific expectations;
- severity rules;
- counterexamples;
- uncertainty language;
- scoring anchors.

The evaluator should not see hidden simulator state when grading a real conversation.

---

## 6. Metadata Schema

Each chunk or knowledge card should carry metadata that can be filtered before semantic retrieval.

Recommended metadata:

```json
{
  "source_id": "cdc_cerc_messages_2018",
  "title": "CERC: Messages and Audiences",
  "publisher": "CDC",
  "source_type": "government_guidance",
  "namespace": "difficult_personal_conversations",
  "scenario_types": [
    "serious_news",
    "uncertainty",
    "emotionally_distressing_information"
  ],
  "skills": [
    "empathy",
    "clarity",
    "uncertainty",
    "audience_adaptation"
  ],
  "audience_age_min": null,
  "audience_age_max": null,
  "language": "en",
  "jurisdiction": "US",
  "clinical_scope": "nonclinical_adaptation_only",
  "license_status": "legal_review_required",
  "source_url": "https://www.cdc.gov/cerc/media/pdfs/CERC_Messages_and_Audiences.pdf",
  "published_date": "2018-01-01",
  "reviewed_date": "2026-07-20",
  "version": "1.0",
  "expert_review_status": "pending"
}
```

Add these fields for child-oriented content:

```json
{
  "developmental_stage": "middle_childhood",
  "age_band": "8-11",
  "caregiver_present": true,
  "sensitive_topic": "pet_illness_or_death"
}
```

Add these fields for negotiation:

```json
{
  "negotiation_type": "salary",
  "phase": "preparation",
  "concepts": ["BATNA", "objective_criteria", "reservation_point"],
  "requires_current_market_data": true
}
```

---

## 7. Chunking and Ingestion Strategy

### 7.1 Do not use arbitrary fixed-size chunking alone

A 500-token sliding window can split a definition from its examples or mix unrelated rules. Prefer semantic units:

- one principle;
- one behavior definition;
- one procedure;
- one example pair;
- one scoring anchor;
- one age-band recommendation;
- one negotiation concept;
- one safety rule.

### 7.2 Suggested chunk sizes

- **Structured playbook card:** 100–350 tokens
- **Behavior definition with examples:** 150–450 tokens
- **Source excerpt used for grounding:** 250–700 tokens
- **Annotated transcript turn:** one to four turns plus labels
- **Long source summary:** 300–800 tokens, written originally and linked to the source

### 7.3 Preserve context in metadata

Every chunk should retain:

- document title;
- section heading;
- page or anchor;
- source URL;
- publisher;
- publication date;
- review status;
- scenario and skill tags.

### 7.4 Deduplicate

Government guides and organizational pages may repeat the same language. Deduplicate semantically similar chunks to prevent retrieval from returning five copies of the same principle.

### 7.5 Keep source text and product guidance separate

Store:

1. **Source-derived content** with provenance.
2. **Original Shoulder Surf interpretation** in a separate record.
3. **Product policy** in a separate controlled configuration.

This makes legal review, updates, and source replacement much easier.

---

## 8. Scenario-Specific Playbooks

### 8.1 Difficult personal conversations

Preparation fields:

```json
{
  "important_fact": "",
  "known_vs_unknown": {
    "known": [],
    "unknown": []
  },
  "listener_age_or_context": "",
  "likely_emotions": [],
  "opening_message": "",
  "questions_to_invite": [],
  "promises_to_avoid": [],
  "support_options": [],
  "next_step": ""
}
```

Evaluation dimensions:

- Was the important fact stated clearly?
- Was uncertainty presented honestly?
- Was the message paced appropriately?
- Did the speaker acknowledge emotion?
- Did the speaker allow questions or silence?
- Did the speaker avoid false reassurance?
- Did the speaker avoid unnecessary detail?
- Were next steps explained?

### 8.2 Talking with a child about a pet's serious illness

The scenario should explicitly include:

- child's age;
- what the veterinarian has confirmed;
- prognosis certainty;
- whether treatment is planned;
- whether euthanasia is possible or scheduled;
- the child's prior experience with illness or death;
- family beliefs and preferred terminology;
- what the child is likely to observe.

Useful age bands:

- 4–6
- 7–9
- 10–12
- 13–15
- 16–17

Do not generate medical facts. The app should use user-provided veterinary information and encourage verification with the veterinarian.

Potential evaluation dimensions:

- direct but age-appropriate language;
- avoidance of confusing euphemisms;
- permission to feel sad, angry, or confused;
- no promise of recovery when uncertain;
- opportunity to ask the same question repeatedly;
- explanation of what will happen next;
- reassurance about care and safety without misleading certainty.

### 8.3 Relationship repair

Preparation fields:

```json
{
  "specific_behavior": "",
  "likely_impact": "",
  "responsibility_statement": "",
  "explanation_if_needed": "",
  "repair_action": "",
  "prevention_plan": "",
  "boundary_or_request": "",
  "expectation_about_forgiveness": "Do not demand immediate forgiveness"
}
```

Evaluation dimensions:

- specificity;
- responsibility;
- acknowledgment of impact;
- remorse;
- absence of blame shifting;
- restitution;
- prevention plan;
- respect for the other person's autonomy;
- willingness to listen.

### 8.4 Salary negotiation

Preparation fields:

```json
{
  "current_compensation": "",
  "target_compensation": "",
  "minimum_acceptable": "",
  "market_evidence": [],
  "accomplishments": [],
  "business_value": [],
  "batna": "",
  "tradeable_terms": [
    "base salary",
    "bonus",
    "equity",
    "title",
    "remote work",
    "start date",
    "vacation",
    "professional development"
  ],
  "anticipated_objections": [],
  "closing_request": ""
}
```

Evaluation dimensions:

- clear ask;
- evidence;
- articulation of value;
- curiosity about constraints;
- response to objections;
- avoidance of unnecessary ultimatums;
- negotiation across multiple terms;
- concession discipline;
- confirmation of final terms.

### 8.5 Large purchase negotiation

Preparation fields:

```json
{
  "item": "",
  "target_total_price": "",
  "maximum_total_price": "",
  "current_comparables": [],
  "financing_preapproval": "",
  "fees_and_addons": [],
  "warranty_terms": "",
  "inspection_requirements": [],
  "batna": "",
  "walk_away_conditions": [],
  "written_quote_requested": true
}
```

Evaluation dimensions:

- focus on total price rather than only monthly payment;
- written terms;
- identification of add-ons;
- financing comparison;
- verification of warranties and conditions;
- willingness to pause or walk away;
- no disclosure of maximum too early;
- final contract verification.

### 8.6 Selling an idea or presenting a proposal

Preparation fields:

```json
{
  "audience": "",
  "decision_maker": "",
  "problem_or_opportunity": "",
  "stakes": "",
  "proposal": "",
  "audience_benefits": [],
  "evidence": [],
  "cost": "",
  "implementation": "",
  "risks": [],
  "mitigations": [],
  "alternatives_considered": [],
  "likely_objections": [],
  "decision_requested": "",
  "next_step": ""
}
```

Evaluation dimensions:

- audience relevance;
- clear problem statement;
- concise proposal;
- evidence quality;
- benefits versus features;
- handling of risk;
- response to objections;
- explicit decision request;
- ownership and timing of next steps.

---

## 9. Universal Behavioral Rubric

The product should score behaviors independently rather than collapsing everything into a single “conversation quality” number.

### 9.1 Suggested dimensions

| Dimension | What It Measures | Observable Signals |
|---|---|---|
| Clarity | Whether the core message or ask was understandable | Direct statement, concrete terms, limited jargon, logical sequence |
| Listening | Whether the user demonstrated understanding | Reflection, summary, clarification, accurate restatement |
| Curiosity | Whether the user explored the other person's perspective | Open questions, follow-up questions, checking assumptions |
| Emotional acknowledgment | Whether emotion was recognized without being exaggerated or dismissed | Naming emotion tentatively, validating impact, allowing silence |
| Respect and autonomy | Whether the other person retained agency | No coercion, no shaming, choices presented honestly |
| Specificity | Whether claims and requests were concrete | Specific behavior, date, amount, commitment, next step |
| Responsibility | Whether the user owned their contribution | No blame shifting, no “sorry you felt,” clear acknowledgment |
| Evidence | Whether factual claims were supported | Data, examples, documented outcomes, objective criteria |
| Defensiveness | Whether the user prematurely argued or redirected | Interruptions, rebuttal before acknowledgment, counteraccusation |
| Pacing | Whether the amount and timing of information fit the situation | Pauses, manageable information, no rapid overload |
| Objection handling | Whether resistance was explored and answered | Clarification, acknowledgment, targeted response |
| Boundary quality | Whether limits were clear, respectful, and enforceable | Specific boundary, consequence under user's control |
| Closing | Whether the conversation produced a clear next step | Decision, owner, date, follow-up, written confirmation |

### 9.2 Evidence requirement

Every score should include:

- transcript quote or timestamp;
- behavior label;
- interpretation;
- confidence;
- recommended alternative.

Example:

```json
{
  "dimension": "emotional_acknowledgment",
  "score": 2,
  "scale_max": 5,
  "evidence": [
    {
      "speaker": "user",
      "timestamp_start": 92.4,
      "timestamp_end": 96.1,
      "text": "I know this is upsetting, but we need to focus on what happens next."
    }
  ],
  "interpretation": "The user recognized that the listener was upset, but immediately redirected to logistics without allowing a response.",
  "confidence": 0.84,
  "stronger_alternative": "I can see this is upsetting. We can pause. What is going through your mind right now?"
}
```

### 9.3 Avoid false precision

Do not present a score such as `83.4% emotionally healthy`. Prefer:

- anchored 1–5 ratings;
- labels such as `strong`, `mixed`, or `needs attention`;
- confidence ranges;
- evidence counts;
- separate critical-failure flags.

---

## 10. Annotated Conversation Dataset

The annotated examples may become more valuable than the raw source corpus.

### 10.1 Recommended annotation schema

```json
{
  "conversation_id": "pet_illness_child_10_001",
  "scenario_type": "child_pet_serious_illness",
  "language": "en",
  "participants": [
    {"role": "parent"},
    {"role": "child", "age": 10}
  ],
  "context": {
    "diagnosis_confirmed": true,
    "prognosis_certainty": "uncertain",
    "treatment_planned": true
  },
  "turns": [
    {
      "turn_id": 1,
      "speaker": "parent",
      "text": "The veterinarian found that Max is very sick.",
      "labels": [
        "clear_fact",
        "age_appropriate",
        "serious_news"
      ],
      "quality": "effective",
      "rationale": "States the important fact directly without unnecessary detail."
    }
  ],
  "overall_outcomes": {
    "clarity": 4,
    "listening": 3,
    "emotional_acknowledgment": 4,
    "false_reassurance": false
  }
}
```

### 10.2 Include difficult negative examples

Examples should cover:

- euphemisms that create confusion;
- false certainty;
- overexplaining;
- minimizing;
- blame shifting;
- conditional apology;
- premature solutioning;
- anchoring mistakes;
- negotiating only on price;
- weak proposal close;
- excessive agreement by the simulated counterpart;
- unsafe or manipulative language.

### 10.3 Include multiple valid approaches

There is rarely one perfect sentence. Annotate several acceptable strategies to avoid training the evaluator to reward only one script.

---

## 11. Retrieval Flow

### 11.1 Preparation flow

```text
1. Classify scenario and user goal.
2. Collect missing scenario facts.
3. Select namespaces.
4. Apply metadata filters.
5. Retrieve 4–8 playbook cards.
6. Retrieve 1–3 source-grounding chunks.
7. Retrieve 1–2 annotated examples.
8. Generate preparation plan.
9. Validate for safety, unsupported factual claims, and role consistency.
```

### 11.2 Simulation flow

```text
1. Load counterpart persona and hidden goals.
2. Load scenario facts and knowledge limits.
3. Retrieve realistic reaction patterns.
4. Generate one conversational turn.
5. Update counterpart state based on the user's actual behavior.
6. Preserve consistency across turns.
7. Do not coach the user while in counterpart role unless the mode explicitly pauses.
```

### 11.3 Evaluation flow

```text
1. Segment transcript by speaker and timestamp.
2. Detect scenario phase: opening, exploration, proposal, resistance, repair, close.
3. Retrieve relevant rubric cards.
4. Extract evidence for each dimension.
5. Score with anchored definitions.
6. Run a contradiction and overclaim check.
7. Generate strengths, improvements, and alternatives.
8. Produce one next-round practice objective.
```

---

## 12. Hybrid Retrieval

Use a combination of:

- semantic vector search;
- keyword or BM25 search;
- metadata filtering;
- reranking;
- recency filtering for changing facts;
- source-authority weighting.

Example weighting:

```text
Final retrieval score =
0.40 semantic similarity
+ 0.20 keyword relevance
+ 0.15 scenario metadata match
+ 0.10 source authority
+ 0.10 expert review status
+ 0.05 recency where relevant
```

Do not use recency to demote stable foundational research automatically. Recency matters much more for compensation, prices, laws, product terms, and policies.

---

## 13. Safety and Product Boundaries

The app should clearly state that it provides rehearsal and communication feedback, not:

- psychotherapy;
- medical diagnosis;
- legal advice;
- financial advice;
- emergency intervention;
- a factual determination of another person's motives;
- a guarantee of a successful outcome.

### 13.1 High-risk escalation categories

Introduce special handling when a conversation includes:

- self-harm or suicide;
- threats or violence;
- child abuse or neglect;
- domestic violence or coercive control;
- stalking;
- medical emergencies;
- illegal activity;
- imminent danger;
- severe psychological crisis.

In these cases, ordinary rehearsal may be inappropriate. The application should switch to a safety-oriented flow and encourage appropriate professional or emergency support.

### 13.2 Relationship repair safeguard

Do not coach a user to “repair” a relationship in a way that pressures a harmed person to reconcile. The app can help the user apologize and make amends, but it should preserve the other person's right not to forgive, resume contact, or accept the proposed repair.

### 13.3 Child conversation safeguard

The app should:

- ask the child's age;
- use caregiver-supplied facts;
- avoid diagnosing the child;
- avoid generating false medical certainty;
- recommend professional guidance when the scenario is especially serious;
- avoid coaching concealment or deception.

### 13.4 Negotiation safeguard

The app should not encourage fraud, misrepresentation, forged evidence, unlawful discrimination, threats, or misuse of confidential information.

---

## 14. Evaluation and Testing Plan

### 14.1 Build fixtures by scenario

Recommended initial fixture set:

| Scenario group | Minimum complete conversations |
|---|---:|
| Difficult personal news | 15 |
| Child and family conversations | 15 |
| Relationship conflict and repair | 20 |
| Salary negotiation | 15 |
| Large purchase negotiation | 15 |
| Proposal and persuasion | 15 |
| Boundary setting | 10 |
| Safety and refusal behavior | 10 |

Target: **100–120 complete conversations**, not only isolated utterances.

### 14.2 Test age and context variation

For child scenarios, include:

- ages 4–6;
- ages 7–9;
- ages 10–12;
- ages 13–15;
- ages 16–17;
- known outcome versus uncertain outcome;
- child asks repeated questions;
- child becomes silent;
- child becomes angry;
- child blames themselves;
- parent becomes emotional;
- bilingual English/Spanish scenarios.

### 14.3 Core model metrics

- role consistency;
- age appropriateness;
- realism;
- emotional range;
- excessive compliance;
- unnecessary refusal rate;
- critical safety miss rate;
- factual hallucination rate;
- rubric adherence;
- evaluator-human correlation;
- evaluator overpraise rate;
- evaluator severity bias;
- latency;
- cost per complete rehearsal;
- token usage;
- recovery after user interruption.

### 14.4 Evaluate retrieval separately

Track:

- source hit rate;
- correct namespace selection;
- relevant-card recall;
- irrelevant retrieval rate;
- citation accuracy;
- stale-fact rate;
- conflicting-source handling;
- effect of retrieval on outcome versus prompt-only baseline.

### 14.5 Human review

Use reviewers with appropriate expertise:

- communication or conflict-resolution specialists;
- licensed mental-health professionals for safety review;
- pediatric or child-development experts for child scenarios;
- negotiation specialists;
- sales or presentation coaches;
- bilingual reviewers for Spanish fixtures.

Expert reviewers should define the rubric and adjudicate disagreements. The model should not be the sole judge of its own performance.

---

## 15. Suggested Initial Implementation Phases

### Phase 1: Narrow pilot

Implement four namespaces:

1. `universal_conversation_skills`
2. `difficult_personal_conversations`
3. `relationship_repair`
4. `negotiation_general`

Create:

- 100–150 original playbook cards;
- 40 annotated conversations;
- 10–15 safety cards;
- source metadata registry;
- evidence-based transcript feedback.

### Phase 2: Specialized scenarios

Add:

- child and pet illness;
- salary negotiation;
- major vehicle purchase;
- proposal and idea selling;
- boundary setting;
- workplace feedback.

### Phase 3: Live facts and personalization

Add:

- current salary data;
- product and price comparisons;
- user-provided documents;
- proposal documents;
- job descriptions;
- negotiation constraints;
- preferred communication style.

### Phase 4: Longitudinal coaching

Track:

- repeated patterns;
- skills improving over time;
- scenario-specific confidence;
- recurring missed opportunities;
- practice goals;
- user-approved preferences.

Do not infer sensitive psychological traits from conversation history.

---

## 16. Recommended Team Deliverables

### Product

- scenario taxonomy;
- supported versus unsupported use cases;
- safety escalation policy;
- user-facing claims;
- scoring presentation;
- consent and privacy language.

### AI/backend

- ingestion pipeline;
- source registry;
- namespaces;
- metadata filters;
- hybrid retrieval;
- reranker;
- agent-role separation;
- transcript evidence extraction;
- evaluation harness;
- model and prompt versioning.

### iOS

- scenario setup forms;
- fact-confirmation UI;
- rehearsal mode;
- pause-for-coaching mode;
- transcript evidence cards;
- score explanations;
- source links;
- privacy controls;
- deletion and export controls.

### Design

- counterpart persona display;
- uncertainty indicators;
- evidence-linked feedback;
- “practice this next” cards;
- separation of factual guidance from communication coaching;
- nonclinical wording;
- accessible visualizations.

### Legal and privacy

- content licensing review;
- data retention policy;
- voice recording consent;
- child-related data handling;
- health information handling;
- source attribution;
- model-provider data terms;
- deletion and export requirements.

---

## 17. Source Registry Template

```yaml
source_id: miti_4_2_1
title: Motivational Interviewing Treatment Integrity Code 4.2.1
publisher: Center on Alcoholism, Substance Abuse, and Addictions
url: https://casaa.unm.edu/assets/docs/miti4_21.pdf
source_type: coding_manual
topics:
  - empathy
  - partnership
  - reflection
  - questions
  - autonomy_support
intended_product_use:
  - rubric_design
  - evaluator_training
full_text_ingestion: pending_legal_review
derived_cards_allowed: pending_legal_review
attribution_required: true
expert_review: pending
last_verified: 2026-07-20
version: 1.0
```

---

## 18. Knowledge Card Quality Checklist

Before publishing a card:

- Is it traceable to one or more reviewed sources?
- Is the wording original?
- Is it behaviorally specific?
- Is it appropriate for the declared scenario?
- Does it avoid clinical diagnosis?
- Does it distinguish facts from advice?
- Does it preserve uncertainty?
- Does it include useful positive and negative examples?
- Can the evaluator detect it from transcript evidence?
- Has it been reviewed for cultural and language bias?
- Does it include a version and review date?
- Is the license status recorded?

---

## 19. Recommended Starting Priorities

The highest-value initial sources are:

1. MITI 4.2.1 for behavioral coding concepts.
2. CDC CERC materials for empathy, uncertainty, audience needs, and message structure.
3. SAMHSA trauma-informed principles for safety, choice, trust, and collaboration.
4. Federal Plain Language guidance for clarity.
5. Open-access conflict and apology research.
6. U.S. Department of Labor salary negotiation guides.
7. FTC car-buying guidance for large-purchase negotiation fixtures.
8. AAP/HealthyChildren guidance for child and pet-loss scenarios, subject to rights review.
9. Original Shoulder Surf playbooks.
10. Expert-annotated conversations.

The primary product advantage will not come from indexing the largest number of documents. It will come from converting trustworthy material into concise, scenario-aware, expert-reviewed behavioral cards and pairing those cards with realistic annotated conversations.

---

## 20. Final Recommendation

Build the first RAG release as a controlled knowledge system rather than an open-ended document dump.

The minimum viable corpus should contain:

- **150–250 structured playbook cards**
- **50–75 annotated complete conversations**
- **10–20 safety and escalation cards**
- **a source registry with licensing status**
- **separate retrieval policies for simulation, coaching, and evaluation**
- **a human-reviewed benchmark set**

This will provide a stronger foundation than ingesting thousands of unstructured pages. It will also make the product easier to evaluate, explain, update, and defend from a safety and compliance perspective.
