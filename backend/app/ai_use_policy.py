"""Deterministic English recognition of instructor AI-use statements (EC-015).

Pure text rules, no model call. A statement is a ban, a limited permission or a
permission. AI named as a course *topic* ("the AI lab", "no AI background is
required") is not a statement about using AI tools. This is not a complete
interpreter of institutional policy: unrecognized wording yields no statement.
"""

import re

PROHIBITED = 'ai_generated_work_prohibited'
LIMITED = 'ai_use_limited'
PERMITTED = 'ai_use_permitted'
AI_RESTRICTIONS = (PROHIBITED, LIMITED)

_TYPOGRAPHY = str.maketrans({'’': "'", '‘': "'", '“': '"', '”': '"', '–': '-', '—': ' - '})

_COMPOUND = r"AI[- ](?:generated|assisted|written|produced|created|authored)"
_PRODUCT = r"chat\s?GPT|GPT(?:-?\d[\w.]*)?|(?:GitHub\s+)?Copilot|Claude|Gemini|LLMs?|(?:large\s+)?language\s+models?|chat\s?bots?"
# A subject of study, not a tool being used.
_TOPIC = (r"(?!\s+(?:labs?|laborator(?:y|ies)|courses?|class(?:es)?|lectures?|research(?:ers?)?|topics?|concepts?|background|"
          r"knowledge|experience|expertise|prerequisites?|fields?|ethics|history|theory|algorithms?|techniques?|methods?|"
          r"majors?|minors?|degrees?|clubs?|careers?|industry|engineers?|engineering|literacy|skills|curriculum|textbooks?|"
          r"books?|safety|polic(?:y|ies)|guidelines?|statement|section|component|unit|module|chapters?)\b)")
AI = (r"(?<![\w-])(?:generative\s+(?:AI|artificial\s+intelligence)|gen\s?AI|" + _COMPOUND +
      r"|AI|artificial\s+intelligence|" + _PRODUCT + r")(?![\w])" + _TOPIC)

# Words that may sit between an AI subject and its predicate without changing the subject.
_FILL = (r"(?:tools?|use|usage|uses|using|used|assistance|help|aid|support|generated|produced|written|created|work|answers?|"
         r"solutions?|responses?|code|content|text|writing|outputs?|materials?|to|generate|of|for|in|on|during|with|by|from|as|"
         r"any|all|this|these|those|such|similar|other|others|or|and|the|a|an|your|their|graded|course|class|coursework|"
         r"assignments?|homeworks?|hw|exams?|examinations?|quizzes|quiz|tests?|midterms?|finals?|projects?|labs?|essays?|"
         r"papers?|reports?|submissions?|like|including|platforms?|systems?|technolog(?:y|ies)|applications?|apps?|"
         r"assistants?|software|programs?|services?|products?|models?|chatbots?|forms?|kinds?|types?|whatsoever|at|purposes?|"
         r"copy|paste|" + AI + r")")
_SUBJECT = AI + r"(?:\s+" + _FILL + r"){0,10}?\s+"
_AUX = (r"(?:(?:is|are|was|were|be|being|remains?|will\s+be|shall\s+be|should\s+be|must\s+be|will\s+remain)\s+)?"
        r"(?:(?:strictly|absolutely|expressly|explicitly|completely|entirely|generally|always|also|therefore|thus)\s+)*")
_NOT = r"(?:not|never|no\s+longer)"
_BAN_WORD = (_NOT + r"\s+(?:be\s+)?(?:permitted|allowed|acceptable|authorized|accepted|tolerated|permissible|an\s+option|"
             r"to\s+be\s+used|appropriate|ok|okay|used)|prohibited|forbidden|banned|disallowed|unauthorized|impermissible|"
             r"off[- ]limits|unacceptable")
_MODAL_NOT = r"(?:(?:must|may|should|shall|can|could|do|does)\s*(?:not|n't)|cannot|mustn'?t|shouldn'?t|can'?t|don'?t)"
_WORK_VERB = r"(?:be\s+(?:used|consulted|employed|utili[sz]ed|submitted|relied\s+(?:up)?on)|generate|write|produce|complete|do|answer|solve|create|draft)"
_DENY = (r"(?:" + _NOT + r"\s+(?:be\s+)?(?:permitted|allowed|authorized|supposed)\s+to|"
         r"(?:prohibited|forbidden|barred|banned|prevented|restricted)\s+from|must\s+not|may\s+not|do\s+not|don'?t|cannot|"
         r"can\s+not|can'?t|should\s+not|shouldn'?t|shall\s+not|mustn'?t|(?:are|is)\s+not\s+to|never|refrain\s+from|avoid)")
