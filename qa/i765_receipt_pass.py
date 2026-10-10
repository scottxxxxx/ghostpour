"""Build the blind receipt pass for the I-765 interviewer lane (2026-10-09).

Renders one request per receipt from the lead's brief (I765 App/Docs/
contracts/gp-lane-brief-i765.md, sections 4 and 7) exactly as GP assembles
it: the served systemPrompt of config/remote/i765/interviewer-turn.json and
the userPromptTemplate filled with the receipt's variables. No model is
called here. A Claude sub agent plays the lane on each file (a SIMULATION,
not the production model through the API; no paid calls) and a second,
blind agent scores the raw objects against section 8. The lead runs the
same receipts with their own judges and we compare.

Usage: python qa/i765_receipt_pass.py  -> qa/runs/i765-receipts-2026-10-09/requests/*.md
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "qa" / "runs" / "i765-receipts-2026-10-09"
CFG = json.loads((ROOT / "config/remote/i765/interviewer-turn.json").read_text())

# --- the brief's agenda lines (section 4; the ids, titles and options are the catalog's) ---
L = {
 "reason": "q_p1_reason | Part 1: Why you are applying | p1.reason | Part 1 is why you are filing. Is this your first work permit, a replacement for a card that was lost, stolen or damaged, or a renewal of a permit you already have? If it is a renewal, keep a copy of your current card handy, because the form asks you to attach it. | options: initial, replacement, renewal",
 "category": "q_p2_eligibility_category | Part 2: About you | p2.eligibility_category | Next, the one thing that decides the rest of this form: your eligibility category, the code USCIS writes in Item 27, like (c)(9) or (c)(3)(B). It is on your receipt notice, on the papers from your school, or in the instructions for your situation. The categories this app prepares are: (c)(9) if your green card application, the I-485, is pending; (c)(3)(A), (c)(3)(B) and (c)(3)(C) if you are an F-1 student on OPT, the C is the STEM extension; (a)(12) if you have Temporary Protected Status and (c)(19) if your TPS application is pending; (a)(17) if you are the spouse of an E visa holder; (a)(18) if you are the spouse of an L-1; (a)(3) if you are a refugee; (a)(5) if you were granted asylum; and (c)(26) if you are the H-4 spouse of an H-1B worker with an approved I-140. Which one is yours? If it is none of these, say other. | options: c9, c3a, c3b, c3c, a12, c19, a17, a18, a3, a5, c26, other",
 "full_name": "q_p2_full_name | Part 2: About you | p2.given_name,p2.middle_name,p2.family_name | Now Part 2, which is about you. Give me all of it in one go and I'll only ask for what's missing: your full legal name, any other names you've used, where USCIS should mail your card, your A-Number, your date and place of birth, your passport, your last arrival in the United States and your current status. To start, I fill this form to match your passport and your other USCIS papers, so give me the name exactly as it is printed there. If you have two surnames or a hyphenated surname, both belong in the family name. What is your full legal name?",
 "other_names": "q_p2_other_names_gate | Part 2: About you | p2.has_other_names | Have you ever used any other names, like a maiden name, a nickname on official papers, or a different spelling? USCIS wants every one of them. | options: yes, no",
 "mailing": "q_p2_mailing_address | Part 2: About you | p2.mailing_address.street,p2.mailing_address.unit,p2.mailing_address.city,p2.mailing_address.postal_code,p2.mailing_address.in_care_of | Where should USCIS mail your work permit? Street address, apartment or suite if any, city, state and ZIP. If the mail goes to someone else's name, tell me that name too.",
 "physical": "q_p2_mailing_is_physical | Part 2: About you | p2.mailing_is_physical | Is that also where you live, your physical address? | options: yes, no",
 "a_gate": "q_p2_a_number_gate | Part 2: About you | p2.has_a_number | Do you have an A-Number, the USCIS number printed on a notice or a card USCIS gave you? Most people with a pending green card application have one; many F-1 students do not, and that is fine. | options: yes, no",
 "uscis_gate": "q_p2_uscis_account_gate | Part 2: About you | p2.has_uscis_account | Do you have a USCIS online account number? You only have one if you filed something online with USCIS before; it is fine to say no. | options: yes, no",
 "reads_en": "q_p3_reads_english | Part 3: How to reach you | p3.reads_english | Part 3 is your statement and how USCIS can reach you. Can you read and understand English, and did you read and understand every question on this application yourself? If someone is interpreting for you, say no, and we will put the interpreter on the form. | options: yes, no",
 "ssn_gate": "q_p2_ssn_gate | Part 2: About you | p2.has_ssn | Do you have a Social Security number? If you were never issued one, say no. | options: yes, no",
 "cit1": "q_p2_citizenship1 | Part 2: About you | p2.citizenship1 | What country are you a citizen or national of?",
 "cit2": "q_p2_second_citizenship_gate | Part 2: About you | p2.has_second_citizenship | Do you hold citizenship or nationality in a second country as well? | options: yes, no",
 "birth": "q_p2_birth_place | Part 2: About you | p2.birth_city,p2.birth_state,p2.birth_country | Where were you born? The city or town, the state or province, and the country, using the country's name as it was when you were born.",
 "dob": "q_p2_date_of_birth | Part 2: About you | p2.date_of_birth | What is your date of birth? You can say it like April 12, 1987.",
 "i94_gate": "q_p2_i94_gate | Part 2: About you | p2.has_i94 | Do you have a Form I-94 arrival record number? It is the 11-character number on the I-94 you got when you last entered the United States, or on the CBP website. If you never had one, say no. | options: yes, no",
 "passport": "q_p2_passport | Part 2: About you | p2.passport_number,p2.passport_country,p2.passport_expiry,p2.travel_document_number | Now your passport: the number of your most recently issued passport, the country that issued it, and its expiration date. Give the number even if that passport has expired. If you also have a travel document number, like a refugee travel document or an advance parole document, say that too.",
 "arrival": "q_p2_last_arrival | Part 2: About you | p2.last_arrival_date,p2.last_arrival_place,p2.status_at_arrival | Tell me about your last arrival in the United States: the date, on or about is fine, the city or airport you came through, and your status when you entered, like B-2 visitor, F-1 student, parolee, or no status.",
 "sevis_gate": "q_p2_sevis_gate | Part 2: About you | p2.has_sevis_number | Do you have a SEVIS number? It starts with N and is printed on a Form I-20 or DS-2019; only students and exchange visitors have one. | options: yes, no",
 "contact": "q_p3_contact | Part 3: How to reach you | p3.daytime_phone,p3.mobile_phone,p3.email | How can USCIS reach you? A daytime phone number, a mobile number if it is different, and your email.",
 # Composed from the catalog (section 5), not in the brief's samples; said so in the run notes.
 "filed_before": "q_p2_filed_i765_before | Part 2: About you | p2.filed_i765_before | Have you ever filed a Form I-765 before, for any work permit? | options: yes, no",
 "sex": "q_p2_sex | Part 2: About you | p2.sex | What is your sex as it appears on your birth certificate? | options: male, female",
 "marital": "q_p2_marital_status | Part 2: About you | p2.marital_status | What is your marital status? | options: single, married, divorced, widowed",
 # es lines (section 4.1 / composed)
 "es_full_name": "q_p2_full_name | Part 2: About you | p2.given_name | Ahora la Parte 2, que es sobre usted. Dígame todo de una vez y solo le preguntaré lo que falte: su nombre legal completo, otros nombres que haya usado, a dónde debe USCIS enviarle la tarjeta, su número A, su fecha y lugar de nacimiento, su pasaporte, su última llegada a Estados Unidos y su estatus actual. Para empezar, lleno este formulario para que coincida con su pasaporte y sus otros documentos de USCIS, así que dígame su nombre tal como está impreso ahí. Vamos por partes: primero la casilla Nombre de pila (Given Name), y los apellidos se los pido después. ¿Cuál es su nombre de pila, o sus nombres si tiene más de uno, exactamente como aparece en su pasaporte?",
 "es_family_name": "q_p2_family_name | Part 2: About you | p2.family_name | Ahora la casilla Apellidos (Family Name): si tiene dos apellidos, los dos van aquí, tal como aparecen en su pasaporte. ¿Cuáles son sus apellidos?",
 "es_middle_check": "q_p2_middle_name_check | Part 2: About you | p2.has_middle_name,p2.middle_name | ¿Tiene un segundo nombre legal, como aparece en su pasaporte? | options: yes, no",
 "es_other_names": "q_p2_other_names_gate | Part 2: About you | p2.has_other_names | ¿Ha usado alguna vez otros nombres, como el apellido de soltera, un apodo en documentos oficiales o una forma distinta de escribirlo? USCIS quiere todos. | options: yes, no",
 "es_reads_en": "q_p3_reads_english | Part 3: How to reach you | p3.reads_english | La Parte 3 es su declaración y cómo USCIS puede comunicarse con usted. ¿Puede leer y entender inglés, y leyó y entendió usted mismo cada pregunta de esta solicitud? Si alguien le está interpretando, diga que no, y pondremos al intérprete en el formulario. | options: yes, no",
 "es_interp_lang": "q_p3_interpreter_language | Part 3: How to reach you | p3.interpreter_language | ¿En qué idioma le interpretó esa persona las preguntas?",
 "es_contact": "q_p3_contact | Part 3: How to reach you | p3.daytime_phone,p3.mobile_phone,p3.email | ¿Cómo puede USCIS comunicarse con usted? Un teléfono de día, un celular si es distinto, y su correo electrónico.",
}

CTX_EN = "state: Texas; language: English; interpreter: no; filing for self: yes"
CTX_ES = "state: Texas; language: Spanish; interpreter: no; filing for self: yes"
ORIENTED = "; oriented: the app already greeted her and explained answering several things at once, so do not greet or explain, open with the first question"
OPENING_QS = ("Is anyone else preparing this application for you, like a friend, a notario or a lawyer?\n"
              "Do you have your documents with you: your passport, your I-94 or arrival record, your most recent receipt notice, your I-20 if you are a student, and your current card if this is a renewal or a replacement?")
KF_START = "p2.mailing_address.state: TX"
KF_42 = ("p1.reason: initial\np2.eligibility_category: c9\np2.given_name: Valentina\np2.middle_name: Isabel\np2.family_name: Morales\n"
         "p2.has_other_names: no\np2.mailing_address.street: 2210 Riverside Drive\np2.mailing_address.unit: Apt 14B\np2.mailing_address.city: Austin\n"
         "p2.mailing_address.state: TX\np2.mailing_address.postal_code: 78741\np2.mailing_address.in_care_of: none (confirmed)\np2.mailing_is_physical: yes\n"
         "p2.has_a_number: yes\np2.a_number: 200555123\np2.has_uscis_account: no\np2.uscis_account_number: none (confirmed)\np2.sex: female\n"
         "p2.marital_status: single\np2.filed_i765_before: no")
KF_43 = KF_42 + ("\np2.has_ssn: yes\np2.ssn: 555550143\np2.citizenship1: Colombia\np2.has_second_citizenship: no\np2.birth_city: Bogota\n"
                 "p2.birth_state: Cundinamarca\np2.birth_country: Colombia\np2.date_of_birth: 1994-03-18\np2.has_i94: yes\np2.i94_number: 55555012345\n"
                 "p2.passport_number: AV555456\np2.passport_country: Colombia\np2.passport_expiry: 2030-06-30\np2.travel_document_number: none (confirmed)\n"
                 "p2.last_arrival_date: 2023-08-02\np2.last_arrival_place: Houston, Texas\np2.status_at_arrival: B-2 visitor\np2.current_status: Adjustment applicant, I-485 pending")
BOUNDARY_P1 = "the standing question is the last in Part 1 Why you are applying; when it is answered, summarize Part 1 in one sentence and open Part 2 About you in the same reply, without asking for confirmation"
BOUNDARY_P2 = "the standing question is the last in Part 2 About you; when it is answered, summarize Part 2 in one sentence and open Part 3 How to reach you in the same reply, without asking for confirmation"
P2_VOL = "p2.given_name, p2.middle_name, p2.family_name, p2.has_other_names, p2.mailing_address.street, p2.mailing_address.unit, p2.mailing_address.city, p2.mailing_address.postal_code, p2.mailing_address.in_care_of, p2.mailing_is_physical, p2.has_a_number, p2.a_number, p2.has_uscis_account, p2.uscis_account_number, p2.sex, p2.marital_status, p2.filed_i765_before, p2.has_ssn, p2.ssn, p2.citizenship1, p2.has_second_citizenship, p2.citizenship2, p2.birth_city, p2.birth_state, p2.birth_country, p2.date_of_birth, p2.has_i94, p2.i94_number, p2.passport_number, p2.passport_country, p2.passport_expiry, p2.travel_document_number, p2.last_arrival_date, p2.last_arrival_place, p2.status_at_arrival, p2.current_status, p2.has_sevis_number, p2.sevis_number, p3.reads_english, p3.daytime_phone, p3.mobile_phone, p3.email"


def agenda(*keys):
    return "\n".join(L[k] for k in keys)


def conv(*lines):
    return "\n".join(lines)


RECEIPTS = [
  # id, locale, standing/agenda, known_facts, conversation, applicant line, extras
  dict(id="s4-opening", locale="en", agenda=agenda("reason", "category", "full_name", "other_names", "mailing", "physical", "a_gate", "uscis_gate", "reads_en"),
       known=KF_START, conversation="[start of interview]", said="[start of interview]", ctx=CTX_EN + ORIENTED, opening=OPENING_QS, boundary=BOUNDARY_P1,
       expect="intent control; reply is the first BEFORE WE BEGIN question alone (at most a two word bridge); no greeting; asking null or the standing node; no facts"),
  dict(id="s4-part1-boundary", locale="en", agenda=agenda("reason", "category", "full_name", "other_names", "mailing", "physical", "a_gate", "uscis_gate", "reads_en"),
       known=KF_START, conversation=conv("INTERVIEWER: Is this your first work permit, a replacement for a card that was lost, stolen or damaged, or a renewal of a permit you already have?", "APPLICANT: My first one, I've never had a work permit."),
       said="My first one, I've never had a work permit.", ctx=CTX_EN, boundary=BOUNDARY_P1,
       expect="p1.reason initial; section_checkpoint part 1 awaiting_confirmation false; asking q_p2_eligibility_category; reply summarizes Part 1 in one clause and asks the category (the long opener, read as given)"),
  dict(id="s4-sevis-no", locale="en", agenda=agenda("sevis_gate", "reads_en", "contact"), known=KF_43,
       conversation=conv("INTERVIEWER: Do you have a SEVIS number? It starts with N and is printed on a Form I-20 or DS-2019; only students and exchange visitors have one.", "APPLICANT: No, I was never a student here."),
       said="No, I was never a student here.", ctx=CTX_EN, boundary=BOUNDARY_P2,
       expect="p2.has_sevis_number no; section_checkpoint part 2 awaiting_confirmation false; asking q_p3_reads_english; one sentence rounding Part 2 off and the Part 3 opener; no 'complete and correct'"),
  dict(id="r7.1-four-facts", locale="en", agenda=agenda("full_name", "other_names", "mailing", "physical", "a_gate", "uscis_gate", "sex", "marital", "reads_en"),
       known="p1.reason: initial\np2.eligibility_category: c9\np2.mailing_address.state: TX",
       conversation=conv("INTERVIEWER: A pending I-485, that's (c)(9). Now Part 2, which is about you: what is your full legal name, exactly as printed on your passport?", "APPLICANT: Valentina Isabel Morales, no other names, my mail goes to 2210 Riverside Drive apartment 14B in Austin 78741, and that's where I live."),
       said="Valentina Isabel Morales, no other names, my mail goes to 2210 Riverside Drive apartment 14B in Austin 78741, and that's where I live.", ctx=CTX_EN, volunteer=P2_VOL,
       expect="intent volunteered_extra; nine facts (given, middle, family, has_other_names no, street, unit, city, postal_code, mailing_is_physical yes); NO state fact; asking q_p2_a_number_gate; name and street and ZIP read back; one question"),
  dict(id="r7.2-no-a-number", locale="en", agenda=agenda("a_gate", "uscis_gate", "sex", "marital", "filed_before", "ssn_gate", "cit1", "reads_en"),
       known="p1.reason: initial\np2.eligibility_category: c3b\np2.given_name: Valentina\np2.middle_name: Isabel\np2.family_name: Morales\np2.has_other_names: no\np2.mailing_address.street: 2210 Riverside Drive\np2.mailing_address.unit: Apt 14B\np2.mailing_address.city: Austin\np2.mailing_address.state: TX\np2.mailing_address.postal_code: 78741\np2.mailing_is_physical: yes",
       conversation=conv("INTERVIEWER: Do you have an A-Number, the USCIS number printed on a notice or a card USCIS gave you?", "APPLICANT: No, I don't have one, I'm a student."),
       said="No, I don't have one, I'm a student.", ctx=CTX_EN, volunteer=P2_VOL,
       expect="p2.has_a_number no and nothing else; asking q_p2_uscis_account_gate; the reply opens with the account question; no N/A, no echo, no 'that's normal for students'"),
  dict(id="r7.3-i94-electronic", locale="en", agenda=agenda("i94_gate", "passport", "arrival", "sevis_gate", "reads_en"),
       known=KF_42 + "\np2.has_ssn: yes\np2.ssn: 555550143\np2.citizenship1: Colombia\np2.has_second_citizenship: no\np2.birth_city: Bogota\np2.birth_state: Cundinamarca\np2.birth_country: Colombia\np2.date_of_birth: 1994-03-18",
       conversation=conv("INTERVIEWER: Do you have a Form I-94 arrival record number?", "APPLICANT: I have one but it's electronic, I don't know the number off hand."),
       said="I have one but it's electronic, I don't know the number off hand.", ctx=CTX_EN, volunteer=P2_VOL,
       expect="intent partial_answer; p2.has_i94 yes; exactly one deferred on p2.i94_number origin applicant partial null; asking q_p2_passport; reply says once the number stays open; never tells her to say no, no N/A"),
  dict(id="r7.4-category-in-words", locale="en", agenda=agenda("category", "full_name", "other_names", "mailing", "physical", "a_gate", "uscis_gate", "reads_en"),
       known="p1.reason: initial\np2.mailing_address.state: TX",
       conversation=conv("INTERVIEWER: A first permit. Now the one thing that decides the rest of the form: your eligibility category, the code USCIS writes in Item 27, like (c)(9) or (c)(3)(B). Which one is yours? If it is none of these, say other.", "APPLICANT: My green card application is pending, I filed the I-485 in March."),
       said="My green card application is pending, I filed the I-485 in March.", ctx=CTX_EN,
       expect="p2.eligibility_category c9 on THIS turn; escalation null; reply names (c)(9) as a statement, no verdict words; asking q_p2_full_name; no deferred and no 'noted' for the March date"),
  dict(id="r7.5-category-outside-v1", locale="en", agenda=agenda("category", "full_name", "other_names", "mailing", "physical", "a_gate", "uscis_gate", "reads_en"),
       known="p1.reason: initial\np2.mailing_address.state: TX",
       conversation=conv("INTERVIEWER: A first permit. Now the one thing that decides the rest of the form: your eligibility category, the code USCIS writes in Item 27, like (c)(9) or (c)(3)(B). Which one is yours? If it is none of these, say other.", "APPLICANT: I applied for asylum last year, it's still pending."),
       said="I applied for asylum last year, it's still pending.", ctx=CTX_EN,
       expect="p2.eligibility_category other and no other fact; escalation NULL (the lead's ruling supersedes the brief's risk_trigger here); asking q_p2_eligibility_category_other; reply names the attorney as the next step once and asks the category's name; no clock, no consequences"),
  dict(id="r7.6-question-back", locale="en", agenda=agenda("passport", "arrival", "sevis_gate", "reads_en"),
       known=KF_42 + "\np2.has_ssn: yes\np2.ssn: 555550143\np2.citizenship1: Colombia\np2.has_second_citizenship: no\np2.birth_city: Bogota\np2.birth_state: Cundinamarca\np2.birth_country: Colombia\np2.date_of_birth: 1994-03-18\np2.has_i94: yes\np2.i94_number: 55555012345",
       conversation=conv("INTERVIEWER: Now your passport: the number of your most recently issued passport, the country that issued it, and its expiration date.", "APPLICANT: Why do you need my passport?"),
       said="Why do you need my passport?", ctx=CTX_EN,
       expect="intent question_back; facts empty; asking q_p2_passport; one sentence of literal meaning (Items 18 to 21) then the short form of the passport question; no 'you must'"),
  dict(id="r7.7-legal-question", locale="en", agenda=agenda("filed_before", "ssn_gate", "cit1", "cit2", "birth", "dob", "i94_gate", "passport", "reads_en"),
       known="p1.reason: renewal\np2.eligibility_category: a12\np2.given_name: Valentina\np2.middle_name: Isabel\np2.family_name: Morales\np2.has_other_names: no\np2.mailing_address.street: 2210 Riverside Drive\np2.mailing_address.unit: Apt 14B\np2.mailing_address.city: Austin\np2.mailing_address.state: TX\np2.mailing_address.postal_code: 78741\np2.mailing_is_physical: yes\np2.has_a_number: yes\np2.a_number: 200555123\np2.has_uscis_account: no\np2.sex: female\np2.marital_status: single",
       conversation=conv("INTERVIEWER: Have you ever filed a Form I-765 before, for any work permit?", "APPLICANT: Wait, if I say yes, will this get me in trouble?"),
       said="Wait, if I say yes, will this get me in trouble?", ctx=CTX_EN,
       expect="intent legal_question; facts empty; escalation trigger legal_question; asking q_p2_filed_i765_before; reply states the literal question, names the attorney once, re-asks; none of fine, okay, won't, better to"),
  dict(id="r7.8a-es-given-name", locale="es", agenda=agenda("es_full_name", "es_other_names", "es_reads_en"),
       known="p1.reason: initial\np2.eligibility_category: c9\np2.mailing_address.state: TX",
       conversation=conv("INTERVIEWER: Una I-485 pendiente, eso es (c)(9). Ahora la Parte 2, que es sobre usted. Vamos por partes: primero la casilla Nombre de pila, y los apellidos se los pido después. ¿Cuál es su nombre de pila, o sus nombres si tiene más de uno, exactamente como aparece en su pasaporte?", "APPLICANT: Valentina Isabel."),
       said="Valentina Isabel.", ctx=CTX_ES,
       expect="p2.given_name 'Valentina Isabel' as ONE value, nothing as middle or family; asking q_p2_family_name; reply in es (usted) plus en; never asks which word is the given name"),
  dict(id="r7.8b-es-double-surname", locale="es", agenda=agenda("es_family_name", "es_middle_check", "es_other_names", "es_reads_en"),
       known="p1.reason: initial\np2.eligibility_category: c9\np2.mailing_address.state: TX\np2.given_name: Valentina Isabel",
       conversation=conv("INTERVIEWER: Valentina Isabel. Ahora la casilla Apellidos: si tiene dos apellidos, los dos van aquí, tal como aparecen en su pasaporte. ¿Cuáles son sus apellidos?", "APPLICANT: Morales Castro."),
       said="Morales Castro.", ctx=CTX_ES,
       expect="p2.family_name 'Morales Castro' as ONE value; asking q_p2_middle_name_check; reply reads the surnames back and asks about a middle name; never splits the surnames; usted"),
  dict(id="r7.9-correction-later", locale="en", agenda=agenda("passport", "arrival", "sevis_gate", "reads_en"),
       known=KF_42 + "\np2.has_ssn: yes\np2.ssn: 555550143\np2.citizenship1: Colombia\np2.has_second_citizenship: no\np2.birth_city: Bogota\np2.birth_state: Cundinamarca\np2.birth_country: Colombia\np2.date_of_birth: 1994-03-18\np2.has_i94: yes\np2.i94_number: 55555012345",
       conversation=conv("INTERVIEWER: What is your date of birth?", "APPLICANT: March 18, 1994.", "INTERVIEWER: March 18th, 1994. Do you have a Form I-94 arrival record number?", "APPLICANT: Yes, 55555012345.", "INTERVIEWER: 5 5 5 5 5, 0 1 2 3 4 5. Now your passport: the number, the country that issued it, and its expiration date.", "APPLICANT: Actually, sorry, my birthday is March 8th, not the 18th."),
       said="Actually, sorry, my birthday is March 8th, not the 18th.", ctx=CTX_EN, volunteer=P2_VOL,
       expect="intent correction; p2.date_of_birth 1994-03-08; conflict null; asking q_p2_passport; reply reads 'March 8th, 1994' back then asks the passport"),
  dict(id="r7.10a-es-reads-english-no", locale="es", agenda=agenda("es_reads_en", "es_contact"),
       known=KF_43 + "\np2.has_sevis_number: no",
       conversation=conv("INTERVIEWER: La Parte 3 es su declaración y cómo USCIS puede comunicarse con usted. ¿Puede leer y entender inglés, y leyó y entendió usted mismo cada pregunta de esta solicitud? Si alguien le está interpretando, diga que no.", "APPLICANT: No, mi hija me está ayudando con el inglés."),
       said="No, mi hija me está ayudando con el inglés.", ctx=CTX_ES,
       expect="p3.reads_english no only (no interpreter name); asking q_p3_interpreter_language; reply says the form then asks for the interpreter in Part 4 and asks the language; nothing about signing; es plus en"),
  dict(id="r7.10b-es-interpreter-language", locale="es", agenda=agenda("es_interp_lang", "es_contact"),
       known=KF_43 + "\np2.has_sevis_number: no\np3.reads_english: no",
       conversation=conv("INTERVIEWER: Entonces el formulario pide los datos de la persona que le interpreta, en la Parte 4. ¿En qué idioma le interpretó esa persona las preguntas?", "APPLICANT: Español."),
       said="Español.", ctx=CTX_ES,
       expect="p3.interpreter_language 'Spanish' (the English name); asking q_p3_contact (the next agenda line); no mention of signing; es plus en"),
]


def render(r: dict) -> str:
    t = CFG["userPromptTemplate"]
    vals = {
        "form_code": "I-765", "jurisdiction": "US-TX", "locale": r["locale"], "turn_id": "t-" + r["id"],
        "case_id": "case-receipts-1", "applicant_context": r.get("ctx", ""), "opening_questions": r.get("opening", "[no opening questions]"),
        "known_facts": r["known"], "agenda": r["agenda"], "section_boundary": r.get("boundary", ""),
        "volunteer_fields": r.get("volunteer", ""), "conversation": r["conversation"], "spoken_numerals": "",
        "user_input": r["said"],
    }
    out = re.sub(r"\{\{(\w+)\}\}", lambda m: vals.get(m.group(1), ""), t)
    assert "{{" not in out
    return out


def main():
    req = OUT / "requests"; req.mkdir(parents=True, exist_ok=True)
    (OUT / "system-prompt.txt").write_text(CFG["systemPrompt"])
    manifest = []
    for r in RECEIPTS:
        body = render(r)
        (req / f"{r['id']}.md").write_text(body)
        manifest.append({"id": r["id"], "locale": r["locale"], "expect": r["expect"]})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"{len(RECEIPTS)} requests in {req}")


if __name__ == "__main__":
    main()
