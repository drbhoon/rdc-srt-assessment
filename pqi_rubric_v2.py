"""SRT_Scoring_Rubric_v2 — the PQI evaluator's scoring rules after the 2026 pilot.

Why it exists. The seven-candidate pilot discriminated reasonably well but
systematically under-scored experienced managers: the evaluator compared their
answers with the comprehensive Model_Response and charged every missing
downstream step (owner, timeline, monitoring, CAPA) against the score. HR's
assessment-engine review (October 2026) set out a revised rubric; this module
is that rubric, written for the evaluator.

What it changes and what it does not. The 100 SRTs, their Model_Responses,
the competency structure, question selection and stored answers are untouched
— they still come from the master workbook. Only the scoring rules change:
one 0–9 level with new definitions in place of v1's Core 0–7 plus Excellence
Lift, a much narrower Critical Failure, prevention scored as AFI rather than
against the main score, and HR's pilot readiness bands on scale C1.

The master's own v1 rubric sheets (SRT_Scoring_Rubric_v1, AI_Scoring_Framework,
AI_Evaluation_Sequence, AI_Evaluator_Output_Schema, Scoring_Architecture,
Master_Metadata) are NOT sent with this rubric: they describe the v1 scheme
and would give the evaluator two conflicting rulebooks. Competency_Framework
and AFI_Rubric still are, verbatim.

A change to anything below that alters what a score means is a new rubric or
a new PROMPT_TEMPLATE — never a silent edit — so every stored evaluation stays
attributable to the rules it was given.
"""

RUBRIC_ID = "SRT_Scoring_Rubric_v2"
PROMPT_TEMPLATE = "pqi-evaluator-v2-1"

# Readiness on scale C1 (HR, October 2026). Pilot-calibrated, not permanent
# psychometric thresholds: to be revisited after validation on 40-60 people.
PILOT_BANDS = [
    {"lower": 0,  "upper": 49,  "label": "Not Yet Ready"},
    {"lower": 50, "upper": 64,  "label": "Developing"},
    {"lower": 65, "upper": 79,  "label": "Ready"},
    {"lower": 80, "upper": 100, "label": "High Readiness"},
]
PILOT_BANDS_LABEL = "Pilot bands P1 on scale C1 (HR, October 2026)"
PILOT_NOTE = ("Readiness bands are pilot-calibrated (HR, October 2026) and will be revisited after validation "
              "with independent expert ratings.")

# Why a level of 0-3 was given. Required for every such level: a short answer,
# absent prevention, a missing KPI or owner, or a longer model answer is not on
# this list, so it cannot be the reason.
LOW_SCORE_BASES = (
    "Missed the essential issue",
    "Materially deficient judgment",
    "Advocated an unsafe or wrong action",
    "No usable answer",
)
NOT_LOW = "Not applicable (level 4 or higher)"

ESSENTIAL = ("Yes", "Partial", "No")
CONFIDENCE = ("High", "Medium", "Low")

FINAL_PRINCIPLE = (
    "This assessment measures judgment, not verbosity. A competent PQI response is one that makes a sound, "
    "workable decision for the situation. Do not require the candidate to narrate an ideal SOP to demonstrate "
    "competence. Use additional validation, prevention, delegation and system-building evidence to distinguish "
    "good from excellent — not to turn a good answer into a poor one."
)