_USE = (r"(?:use|using|utili[sz]e|utili[sz]ing|employ|employing|rely(?:ing)?\s+(?:up)?on|consult|consulting|submit|submitting|"
        r"turn(?:ing)?\s+in|generate|generating|copy|copying|paste|pasting|have(?!\s+to\b)|having|let|letting|ask|asking)")
_OBJECT = r"(?:\s+[\w'/-]+){0,6}?\s+" + AI
# AI in an acting role: used, submitted, or named as a tool/product.
_ACT = (r"(?:(?:use|using|usage|used|utili[sz]\w+|submitting|submit\w*|relying|reliance|copying)\s+(?:of\s+|on\s+|from\s+)?"
        r"(?:[\w'/-]+\s+){0,4}?" + AI + r"|" + AI + r"\s+(?:tools?|use|usage|assistance|help|outputs?|assistants?|chatbots?|"
        r"software|programs?|services?|platforms?|systems?|technolog\w+|generated|written|produced)"
        r"|(?<![\w-])(?:" + _COMPOUND + r"|" + _PRODUCT + r")(?![\w]))")
_OFFENSE = (r"(?:cheating|academic\s+(?:dishonesty|misconduct)|scholastic\s+dishonesty|plagiari[sz]m|collusion|"
            r"(?:academic\s+)?integrity\s+violation|violation\s+of\b|misconduct|unauthorized\s+(?:assistance|aid|help))")

# The object of "permits ..." / "prohibits ...": "the use of AI", "students to use ChatGPT", "students from using AI".
_GRANT_OBJECT = (r"(?:(?:students|you|learners)\s+(?:to\s+use|from\s+using)\s+|(?:the\s+|any\s+|all\s+)?(?:(?:cited|responsible|ethical|"
                 r"appropriate|open|free|thoughtful|limited)\s+)?(?:use|usage)\s+of\s+)(?:[\w'/-]+\s+){0,4}?" + AI)

_BANS = [re.compile(pattern, re.I) for pattern in (
    # "AI tools are not allowed", "ChatGPT is banned", "AI may not be used".
    _SUBJECT + r"(?:" + _AUX + r"(?:" + _BAN_WORD + r")|" + _MODAL_NOT + r"\s+(?:ever\s+)?" + _WORK_VERB + r")",
    # "Do not use AI", "not allowed to use ChatGPT", "prohibited from using AI".
    _DENY + r"\s+(?:(?:ever|simply|just|directly|even)\s+)?" + _USE + _OBJECT,
    # "This course prohibits the use of AI", "The instructor does not permit students to use ChatGPT".
    r"\b(?:prohibits?|forbids?|bans?|disallows?|(?:does|do|will|would)\s+not\s+(?:permit|allow|accept|tolerate|authori[sz]e)|"
    r"doesn'?t\s+(?:permit|allow|accept))\s+" + _GRANT_OBJECT,
    # "Do not copy, share, or use AI".
    _DENY + r"\b[^.;:]{0,60}?\b(?:or|nor)\s+" + _USE + _OBJECT,
    # "No AI.", "No AI-generated solutions", "No use of ChatGPT on exams".
    r"\bno\s+(?:(?:use|usage)\s+of\s+)?(?:any\s+)?" + AI +
    r"(?:\s+(?:tools?|use|usage|assistance|help|solutions?|answers?|work|code|content|chatbots?|assistants?|outputs?))?"
    r"\s*(?:$|[:)\-]|(?:is|are|allowed|permitted|on|for|in|during|when|while|or|and|at|no|may|can|should|will|whatsoever)\b)",
    # "must be completed without the use of AI".
    r"\b(?:must|should|shall|needs?\s+to|(?:are|is)\s+to|required\s+to|expected\s+to|ha(?:ve|s)\s+to)\b[^.;]{0,80}?\bwithout\s+"
    r"(?:the\s+)?(?:(?:use|help|aid|assistance|support|involvement)\s+(?:of|from)\s+|using\s+|relying\s+on\s+|consulting\s+)?"
    r"(?:any\s+)?(?:" + _FILL + r"\s+){0,3}?" + AI,
    # "Using ChatGPT is considered academic dishonesty", "AI-generated code will be treated as cheating".
    _ACT + r"\b[^.;]{0,80}?\b(?:(?:considered|treated|regarded|deemed|viewed|seen|handled|classified|reported)\s+(?:as\s+)?|"
    r"constitutes?\s+|counts?\s+as\s+|qualif(?:y|ies)\s+as\s+|amounts?\s+to\s+|(?:is|are|will\s+be|would\s+be)\s+)"
    r"(?:(?:a|an|the)\s+)?(?:(?:form|act|case|type|kind|instance)\s+of\s+)?" + _OFFENSE,
    # "Cheating includes using ChatGPT".
    r"\b(?:cheating|academic\s+(?:dishonesty|misconduct)|plagiari[sz]m)\b[^.;]{0,40}?\b(?:includes?|including|such\s+as|to\s+use|"
    r"by\s+using|is\s+using)\b[^.;]{0,30}?" + AI,
    # "Use of AI will result in a zero", "AI-generated work will be reported".
    _ACT + r"\b[^.;]{0,100}?\b(?:will|shall|may|can|could|would)\s+(?:automatically\s+)?(?:(?:result\s+in|receive|be\s+given|earn|"
    r"get|lead\s+to|be\s+subject\s+to|incur|be\s+assigned)\b[^.;]{0,60}?\b(?:zero|0\b|no\s+credit|failing|fail\w*|penalt\w+|"
    r"disciplin\w+|sanction\w*|referr\w+|integrity|misconduct)|be\s+(?:reported|referred|penali[sz]ed)\b|fail\b)",
)]
_ALLOW_WORD = r"(?:allowed|permitted|acceptable|encouraged|welcomed?|fine|okay|ok|authorized|permissible|approved)"
_ALLOWS = [re.compile(pattern, re.I) for pattern in (
    _SUBJECT + r"(?:" + _AUX + _ALLOW_WORD + r"\b|(?:may|can|could)\s+(?:also\s+|only\s+)?be\s+(?:used|consulted|employed|utili[sz]ed))",
    r"\b(?:may|can|could|(?:are|is|be)\s+(?:allowed|permitted|welcome|free|encouraged|authorized)\s+to|feel\s+free\s+to)\s+"
    r"(?:(?:also|freely|still|certainly|only)\s+)?" + _USE + _OBJECT,
    # "This course permits the use of Generative AI as a resource for completing assignments".
    r"\b(?:permits?|allows?|encourages?|welcomes?|authori[sz]es?)\s+" + _GRANT_OBJECT,
    AI + r"\b[^.;]{0,60}?\b" + _NOT + r"\s+(?:be\s+)?(?:prohibited|forbidden|restricted|banned|disallowed)\b",
    r"\bno\s+(?:restrictions?|prohibition|ban)\s+on\b[^.;]{0,40}?" + AI,
)]
_DOUBLE_NEGATIVE = re.compile(r"\b" + _NOT + r"\s+(?:be\s+)?(?:prohibited|forbidden|restricted|banned|disallowed)\b"
                              r"|\bno\s+(?:restrictions?|prohibition|ban)\s+on\b", re.I)
_NOT_AN_OFFENSE = re.compile(r"\b" + _NOT + r"\s+(?:be\s+)?(?:considered|treated|regarded|deemed|viewed|seen|cheating|plagiari[sz]m|"
                             r"a\s+violation|penali[sz]ed|reported)\b", re.I)
# A citation requirement is about how AI is acknowledged, not whether it may be used.
_CITATION_CONDITION = re.compile(
    r"\bwithout\s+(?:proper\s+|appropriate\s+|adequate\s+|full\s+|clear\s+|any\s+)?(?:citation|citing|attribution|acknowledg\w+|"
    r"disclos\w+|credit|documentation|references?)\b|\b(?:uncited|undisclosed|unacknowledged|unattributed)\b"
    r"|\bunless\s+(?:you\s+|it\s+is\s+|they\s+are\s+)?(?:cite|cited|acknowledge|acknowledged|disclose|disclosed|document|documented)\b", re.I)