RUBRIC_TEXT = f"""# {RUBRIC_ID}

{FINAL_PRINCIPLE}

**Level 5 means a competent PQI could reasonably act on this answer.** It does not mean "half of an ideal answer". **Level 6 means strong experienced-manager judgment.**

## 1. The question for every SRT
Has the candidate recognised the material issue and demonstrated a workable, responsible PQI judgment or course of action?

Never ask "how many elements of the Model_Response did the candidate mention?". The Model_Response is one example of a very good response — an evaluator reference, not a checklist and not a script. A candidate may reach the same or a better decision through different reasoning, terminology, sequence or practical experience. Award credit for demonstrated judgment; do not reward similarity to the model answer. Ask: "Could an experienced, competent PQI reasonably take the candidate's proposed course of action?" If yes, do not give a low level because the answer is shorter than the model.

## 2. Competence is not completeness
An experienced PQI may answer briefly because the operational decision is obvious to them. A response does not need, in every SRT: immediate action, evidence, root cause, stakeholder communication, delegation, owner, timeline, preventive action, monitoring, effectiveness verification, commercial consequence and documentation. Require an element only where it is material to this specific SRT. Do not reduce an otherwise competent answer because it does not describe every desirable downstream action — those elements may separate good from excellent, but must never turn a good response into a weak one.

Example. The essential challenge is whether to commit to an unsupported two-hour slump retention, and the candidate says: "Our data supports 90 minutes only. Do not guarantee two hours. Test an admixture solution first." That is good PQI judgment. The absence of a trial timeline, a named owner, a customer communication script and a recurrence-prevention mechanism is not evidence of poor competence.

## 3. The 0–9 level (record one integer; 10 is never output)
- **0 — No evidence.** Blank, unintelligible, completely irrelevant, or no engagement with the situation.
- **1 — Fundamentally wrong.** Seriously misunderstands the central issue or gives a clearly inappropriate course of action.
- **2 — Materially deficient.** Recognises only a fragment of the issue, or proposes an action that would not reliably control the situation; may contain useful knowledge, but important risk remains uncontrolled.
- **3 — Partial judgment.** Recognises the central issue or gives a partially appropriate direction, but an important operational or technical gap remains; not yet reliable enough for independent PQI handling of this situation.
- **4 — Directionally correct.** Would probably handle the immediate issue reasonably, but with a significant gap in decision quality, validation, sequencing or practical execution. This is not a poor answer.
- **5 — Competent PQI response.** Understands the essential issue; gives a workable, responsible action or judgment; avoids important unsafe or unethical shortcuts; gives enough reasoning to show the decision is not accidental. Acceptable role competence. Does not need a complete SOP, CAPA system or monitoring architecture.
- **6 — Strong experienced-manager response.** Clearly sound and practical, showing mature judgment — typically some combination of good technical reasoning, practical sequencing, trade-off handling, use of evidence, coordination, commercial awareness and sensible contingency. Do not demand all of these.
- **7 — Very strong integrated judgment.** Unusually strong PQI judgment that integrates the most relevant technical, business, people or execution considerations; clearly better than normal competent handling.
- **8 — Excellent.** Strong judgment plus one or more genuinely differentiating features: quantified evidence, robust validation, thoughtful controlled delegation, strong technical-commercial integration, sustainable system improvement, excellent risk anticipation. Never for length.
- **9 — Exceptional.** Rare. Outstanding judgment, practical wisdom, integration and system-level thinking beyond what is normally expected of a good PQI. 9 must remain uncommon.

## 4. Brevity, length and words are not evidence
Response length is not evidence of competence. A concise answer may score 5, 6, 7 or higher if it shows the required judgment; a long answer may score 2, 3 or 4 if it avoids the core decision or reasons poorly. Never use word count, grammar, English fluency, sophistication of vocabulary, management jargon, the number of bullet points or similarity to the model answer as a positive signal. Do not reward words such as RCA, CAPA, monitoring, training, SOP, AI, dashboard, ownership or prevention unless the candidate shows what they mean in this situation.

## 5. Language and voice
Answers may be in English, Hindi, Hinglish or imperfect spoken English, typed or dictated. Ignore grammar, spelling, accent, transcription noise and fragmented spoken syntax wherever the intended meaning is reasonably clear. Score meaning and judgment, not linguistic quality. Never strengthen an answer by inventing meaning that is not reasonably present, and never credit an action because a knowledgeable PQI "would probably" do it.

## 6. Read each SRT through four lenses
Use the SRT's own Model_Response, AI_Evaluation_Anchors, Primary_Discriminator and Serious_Negative_or_Critical_Indicators to separate:
- **A. Essential judgment** — what a competent PQI must recognise or decide here. The most important dimension: if the candidate gets this materially right, the level is normally in the competent range (5+) even without further sophistication.
- **B. Supporting evidence** — reasoning, checks or implementation detail showing why the decision is right. Separates 4 from 5 and 5 from 6.
- **C. Excellence evidence** — quantified validation, strong cross-functional integration, controlled delegation, business-value reasoning, robust contingency, prevention or system design. Separates 6 to 9.
- **D. Danger signals** — actions that are actually unsafe, unethical, dishonest or materially unsound. These reduce the level substantially. An omitted best practice is not a danger signal.

Serious_Negative_or_Critical_Indicators lists both ordinary serious negatives and the rarer critical failures. Advocating an item on that list is a Serious_Negative; it is a Critical Failure only when it is one of the fundamentally unacceptable kinds in section 9.

## 7. Decisions
Where Decision_Applicable = Yes, a decision is still required, but read it sensibly. The candidate does not need to say "my decision is"; a decision may be plainly implied by the action. "Stop unloading and test the material before use" is a decision. "We need to check whether it meets acceptance criteria" is analysis unless the candidate also says what happens pending that check. Record Decision_Made = No only when the candidate genuinely avoids committing to a course of action in an SRT where one is essential; the application then caps the level at 6. Do not record No where the decision is plainly implicit.

## 8. The main level and the Anti-Firefighting Index measure different things
The main level asks: can this person handle the situation competently? AFI asks: does this person reduce the chance that the same problem needs firefighting again? Do not lower the main level because prevention was not mentioned; solving the immediate situation competently can still justify 5 or 6. The absence of recurrence prevention belongs in AFI.

Exception: prevention may materially affect the main level where prevention is itself central — C9 Complaint Resolution, RCA & Continuous Improvement; explicitly recurring problems; CAPA closure; chronic process failures; or an SRT whose Primary_Discriminator tests system or prevention behaviour. Even there, distinguish "competently dealt with the event" from "built a sustainable solution", and do not penalise the same omission twice.

AFI is scored separately, 0–5, with the AFI_Rubric below. Anti_Firefighting_Applicability = Yes: always score it. Conditional: activate it only where recurrence or system prevention is genuinely relevant to how the candidate handled the situation; otherwise set AFI_Activated = No and AFI_Evidence_Level = null. No: AFI_Activated = Not Applicable and AFI_Evidence_Level = null. AFI is never added to the main level or to Overall.

## Hero PQI
Watch for the experienced manager who personally solves everything ("I will personally check every load", "tell the customer to call me for every problem", "I will approve every adjustment", "I will visit all sites myself"). This can show technical confidence and responsiveness, so do not reduce a technically sound answer on a technical competency for it. Where delegation, capability building or system building is the competency demand — C3 Team Development & Delegation, the relevant C2 planning situations — it limits the level, and it limits AFI. Distinguish a technically capable manager from a scalable PQI who builds capability in others. For field-technician capability and deployment, reward train → assess → certify → deploy → monitor → refresh behaviour; the candidate need not name it.

## 9. Critical Failure — narrow
Reserve Critical_Failure = Yes for clear, affirmative advocacy of fundamentally unacceptable behaviour, for example: accepting a bribe or inducement; deliberately falsifying quality records; intentionally hiding a failing test result; knowingly supplying the wrong grade; deliberately allowing uncontrolled water addition despite understanding the risk; knowingly releasing seriously unacceptable concrete; deliberately fabricating calibration or test records; knowingly giving an unsupported technical guarantee where safety or quality is materially at risk.

These are NOT Critical Failures: incomplete CAPA; no effectiveness check; weak RCA; cost not quantified; generic delegation; poor documentation; missing owner or date; an incomplete model-answer element; an imperfect but plausible technical solution. They are ordinary scoring weaknesses.

A Critical Failure requires that the candidate clearly chooses or advocates the unacceptable action. Mentioning a bad alternative and rejecting it is not one. If the meaning is ambiguous, prefer ordinary scoring and an unresolved contradiction over a Critical Failure, unless the unacceptable choice is reasonably clear. When you record one, Critical_Failure_Statement must quote the candidate's exact words that chose or advocated the action — copied from the response, not paraphrased — and Critical_Failure_Reason says in one sentence why it is fundamentally unacceptable. The application caps the level at 2, and checks that the quoted words are in the response. Severity and type are fixed by the master for each SRT; do not assess them.

## 10. Contradictions
Where the candidate gives both safe and unsafe alternatives without resolving which they would actually choose, set Unresolved_Contradiction, do not credit the most favourable reading, and score the demonstrated ambiguity and risk. Do not manufacture contradictions from minor wording differences.

## 11. Before you record a level
1. Identify the central judgment demand of the SRT.
2. Read the answer for meaning; ignore grammar and transcription quality.
3. What did the candidate actually decide, recommend or understand?
4. Is that workable and responsible for a PQI?
5. Place it on 0–7 primarily on demonstrated competence.
6. Consider 8 or 9 only for truly differentiating evidence.
7. Check Serious_Negative and Critical_Failure.
8. Score AFI separately.
9. Brevity check: "Would I have scored this higher if the candidate had written the same judgment in 100 words instead of 25?" If yes, reconsider.
10. Model-answer check: "Am I reducing this answer because it did not reproduce model-answer points that were not essential to managing this situation?" If yes, reconsider.

Before you record any level from 0 to 3, confirm that the candidate genuinely missed the essential issue, gave materially deficient judgment, advocated an unsafe or wrong action, or gave no usable answer — and record which one as Low_Score_Basis. Never give 1–3 because the answer was short, prevention was absent, no KPI was mentioned, an owner or date was missing, or the model answer contained more steps.

## 12. What you record
- Raw_Level: the 0–9 integer (before the application's caps).
- Decision_Made: Yes / No / Not Applicable (Not Applicable only where Decision_Applicable = No).
- Essential_Judgment_Demonstrated: Yes / Partial / No.
- Key_Evidence: at most three concise points from the response that justify the level.
- Material_Gap: the single most important gap — what most stands between this response and the next level. Required even for strong answers; naming a gap is not a reason to lower the level.
- Low_Score_Basis: for levels 0–3, one of the four reasons above; otherwise "{NOT_LOW}".
- Serious_Negative: Yes / No.
- Critical_Failure, Critical_Failure_Statement, Critical_Failure_Reason (both blank when No).
- Unresolved_Contradiction.
- AFI_Activated, AFI_Evidence_Level (0–5 or null), AFI_Evidence (one concise statement).
- Evaluation_Confidence: High / Medium / Low — how confident you are in your reading of the response. Low triggers a second review.
Do not write an essay for each SRT.

The candidate's name, designation, experience, time taken and the length of the answer are not given to you and must play no part in the level. Do not curve or anchor towards any expected result for any person or group.
"""

# The report writer's additional rules under v2 (Candidate_Output_Spec still applies).
REPORT_GUIDANCE = """Rubric v2 report rules (HR, October 2026):
- Keep three things apart: role readiness (the readiness band), development opportunities (Development Priorities) and critical risk (confirmed Critical Flags only). A candidate can be Ready while still having meaningful development priorities.
- Do not imply that a candidate lacks role capability because AFI, or one competency, is weaker. Prefer "Ready, with development priority in delegation and closed-loop prevention" over "Not ready for independent PQI deployment" wherever the overall demonstrated capability is solid.
- Questions not answered score zero in the official result. When Completion shows unanswered questions, say plainly that the result reflects an incomplete assessment as well as the answers given; the answered-question figure is diagnostic only and must not be presented as the candidate's score.
- Readiness bands are pilot-calibrated; do not describe them as permanent thresholds. The band cut-offs and the "/10" competency profile in Candidate_Output_Spec below describe the earlier scheme: under this rubric, readiness uses HR's pilot bands on scale C1 and every score is given to you already computed — quote them as given."""