_AUTHORIZATION = (r"\b(?:with|after|upon|given|subject\s+to)\s+(?:prior\s+|explicit\s+|express\s+|written\s+|my\s+|the\s+|"
                  r"instructor'?s?\s+|professor'?s?\s+)*(?:permission|approval|authorization|consent)\b"
                  r"|\b(?:if|when|where)\s+(?:it\s+is\s+|they\s+are\s+)?(?:explicitly\s+|expressly\s+|specifically\s+)?"
                  r"(?:permitted|authorized|approved|allowed|stated|instructed|noted|specified|indicated)\b"
                  r"|\b(?:as|in\s+ways|to\s+the\s+extent)\s+(?:explicitly\s+)?(?:permitted|authorized|allowed|approved|specified|directed|instructed)\s+(?:by|in)\b")
# Learning-support purposes: permission for these does not cover producing the assigned work.
_PURPOSE = (r"\b(?:to|for)\s+(?:help\s+(?:you\s+)?)?(?:brainstorm\w*|check\w*|proofread\w*|edit\w*|debug\w*|outlin\w*|study\w*|review\w*|"
            r"practice|practicing|learn\w*|understand\w*|explain\w*|clarif\w*|summari[sz]\w*|research\w*|ideas?|grammar|spelling|"
            r"translat\w*|format\w*|explor\w*)\b")
_LIMITED = re.compile(r"\b(?:only|solely|exclusively|limited\s+to|strictly\s+for|nothing\s+(?:more|else))\b|" + _PURPOSE + "|" + _AUTHORIZATION, re.I)
# "Unauthorized use", "unauthorized or unapproved use": the rule defers to what the instructor authorizes.
_QUALIFIED = (r"\b(?:unauthori[sz]ed|unapproved|unpermitted|non-?permitted|improper|inappropriate)\b(?:\s+(?:or\s+|and\s+)?[\w-]+){0,3}?"
              r"\s+(?:use|usage|uses|reliance)\b|\bwithout\s+(?:prior\s+|explicit\s+|express\s+|written\s+|the\s+|my\s+|"
              r"instructor'?s?\s+|professor'?s?\s+)*(?:permission|authorization|approval|consent)\b")
_DEFERS = re.compile(_QUALIFIED, re.I)
# "When AI use is authorized, students must ..." states a condition, not a permission.
_CONDITIONAL = re.compile(r"\b(?:when|if|where|whenever|once|unless|provided\s+that|in\s+cases\s+where)\b[^.;]{0,40}?" + AI, re.I)
# The policy itself says an instructor/assignment may authorize an exception.
_EXCEPTIONS = re.compile(
    r"\bunless\b[^.;]{0,100}?\b(?:permitted|allowed|authori[sz]\w+|approv\w+|permission|consent|stated|specified|noted|instructed|indicated|told|directed|otherwise)\b"
    r"|\bexcept\s+(?:where|when|as|if)\b[^.;]{0,80}?\b(?:permitted|allowed|authorized|approved|stated|specified|noted|instructed|indicated|directed)\b"
    r"|\botherwise\s+(?:stated|specified|noted|indicated|instructed)\b|" + _QUALIFIED + "|" + _AUTHORIZATION, re.I)
_PRONOUN_OBJECT = re.compile(r"\b(use|using|utili[sz]e|utili[sz]ing|employ|employing|rely(?:ing)?\s+(?:up)?on|consult|consulting|ask|asking|have|let)"
                             r"\s+(?:it|them|these\s+tools|those\s+tools|such\s+tools|this\s+technology|these\s+technologies)\b", re.I)
_MENTION = re.compile(AI, re.I)

_WORK_TYPES = {
    'programming': r"\b(?:programming|coding)\s+(?:assignments?|projects?|exercises?|tasks?)\b",
    'writing': r"\b(?:essays?|papers?|reports?|(?:writing|written)\s+assignments?)\b",
    'exam': r"\b(?:exams?|examinations?|midterms?|tests?(?!\s+cases?)|finals?(?!\s+(?:project|report|paper|presentation|essay|draft|submission|grade)))\b",
    'quiz': r"\bquiz(?:zes)?\b",
    'homework': r"\b(?:homeworks?|hw|problem\s+sets?|psets?)\b",
    'project': r"\bprojects?\b",
    'lab': r"\b(?:labs?|laborator(?:y|ies))\b",
    'discussion': r"\bdiscussions?\b",
}
_ANY_TYPE = "|".join(pattern[2:-2] if pattern.startswith(r"\b") and pattern.endswith(r"\b") else pattern for pattern in _WORK_TYPES.values())
# Only an explicit scoping phrase narrows a rule ("on exams", "to write your reports").
_SCOPED = re.compile(r"\b(?:on|in|for|during|(?:when|while)\s+(?:taking|completing|doing|writing|working\s+on)|"
                     r"to\s+(?:complete|do|write|take|finish|solve|answer))\s+(?:(?:any|all|the|your|these|those|graded|course|"
                     r"in-class|take-home|online|individual|and|or)\s+)*(?:" + _ANY_TYPE + r")(?:\s+(?:and|or)\s+(?:(?:the|your|any|all)\s+)?(?:"
                     + _ANY_TYPE + r"))*", re.I)
_ALL_WORK = re.compile(r"\b(?:assignments?|course\s*work|graded\s+work|(?:any|all|submitted|assessed|your)\s+work|"
                       r"(?:this|the)\s+(?:course|class)|assessments?|deliverables?|any\s+purpose|at\s+all|whatsoever|"
                       r"in\s+any\s+(?:form|way))\b", re.I)


def _normalize(text):
    text = text.translate(_TYPOGRAPHY)
    text = re.sub(r"\bA\.I\.?(?=\W|$)", "AI", text)
    return re.sub(r"\b(e\.g|i\.e|etc|vs)\.", lambda match: match[1].replace(".", ""), text, flags=re.I)


def mentions_ai(text):
    """True when the text names an AI tool in a usage sense (not AI as a course topic)."""
    return bool(_MENTION.search(_normalize(text)))


def work_types(text):
    """Kinds of course work named in a title or phrase, e.g. {'homework'} for 'HW6_questions.pdf'."""
    text = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])|[_\-.]+", " ", text or "")
    return {kind for kind, pattern in _WORK_TYPES.items() if re.search(pattern, text, re.I)}


def _scope(sentence):
    """Work types a statement is limited to; empty means all course work."""
    phrases = [match[0] for match in _SCOPED.finditer(sentence)]
    remainder = sentence
    for phrase in phrases:
        remainder = remainder.replace(phrase, " ")
    if not phrases or _ALL_WORK.search(remainder):
        return []
    return sorted(set().union(*(work_types(phrase) for phrase in phrases)))


def _clause_kind(clause):
    banned = any(pattern.search(clause) for pattern in _BANS) and not _DOUBLE_NEGATIVE.search(clause) and not _NOT_AN_OFFENSE.search(clause)
    if banned:
        # Penalizing only uncited/undisclosed use neither bans nor authorizes AI.
        return None if _CITATION_CONDITION.search(clause) and not _DEFERS.search(clause) else PROHIBITED
    if any(pattern.search(clause) for pattern in _ALLOWS) and not _CONDITIONAL.search(clause):
        return LIMITED if _LIMITED.search(clause) else PERMITTED
    return None


def ai_statements(sentence, *, carry=False):
    """Classify one sentence's AI-use statements.

    `carry` says the previous sentence named an AI tool, so "do not use it"
    refers to that tool. Returns dicts with `kind`, `allows_exceptions` (the
    policy itself permits authorized exceptions) and `work_types` (empty = all).
    """
    text = _normalize(sentence)
    parts = [re.sub(r"\([^)]*\)", " ", text)] + re.findall(r"\(([^)]*)\)", text)
    statements = []
    for part in parts:
        for piece in re.split(r"[.!?;]+(?:\s|$)|\n+", part):
            piece = re.sub(r",\s*(?:such as|including)\b.*?,\s*(?=(?:is|are|must|may|shall)\b)", " ", piece, flags=re.I)
            piece = re.sub(r"[,\s]+", " ", piece).strip()
            if not piece:
                continue
            named = carry
            for clause in re.split(r"\b(?:but|however|although|though|whereas|yet)\b", piece, flags=re.I):
                if named:
                    clause = _PRONOUN_OBJECT.sub(lambda match: match[1] + " AI", clause)
                named = named or bool(_MENTION.search(clause))
                kind = _clause_kind(clause) if _MENTION.search(clause) else None
                if kind:
                    statements.append({'kind': kind, 'work_types': _scope(piece),
                                       'allows_exceptions': kind in AI_RESTRICTIONS and bool(_EXCEPTIONS.search(piece))})
    return statements
