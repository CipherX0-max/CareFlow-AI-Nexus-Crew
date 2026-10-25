"""Deterministic, auditable text understanding: language detection, concept lexicon (English / Hindi /
Hinglish), negation + temporality, durations, vitals, age. Used (a) as the always-available baseline
extractor and (b) as a cross-check on the LLM. It never infers facts that are not in the text.

Limitations (documented in README): keyword lexicon, not NLP; negation scope is windowed; coverage is
limited to the concepts below. Anything outside it is left to the LLM and flagged for staff review."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------- language ---------------------------
SCRIPT_BLOCKS = {
    "hi": (0x0900, 0x097F), "bn": (0x0980, 0x09FF), "pa": (0x0A00, 0x0A7F), "gu": (0x0A80, 0x0AFF),
    "or": (0x0B00, 0x0B7F), "ta": (0x0B80, 0x0BFF), "te": (0x0C00, 0x0C7F), "kn": (0x0C80, 0x0CFF),
    "ml": (0x0D00, 0x0D7F), "ur": (0x0600, 0x06FF),
}
HINGLISH_MARKERS = set("""mujhe mujhko mere meri mera hai hain hu hoon ho hota hoti rahi raha rahe nahi nahin nhi dard bukhar bukhaar
seene sine chhati chaati mein me se ko ka ki ke pet sir sar saans din dino kal aaj bahut bohot thoda thodi abhi pehle pichle
lekin par magar aur kya tha thi the lag lagta lagti ho gaya gayi ghante hafte mahine saal umar dawai goli takleef taklif
chakkar ulti dast kamzori behosh achanak tez kam jyada zyada subah raat shaam mujhe""".split())
SUPPORTED_LANGS = {"en", "hi", "hi-Latn"}


def detect_language(text: str) -> tuple[str, str]:
    """Returns (language_code, support) where support is 'supported' | 'limited'."""
    counts: dict[str, int] = {}
    letters = 0
    for ch in text:
        if ch.isalpha():
            letters += 1
            cp = ord(ch)
            for code, (lo, hi) in SCRIPT_BLOCKS.items():
                if lo <= cp <= hi:
                    counts[code] = counts.get(code, 0) + 1
                    break
    if letters and counts:
        code, n = max(counts.items(), key=lambda kv: kv[1])
        if n / letters > 0.3:
            return code, ("supported" if code in SUPPORTED_LANGS else "limited")
    toks = re.findall(r"[a-z]+", text.lower())
    if toks:
        hits = sum(1 for t in toks if t in HINGLISH_MARKERS)
        if hits >= 2 and hits / len(toks) >= 0.15:
            return "hi-Latn", "supported"
    return "en", "supported"


# ---------------------------------------------------------------- concept lexicon --------------------
# canonical concept -> (category, label, [synonyms]) ; Devanagari + romanised Hindi included.
C = {}


def _c(key, category, label, *syns):
    C[key] = (category, label, list(syns))


_c("chest_pain", "symptom", "Chest pain / pressure",
   "chest pain", "pain in (?:my |the )?chest", "chest pressure", "chest tightness", "tightness in (?:my |the )?chest",
   "chest heaviness", "heaviness in (?:my |the )?chest", "chest discomfort", "discomfort in (?:my |the )?chest",
   "uncomfortable in (?:my |the )?chest", "seene (?:me|mein|main) (?:dard|bhaari|bhaaripan|jakdan|jalan)",
   "sine (?:me|mein) dard", "chh?aati (?:me|mein) dard", "chhati (?:me|mein) dard", "सीने में (?:दर्द|जकड़न|भारीपन)",
   "छाती में (?:दर्द|जकड़न|भारीपन)", "सीने का दर्द", "सीने में तकलीफ")
_c("shortness_of_breath", "symptom", "Shortness of breath",
   "short(?:ness)? of breath", "breathless(?:ness)?", "difficulty (?:in )?breathing", "trouble breathing", "breathing difficulty",
   "(?:can ?not|can't|cannot|unable to) breathe", "saans (?:phool\\w*|lene (?:me|mein) (?:taklif|takleef|dikkat|problem)|ki (?:taklif|takleef|dikkat)|nahi aa rahi|nahin aa rahi|nhi aa rahi)",
   "dam phool\\w*", "सांस (?:फूल\\w*|लेने में (?:तकलीफ|दिक्कत|परेशानी)|की (?:तकलीफ|दिक्कत)|नहीं आ रही)", "साँस (?:फूल\\w*|लेने में (?:तकलीफ|दिक्कत))")
_c("fever", "symptom", "Fever", "fever", "feverish", "high temperature", "(?:tez |bahut )?bukh[a]+r", "बुखार", "ज्वर")
_c("headache", "symptom", "Headache", "head ?ache", "pain in (?:my |the )?head", "(?:sir|sar) (?:me |mein )?dard", "सिर ?दर्द", "सिर में दर्द", "सर दर्द")
_c("cough", "symptom", "Cough", "cough(?:ing)?", "khansi", "खांसी", "खाँसी")
_c("abdominal_pain", "symptom", "Abdominal pain", "(?:stomach|abdominal|belly|tummy) (?:pain|ache)", "pain in (?:my |the )?(?:stomach|abdomen|belly)",
   "pet (?:me |mein )?dard", "पेट (?:में )?दर्द")
_c("vomiting", "symptom", "Vomiting", "vomit(?:ing|ed|s)?", "throwing up", "threw up", "ulti(?:yan)?", "उल्टी")
_c("nausea", "symptom", "Nausea", "nausea(?:ted)?", "feel(?:ing)? sick to (?:my )?stomach", "ji machl\\w*", "मतली", "जी मिचल\\w*")
_c("diarrhea", "symptom", "Diarrhoea", "diarrh?o?ea", "loose (?:motion|stool)s?", "dast", "दस्त")
_c("dizziness", "symptom", "Dizziness", "dizz(?:y|iness)", "light ?headed(?:ness)?", "chakkar", "चक्कर")
_c("syncope", "symptom", "Fainting / loss of consciousness", "faint(?:ed|ing)?", "passed out", "blacked out", "lost consciousness",
   "behosh(?:i)?", "बेहोश(?:ी)?")
_c("palpitations", "symptom", "Palpitations", "palpitations?", "heart (?:is )?racing", "racing heart", "dhadkan (?:tez|badh\\w*)", "धड़कन (?:तेज|बढ़\\w*)")
_c("weakness", "symptom", "General weakness", "weakness", "feeling weak", "kamzori", "कमजोरी")
_c("unilateral_weakness", "symptom", "One-sided weakness / numbness",
   "(?:weakness|numbness) (?:on|in) one side", "one[- ]sided (?:weakness|numbness)", "(?:arm|leg|hand) (?:is )?(?:weak|numb)",
   "half (?:of )?(?:my )?body (?:is )?(?:weak|numb)", "ek taraf (?:ki )?kamzori", "आधे शरीर में कमजोरी")
_c("facial_droop", "symptom", "Facial droop", "facial droop", "face (?:is )?droop\\w*", "drooping face", "mouth (?:is )?drooping", "chehra (?:ek taraf )?tedha", "चेहरा टेढ़ा")
_c("slurred_speech", "symptom", "Slurred / difficult speech", "slurred speech", "slurring", "difficulty speaking", "trouble speaking",
   "(?:cannot|can't|unable to) speak", "bolne (?:me|mein) (?:dikkat|taklif)", "ज़ुबान लड़खड़ा\\w*", "बोलने में (?:दिक्कत|तकलीफ)")
_c("severe_bleeding", "symptom", "Heavy bleeding", "heavy bleeding", "severe bleeding", "bleeding (?:a lot|heavily|profusely)", "bahut (?:zyada |jyada )?khoon", "बहुत (?:ज्यादा )?खून")
_c("blood_in_vomit_or_cough", "symptom", "Blood in vomit / cough", "(?:cough(?:ing|ed)?(?: up)?|vomit(?:ing|ed)?(?: up)?) blood", "blood in (?:my )?(?:vomit|sputum|cough)",
   "khoon ki ulti", "खून की उल्टी", "खांसी में खून")
_c("seizure", "symptom", "Seizure / fit", "seizures?", "convulsions?", "had a fit", "daura", "दौरा", "मिर्गी")
_c("confusion", "symptom", "Confusion", "confus(?:ed|ion)", "disoriented", "not making sense", "hosh nahi", "भ्रम")
_c("sweating", "symptom", "Sweating", "(?:cold )?sweat(?:ing|y)", "pasina", "पसीना")
_c("radiating_arm_pain", "symptom", "Pain radiating to arm / jaw", "(?:(?<!chest )(?<!back )(?<!abdominal )(?<!stomach )pain )?(?:radiating|spreading|going) (?:to|into) (?:the |my )?(?:left )?(?:arm|jaw|shoulder)",
   "left arm pain", "pain in (?:my |the )?left arm", "baayen haath (?:me|mein) dard", "बाएं हाथ में दर्द")
_c("sore_throat", "symptom", "Sore throat", "sore throat", "throat pain", "gale (?:me|mein) (?:dard|kharash)", "गले में (?:दर्द|खराश)")
_c("back_pain", "symptom", "Back pain", "back ?(?:pain|ache)", "pain in (?:my |the )?back", "kamar (?:me |mein )?dard", "कमर दर्द", "कमर में दर्द")
_c("rash", "symptom", "Skin rash", "rash", "hives", "daane", "दाने")
_c("urinary_symptoms", "symptom", "Painful / frequent urination", "burning (?:while |on )?urinat\\w*", "painful urination", "peshab (?:me|mein) jalan", "पेशाब में जलन")
_c("leg_swelling", "symptom", "Leg swelling", "(?:leg|ankle|feet) swelling", "swollen (?:legs?|ankles?|feet)", "pair(?:on)? (?:me|mein) sujan", "पैरों में सूजन")
_c("vision_change", "symptom", "Vision change", "blurred vision", "(?:double|loss of) vision", "vision (?:loss|problem)", "dhundhla dikh\\w*", "धुंधला दिख\\w*")
_c("injury", "symptom", "Recent injury / fall", "(?:had a |took a )?fall(?:en)?|fell (?:down|off)", "accident", "head injury", "injur(?:y|ed)", "chot", "चोट")
_c("pregnancy", "context", "Pregnancy", "pregnan(?:t|cy)", "garbh\\w*", "गर्भवती", "प्रेग्नेंट")
_c("diabetes", "history", "Diabetes", "diabet(?:es|ic)", "sugar (?:ki )?(?:bimari|problem)", "मधुमेह", "डायबिटीज़?")
_c("hypertension", "history", "Hypertension", "hypertension", "high blood pressure", "bp (?:ki )?(?:bimari|problem)", "उच्च रक्तचाप", "हाई बीपी")
_c("heart_disease", "history", "Heart disease", "heart (?:disease|attack|problem|condition)", "cardiac (?:disease|history)", "angioplasty", "bypass surgery", "दिल की बीमारी", "हार्ट अटैक")
_c("asthma_copd", "history", "Asthma / COPD", "asthma", "copd", "दमा", "अस्थमा")
_c("kidney_disease", "history", "Kidney disease", "kidney (?:disease|failure|problem)", "dialysis", "किडनी (?:की )?(?:बीमारी|खराब)")
_c("stroke_history", "history", "Previous stroke", "previous stroke", "had a stroke", "stroke history", "लकवा")
_c("cancer", "history", "Cancer", "cancer", "chemotherapy", "कैंसर")

STROKE_LIKE = ("facial_droop", "slurred_speech", "unilateral_weakness")

_BOUND_L = r"(?<![\w\u0900-\u09FF])"
_BOUND_R = r"(?![\w\u0900-\u09FF])"
_CONCEPT_RX = {
    k: re.compile(_BOUND_L + "(?:" + "|".join(sorted(v[2], key=len, reverse=True)) + ")" + _BOUND_R, re.I)
    for k, v in C.items()
}

# ---------------------------------------------------------------- negation / uncertainty / time ------
NEG_EN = r"(?:no|not|never|without|denies|denied|deny|none|neither|nor|free of|absence of|negative for|haven'?t|hasn'?t|don'?t|doesn'?t|didn'?t|isn'?t|aren'?t|wasn'?t|weren'?t|can'?t|cannot)"
NEG_HI = r"(?:nahi|nahin|nahi|nhi|nai|mat|bina|bagair|na hi|नहीं|नही|बिना|बगैर|नाही|নেই|নাই|না)"
NEG_RX = re.compile(_BOUND_L + "(?:" + NEG_EN + "|" + NEG_HI + ")" + _BOUND_R, re.I)
UNCERTAIN_RX = re.compile(
    _BOUND_L + r"(?:maybe|perhaps|might|possibly|probably|not (?:really )?sure|i think|i guess|unsure|kind of|sort of|shayad|pata nahi|pata nhi|kya pata|lagta hai|"
    r"शायद|पता नहीं|लगता है)" + _BOUND_R, re.I)
RESOLVED_RX = re.compile(
    _BOUND_L + r"(?:gone|resolved|went away|disappeared|settled|better now|no longer|not anymore|stopped|subsided|theek ho gaya|theek ho gayi|"
    r"ab theek|khatam ho gaya|khatam ho gayi|ठीक हो गया|ठीक हो गई|खत्म हो गया|अब ठीक)" + _BOUND_R, re.I)
PAST_RX = re.compile(
    _BOUND_L + r"(?:yesterday|last (?:night|week|month|year)|earlier|before|previously|used to|pehle|pichle|kal|purana|"
    r"पहले|कल|पिछले)" + _BOUND_R, re.I)
CURRENT_RX = re.compile(
    _BOUND_L + r"(?:now|right now|currently|still|at the moment|today|abhi|aaj|ab|अभी|आज|अब|since|for the (?:last|past)|from)" + _BOUND_R, re.I)
CONTRAST_SPLIT = re.compile(
    r"(?<=[.;!?\n।॥])\s+|\s+(?:but|however|although|though|lekin|par|magar|parantu|लेकिन|परंतु|मगर)\s+|(?:(?<!\d)\.(?!\d)|[;!?\n।॥])+", re.I)
_TOKEN_RX = re.compile(r"[^\s,;:]+|[,;:]")
_COORD = {"and", "or", "nor", "aur", "ya", "और", "या", "tatha", "तथा", "evam", "एवं", "bhi", "भी"}
_STOP_AFTER = {"and", "aur", "और", "ya", "या", "but", "lekin", "par", "magar", "लेकिन", "while", "also"}

NUM_WORDS = {"ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "che": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
             "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1,
             "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10}
UNIT_WORDS = {"hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour", "ghante": "hour", "ghanta": "hour", "घंटे": "hour", "घंटा": "hour",
              "day": "day", "days": "day", "din": "day", "dino": "day", "दिन": "day", "week": "week", "weeks": "week", "hafte": "week", "hafta": "week", "हफ्ते": "week", "सप्ताह": "week",
              "month": "month", "months": "month", "mahine": "month", "mahina": "month", "महीने": "month", "महीना": "month",
              "year": "year", "years": "year", "saal": "year", "sal": "year", "साल": "year", "वर्ष": "year",
              "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute", "minat": "minute", "मिनट": "minute"}
_NUMTOK = r"(\d+(?:\.\d+)?|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")"
_UNITTOK = r"(" + "|".join(sorted(UNIT_WORDS, key=len, reverse=True)) + r")"
DURATION_RX = re.compile(
    _BOUND_L + r"(?:for (?:the )?(?:last |past |about |around |almost )?|since (?:last |past )?)?" + _NUMTOK + r"\s*(?:\+\s*)?" + _UNITTOK + r"(?:\s*(?:se|ago|से))?" + _BOUND_R, re.I)
SINCE_RX = re.compile(_BOUND_L + r"(since (?:yesterday|this morning|last night|morning|evening|last week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                      r"(?:kal|subah|raat|parso|kal raat|aaj subah) se|कल से|सुबह से|रात से|परसों से)" + _BOUND_R, re.I)
SUDDEN_RX = re.compile(_BOUND_L + r"(suddenly|all of a sudden|sudden(?:ly)? onset|achanak|अचानक)" + _BOUND_R, re.I)
GRADUAL_RX = re.compile(_BOUND_L + r"(gradually|slowly|dheere dheere|धीरे धीरे|धीरे-धीरे)" + _BOUND_R, re.I)
FREQ_RX = re.compile(_BOUND_L + r"(on and off|comes and goes|come and go|every (?:day|night|hour|week)|daily|twice a day|repeatedly|recurring|again and again|"
                     r"several times|baar baar|bar bar|roz|रोज|बार बार|बार-बार|\d+ times (?:a|per) (?:day|week|hour))" + _BOUND_R, re.I)
SEV_WORD_RX = re.compile(_BOUND_L + r"(severe(?:ly)?|very (?:bad|painful|strong)|unbearable|excruciating|worst|intense|moderate|mild|slight|"
                         r"bahut (?:tez|zyada|jyada)|bohot tez|tez|halka|halki|थोड़ा|बहुत तेज|तेज़?|हल्का)" + _BOUND_R, re.I)
SEV_NUM_RX = re.compile(r"(\d{1,2})\s*(?:/|out of)\s*10")
WORSE_RX = re.compile(_BOUND_L + r"(getting worse|gets worse|worsening|worse|increasing|more and more|badh raha|badhta ja raha|badh rahi|बढ़ रहा|बढ़ता जा रहा|बदतर)" + _BOUND_R, re.I)
RAPID_RX = re.compile(_BOUND_L + r"(rapidly|quickly|fast|very fast|तेजी से|jaldi se|tezi se)" + _BOUND_R, re.I)
BETTER_RX = re.compile(_BOUND_L + r"(getting better|improving|improved|better|kam ho raha|kam ho rahi|कम हो रहा|सुधार)" + _BOUND_R, re.I)
SAME_RX = re.compile(_BOUND_L + r"(same|unchanged|not changed|stable|waisa hi|वैसा ही)" + _BOUND_R, re.I)


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def negation_count(text: str) -> int:
    """Number of negation cues (used to detect translation / ASR errors that drop or add a negation)."""
    return len(NEG_RX.findall(UNCERTAIN_RX.sub(" ", text)))


def _num(tok: str) -> Optional[float]:
    tok = tok.lower()
    if tok in NUM_WORDS:
        return float(NUM_WORDS[tok])
    try:
        return float(tok)
    except ValueError:
        return None


def parse_duration(text: str) -> Optional[str]:
    m = DURATION_RX.search(text)
    if m:
        n, unit = _num(m.group(1)), UNIT_WORDS.get(m.group(2).lower())
        if n is not None and unit:
            n_s = str(int(n)) if n == int(n) else str(n)
            return f"{n_s} {unit}{'s' if n != 1 else ''}"
    m = SINCE_RX.search(text)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------- fact drafts ------------------------
@dataclass
class FactDraft:
    category: str
    concept: str
    label: str = ""
    polarity: str = "present"
    temporal: str = "current"
    value_text: Optional[str] = None
    value_num: Optional[float] = None
    value_num2: Optional[float] = None
    unit: Optional[str] = None
    onset: Optional[str] = None
    duration: Optional[str] = None
    frequency: Optional[str] = None
    severity: Optional[str] = None
    progression: Optional[str] = None
    origin: str = "explicit"
    excerpt: str = ""
    locator: Optional[str] = None
    confidence: Optional[float] = None
    method: str = "lexicon"
    flags: list = field(default_factory=list)
    span: tuple = (0, 0)  # offsets within the source text (for overlap/merge)
    subject: str = "patient"
    relationship_to_patient: str = "patient"
    assertion_status: str = "present"
    section_heading: Optional[str] = None
    page_number: Optional[int] = None
    location_in_source: Optional[str] = None
    event_date: Optional[str] = None
    evidence_refs: list = field(default_factory=list)

    def key(self) -> tuple:
        return (self.category, self.concept, self.subject, self.relationship_to_patient)


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    out = list(text)
    for a, b in spans:
        for i in range(a, b):
            out[i] = " "
    return "".join(out)


def _clauses(text: str) -> list[tuple[int, str]]:
    out, pos = [], 0
    for m in CONTRAST_SPLIT.finditer(text):
        if m.start() > pos:
            out.append((pos, text[pos:m.start()]))
        pos = m.end()
    if pos < len(text):
        out.append((pos, text[pos:]))
    return [(p, c) for p, c in out if c.strip()]


# ---------------------------------------------------------------- section & context tracking ----------
FAMILY_HEADING_RX = re.compile(
    r"^\s*(?:family\s+(?:medical\s+)?history(?:\s+of)?|f/?h/?o|fhx|hereditary\s+history)\s*[:\-]?(?:\s*(.*))?$",
    re.I
)
PERSONAL_HEADING_RX = re.compile(
    r"^\s*(?:personal\s+(?:medical\s+)?history|past\s+medical\s+history|pmh|medical\s+history|patient\s+history|chief\s+complaint|presenting\s+complaint|history\s+of\s+present\s+illness|hpi|symptoms|current\s+symptoms|clinical\s+notes)\s*[:\-]?(?:\s*(.*))?$",
    re.I
)
SUSPECTED_HEADING_RX = re.compile(
    r"^\s*(?:differential\s+diagnosis|ddx|impression|clinical\s+impression|provisional\s+diagnosis|suspected|working\s+diagnosis|to\s+rule\s+out|rule\s+out)\s*[:\-]?(?:\s*(.*))?$",
    re.I
)
EDUCATIONAL_HEADING_RX = re.compile(
    r"^\s*(?:educational\s+(?:information|note|material)|patient\s+(?:education|information|instructions)|general\s+information|reference\s+information|disclaimer|important\s+note|warning\s+signs|return\s+precautions|laboratory\s+note)\s*[:\-]?(?:\s*(.*))?$",
    re.I
)
AMBIGUOUS_HEADING_RX = re.compile(
    r"^\s*(?:[\?\*\[\]\#\_\~]{2,}|\[?(?:illegible|unclear|unreadable|corrupted|damaged|ambiguous)\]?|\w{1,2}\?+)\s*history(?:\s+of)?\s*[:\-]?(?:\s*(.*))?$|history\s*\((?:unclear|ambiguous|unknown)[^)]*\)",
    re.I
)
MEDS_PAST_HEADING_RX = re.compile(
    r"^\s*(?:past\s+medications?|discontinued\s+medications?|previous\s+medications?)\s*[:\-]?(?:\s*(.*))?$",
    re.I
)

FORM_YES_RX = re.compile(r"^\s*[:\-]\s*(?:yes|present|positive|pos|\+|ha|haan|हाँ|होय)\b", re.I)
FORM_NO_RX = re.compile(r"^\s*[:\-]\s*(?:no|absent|negative|neg|nil|none|none\s+known|\-|nahi|nahin|नहीं|नाही)\b", re.I)

FAMILY_MEMBER_TERMS = [
    ("mother", re.compile(r"\b(?:my\s+|patient'?s?\s+)?(?:mother|mom|mum|maternal\s+mother|maa|mata(?:ji)?|ammi|माँ|माता)\b", re.I)),
    ("father", re.compile(r"\b(?:my\s+|patient'?s?\s+)?(?:father|dad|paternal\s+father|papa|pita(?:ji)?|abba|पिता|पापा)\b", re.I)),
    ("sibling", re.compile(r"\b(?:my\s+|patient'?s?\s+)?(?:brother|sister|sibling|bhai|behen|bahan|भाई|बहन)\b", re.I)),
    ("child", re.compile(r"\b(?:my\s+|patient'?s?\s+)?(?:son|daughter|child|beta|beti|बेटा|बेटी)\b", re.I)),
    ("other_relative", re.compile(r"\b(?:my\s+|patient'?s?\s+)?(?:grandmother|grandfather|grandparent|grandma|grandpa|aunt|uncle|cousin|maternal\s+\w+|paternal\s+\w+|dadi|dada|nani|nana|chacha|mama|दादा|दादी|नाना|नानी)\b", re.I)),
    ("unknown", re.compile(r"\b(?:family\s+(?:medical\s+)?history(?:\s+of)?|runs?\s+in\s+(?:my\s+|the\s+)?family|in\s+(?:my\s+|the\s+)?family|hereditary|khandan\s+mein|parivar\s+mein|परिवार\s+में)\b", re.I)),
]
OTHER_PERSON_RX = re.compile(r"\b(?:my\s+|a\s+)?(?:friend|neighbour|neighbor|colleague|coworker|co-worker|roommate|boss|classmate|husband|wife|spouse|partner|boyfriend|girlfriend)\b", re.I)
QUESTION_DIAG_RX = re.compile(r"\b(?:could\s+this\s+be|do\s+i\s+have|is\s+this\s+a|could\s+it\s+be|wondering\s+if\s+i\s+have|wonder\s+if\s+i\s+have|can\s+this\s+be|am\s+i\s+having|kya\s+mujhe\s+\w+\s+hai)\b", re.I)
SUSPECTED_INLINE_RX = re.compile(r"\b(?:rule\s+out|r/o|suspected|suspect(?:ing)?|possible|possibly|differential\s+diagnosis|ddx|query|cannot\s+(?:rule\s+out|exclude)|to\s+exclude|evaluate\s+for|workup\s+for|concern\s+for)\b", re.I)
CONDITIONAL_INLINE_RX = re.compile(r"\b(?:if\s+you\s+(?:develop|experience|have|notice)|return\s+to\s+(?:clinic|er|emergency)\s+if|seek\s+(?:immediate\s+)?(?:medical\s+)?(?:care|attention)\s+if|warning\s+signs\s+include|call\s+\d+\s+if|in\s+case\s+of)\b", re.I)
EDUCATIONAL_INLINE_RX = re.compile(r"\b(?:is\s+a\s+(?:common|rare|serious|chronic)\s+(?:condition|disease|disorder)|can\s+be\s+caused\s+by|may\s+be\s+seen\s+in|patients\s+with\s+\w+\s+may\s+experience|educational\s+(?:note|information|material)|general\s+health\s+information|risk\s+factors\s+include|is\s+characterized\s+by|commonly\s+causes)\b", re.I)
DISCLAIMER_INLINE_RX = re.compile(r"\b(?:not\s+a\s+(?:clinical\s+)?diagnosis|for\s+informational\s+purposes\s+only|does\s+not\s+replace\s+medical\s+advice|clinical\s+correlation\s+is\s+(?:recommended|required|advised)|disclaimer)\b", re.I)
HISTORICAL_DIAG_RX = re.compile(r"\b(?:in\s+remission|cured|history\s+of\s+childhood\s+\w+|resolved\s+years\s+ago|diagnosed\s+in\s+(\d{4})|had\s+\w+\s+in\s+(\d{4}))\b", re.I)
YEAR_DATE_RX = re.compile(r"\b(20\d{2}|19\d{2})\b")


@dataclass
class SectionSpan:
    section_type: str
    heading: str
    start: int
    end: int
    event_date: Optional[str] = None


def parse_document_sections(text: str) -> list[SectionSpan]:
    """Identify document sections, headings and date anchors to provide structural context to every clause."""
    spans: list[SectionSpan] = []
    lines = text.splitlines(keepends=True)
    pos = 0
    cur_type = "general"
    cur_heading = ""
    cur_start = 0

    dm = re.search(r"(?:collected|reported|report date|sample date|date)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})", text, re.I)
    doc_date = dm.group(1) if dm else None

    for line in lines:
        line_len = len(line)
        stripped = line.strip()
        new_type = None
        new_heading = ""

        if AMBIGUOUS_HEADING_RX.match(stripped):
            new_type = "ambiguous"
            new_heading = stripped
        elif FAMILY_HEADING_RX.match(stripped):
            new_type = "family_history"
            new_heading = stripped
        elif PERSONAL_HEADING_RX.match(stripped):
            new_type = "personal_history"
            new_heading = stripped
        elif SUSPECTED_HEADING_RX.match(stripped):
            new_type = "suspected"
            new_heading = stripped
        elif EDUCATIONAL_HEADING_RX.match(stripped):
            new_type = "educational"
            new_heading = stripped
        elif MEDS_PAST_HEADING_RX.match(stripped):
            new_type = "past_medications"
            new_heading = stripped

        if new_type and new_type != cur_type:
            if pos > cur_start:
                spans.append(SectionSpan(section_type=cur_type, heading=cur_heading, start=cur_start, end=pos, event_date=doc_date))
            cur_type = new_type
            cur_heading = new_heading
            cur_start = pos

        pos += line_len

    if pos > cur_start:
        spans.append(SectionSpan(section_type=cur_type, heading=cur_heading, start=cur_start, end=pos, event_date=doc_date))
    return spans


def _section_at(spans: list[SectionSpan], offset: int) -> Optional[SectionSpan]:
    for s in spans:
        if s.start <= offset < s.end:
            return s
    return spans[-1] if spans else None


def _near_negation(clause: str, span: tuple[int, int], other_spans: tuple = ()) -> bool:
    """A negation cue in scope of the match. Before: up to 5 tokens back, stopping at a comma (list coordination with
    and/or is allowed: "no fever or chest pain"). After (Hindi is verb-final): up to 3 tokens forward, stopping at a comma,
    a conjunction, or the start of the next symptom ("seene mein dard hai aur bukhar nahi hai" must not deny chest pain).
    Also supports clinical form style: "Cancer: No" / "Diabetes: Nil"."""
    after_text = clause[span[1]:].strip()
    if FORM_NO_RX.match(after_text):
        return True
    if FORM_YES_RX.match(after_text):
        return False
    masked = UNCERTAIN_RX.sub(lambda m: " " * len(m.group(0)), clause)
    before = _TOKEN_RX.findall(masked[:span[0]])
    n = 0
    for tok in reversed(before):
        if tok in ",;:":
            break
        if NEG_RX.fullmatch(tok):
            return True
        n += 1
        if n >= 5:
            break
    nxt = min([a for a, _ in other_spans if a >= span[1]], default=len(masked))
    after = _TOKEN_RX.findall(masked[span[1]:nxt])
    for i, tok in enumerate(after[:3]):
        if tok in ",;:" or tok.lower() in _STOP_AFTER:
            break
        if NEG_RX.fullmatch(tok):
            return True
    return False


def extract_symptoms(text: str, locator: Optional[str] = None) -> list[FactDraft]:
    drafts: list[FactDraft] = []
    last_in_prev: list[FactDraft] = []
    sections = parse_document_sections(text)
    for off, clause in _clauses(text):
        sec = _section_at(sections, off)
        found: list[tuple[int, int, str]] = []
        for key, rx in _CONCEPT_RX.items():
            for m in rx.finditer(clause):
                found.append((m.start(), m.end(), key))
        # prefer longest span when two concepts overlap
        found.sort(key=lambda t: (-(t[1] - t[0]), t[0]))
        taken: list[tuple[int, int, str]] = []
        for a, b, k in found:
            if not any(a < tb and b > ta for ta, tb, _ in taken):
                taken.append((a, b, k))
        taken.sort()
        clause_drafts: list[FactDraft] = []
        for a, b, key in taken:
            category, label, _ = C[key]
            neg = _near_negation(clause, (a, b), tuple((x, y) for x, y, _ in taken if (x, y) != (a, b)))
            unc = bool(UNCERTAIN_RX.search(clause))
            resolved = bool(RESOLVED_RX.search(clause))
            past = bool(PAST_RX.search(clause)) and not CURRENT_RX.search(clause)
            if unc:
                pol, temp = "uncertain", "unknown"
            elif resolved and not neg:
                pol, temp = "present", "resolved"
            elif resolved and neg and re.search(r"no longer|not anymore|ab", clause, re.I):
                pol, temp = "present", "resolved"
            elif neg:
                pol, temp = "denied", "past" if past else "current"
            else:
                pol, temp = "present", "past" if past else "current"
            if category in ("history", "context") and temp == "current":
                temp = "unknown"

            # Determine source-aware provenance, subject, and assertion status
            subj = "patient"
            rel = "patient"
            assertion = "present" if pol == "present" else "denied" if pol == "denied" else "suspected"
            sec_heading = sec.heading if sec and sec.heading else None
            ev_date = sec.event_date if sec else None
            flags: list[str] = []

            # Check year/date anchor in clause
            ym = YEAR_DATE_RX.search(clause)
            if ym:
                ev_date = ym.group(1)

            # Check historical remission or past cured
            if HISTORICAL_DIAG_RX.search(clause):
                assertion = "resolved" if ("remission" in clause.lower() or "cured" in clause.lower() or "resolved" in clause.lower()) else "historical"
                temp = "past" if assertion == "historical" else "resolved"

            # 1. Family history section or inline family terms
            is_family_sec = bool(sec and sec.section_type == "family_history")
            inline_family_rel = None
            for rname, r_rx in FAMILY_MEMBER_TERMS:
                if r_rx.search(clause):
                    inline_family_rel = rname
                    break

            if is_family_sec or inline_family_rel:
                subj = "family_member"
                rel = inline_family_rel or "unknown"
                assertion = "family-history-negative" if pol == "denied" else "family-history-positive"
                category = "history"
                if not sec_heading and is_family_sec:
                    sec_heading = "Family History"

            # 2. Other person referent (friend, neighbour, spouse)
            elif OTHER_PERSON_RX.search(clause):
                subj = "another_person"
                rel = "other_person"
                assertion = "denied" if pol == "denied" else "present"

            # 3. Ambiguous / damaged OCR section heading
            elif sec and sec.section_type == "ambiguous":
                subj = "unknown"
                rel = "unknown"
                assertion = "unknown"
                pol = "uncertain"
                flags.append("subject_or_section_uncertain")

            # 4. Suspected / Differential / Rule-out
            elif (sec and sec.section_type == "suspected") or SUSPECTED_INLINE_RX.search(clause):
                subj = "patient"
                rel = "patient"
                assertion = "suspected"
                pol = "uncertain"
                flags.append("suspected_condition_not_confirmed")

            # 5. Diagnostic question asked by patient (e.g. "Could this be cancer?")
            elif QUESTION_DIAG_RX.search(clause):
                subj = "patient"
                rel = "patient"
                assertion = "questioned"
                pol = "uncertain"
                flags.append("diagnostic_question_not_confirmed")

            # 6. Educational material or disclaimer
            elif (sec and sec.section_type == "educational") or EDUCATIONAL_INLINE_RX.search(clause) or DISCLAIMER_INLINE_RX.search(clause):
                subj = "unknown"
                rel = "unknown"
                assertion = "educational" if (EDUCATIONAL_INLINE_RX.search(clause) or (sec and "education" in sec.heading.lower())) else "disclaimer"
                pol = "uncertain"
                flags.append("non_patient_educational_or_disclaimer")

            # 7. Conditional / hypothetical warning instructions
            elif CONDITIONAL_INLINE_RX.search(clause):
                subj = "patient"
                rel = "patient"
                assertion = "conditional"
                pol = "uncertain"
                flags.append("conditional_or_hypothetical_instruction")

            d = FactDraft(
                category=category, concept=key, label=label, polarity=pol, temporal=temp,
                excerpt=_excerpt(text, off + a, off + b), locator=locator, span=(off + a, off + b),
                subject=subj, relationship_to_patient=rel, assertion_status=assertion,
                section_heading=sec_heading, event_date=ev_date, flags=flags,
            )
            if pol != "denied" and assertion not in ("educational", "disclaimer"):
                _attach_modifiers(d, clause)
            clause_drafts.append(d)
        if not clause_drafts and last_in_prev:
            # orphan modifiers ("since 3 days", "it is getting worse") describe the previous clause's symptoms
            if RESOLVED_RX.search(clause) and not NEG_RX.search(RESOLVED_RX.sub(" ", clause)):
                for d in last_in_prev:
                    if d.polarity == "present":
                        d.temporal = "resolved"
                        d.flags.append("resolved_in_adjacent_sentence")
            probe = FactDraft(category="", concept="")
            _attach_modifiers(probe, clause)
            if any([probe.duration, probe.onset, probe.progression, probe.severity, probe.frequency]):
                for d in last_in_prev:
                    if d.polarity == "present" and d.category == "symptom":
                        for f in ("duration", "onset", "progression", "severity", "frequency"):
                            if getattr(d, f) is None and getattr(probe, f):
                                setattr(d, f, getattr(probe, f))
                                if "modifier_from_adjacent_sentence" not in d.flags:
                                    d.flags.append("modifier_from_adjacent_sentence")
        if clause_drafts:
            last_in_prev = [d for d in clause_drafts if d.category == "symptom"]
        drafts.extend(clause_drafts)
    return drafts


def _excerpt(text: str, a: int, b: int, pad: int = 50) -> str:
    lo, hi = max(0, a - pad), min(len(text), b + pad)
    return text[lo:hi].strip()


def _attach_modifiers(d: FactDraft, clause: str) -> None:
    d.duration = parse_duration(clause)
    if SUDDEN_RX.search(clause):
        d.onset = "sudden"
    elif GRADUAL_RX.search(clause):
        d.onset = "gradual"
    m = FREQ_RX.search(clause)
    if m:
        d.frequency = m.group(1).lower()
    m = SEV_NUM_RX.search(clause)
    painful = d.concept.endswith(("_pain", "ache")) or d.concept in ("headache", "sore_throat")
    if m and painful and 0 <= int(m.group(1)) <= 10:
        d.severity = f"{int(m.group(1))}/10"
    else:
        m = SEV_WORD_RX.search(clause)
        if m:
            d.severity = m.group(1).lower()
    if WORSE_RX.search(clause):
        d.progression = "rapidly_worsening" if RAPID_RX.search(clause) else "worsening"
    elif BETTER_RX.search(clause) and not WORSE_RX.search(clause):
        d.progression = "improving"
    elif SAME_RX.search(clause):
        d.progression = "stable"


# ---------------------------------------------------------------- vitals / age / allergy / meds ------
VITAL_PATTERNS = [
    ("spo2", "%", re.compile(r"(?:spo2|sp02|o2 sat(?:uration)?s?|oxygen(?: saturation| level)?|saturation)\s*(?:is|was|of|:|=|at)?\s*(\d{2,3}(?:\.\d)?)\s*(%|percent)?", re.I)),
    ("heart_rate", "bpm", re.compile(r"(?:heart rate|pulse(?: rate)?|\bhr\b)\s*(?:is|was|of|:|=|at)?\s*(\d{2,3})\s*(bpm|/min|per min\w*)?", re.I)),
    ("resp_rate", "/min", re.compile(r"(?:respiratory rate|resp(?:iration)? rate|breathing rate|\brr\b)\s*(?:is|was|of|:|=|at)?\s*(\d{1,2})\s*(/min|per min\w*|breaths?(?: per minute)?)?", re.I)),
    ("temperature", None, re.compile(r"(?:temp(?:erature)?|fever of|bukhar|बुखार)\s*(?:is|was|of|:|=|at)?\s*(\d{2,3}(?:\.\d)?)\s*(?:°|º|degrees?\s*)?\s*(f(?:ahrenheit)?|c(?:elsius)?)?\b", re.I)),
    ("temperature", None, re.compile(r"(\d{2,3}(?:\.\d)?)\s*(?:°|º)\s*(f|c)\b", re.I)),
    ("pain_score", "/10", re.compile(r"pain(?: score| level| scale)?\s*(?:is|was|of|:|=|at)?\s*(\d{1,2})\s*(?:/|out of)\s*10", re.I)),
]
BP_RX = re.compile(r"(?:\bbp\b|blood pressure|b\.p\.?)\s*(?:is|was|of|:|=|at)?\s*(\d{2,3})\s*(?:/|over)\s*(\d{2,3})\s*(mm ?hg)?|(\d{2,3})\s*/\s*(\d{2,3})\s*mm ?hg", re.I)
TIME_RX = re.compile(r"\bat (\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)

# Physiological *impossibility* bounds used only to flag likely typing/OCR/unit errors. NOT clinical thresholds.
PLAUSIBLE = {"spo2": (50, 100, "%"), "heart_rate": (20, 300, "bpm"), "resp_rate": (4, 80, "/min"),
             "systolic_bp": (40, 300, "mmHg"), "diastolic_bp": (20, 200, "mmHg"), "pain_score": (0, 10, "/10"),
             "temperature_c": (30, 45, "C"), "temperature_f": (86, 113, "F")}


def f_to_c(f: float) -> float:
    return round((f - 32) * 5 / 9, 1)


def extract_vitals(text: str, locator: Optional[str] = None) -> list[FactDraft]:
    out: list[FactDraft] = []
    for name, default_unit, rx in VITAL_PATTERNS:
        for m in rx.finditer(text):
            val = float(m.group(1))
            unit = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            d = FactDraft(category="vital", concept=name, label=name.replace("_", " ").title(), value_num=val,
                          excerpt=_excerpt(text, m.start(), m.end(), 30), locator=locator, span=(m.start(), m.end()))
            if name == "temperature":
                u = (unit or "").lower()
                d.unit = "F" if u.startswith("f") else "C" if u.startswith("c") else None
                if d.unit is None:
                    d.flags.append("unit_missing")
            elif name == "spo2":
                d.unit = "%"
                if not unit and val > 100:
                    d.flags.append("implausible_value")
            else:
                d.unit = default_unit
            d.flags += _plausibility(d)
            tm = TIME_RX.search(text[max(0, m.start() - 40): m.end() + 40])
            if not tm:
                d.flags.append("measurement_time_unknown")
            out.append(d)
    for m in BP_RX.finditer(text):
        s, d_ = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(5))
        fd = FactDraft(category="vital", concept="blood_pressure", label="Blood pressure", value_num=float(s), value_num2=float(d_), unit="mmHg",
                       excerpt=_excerpt(text, m.start(), m.end(), 30), locator=locator, span=(m.start(), m.end()))
        fd.flags += _plausibility(fd)
        if not TIME_RX.search(text[max(0, m.start() - 40): m.end() + 40]):
            fd.flags.append("measurement_time_unknown")
        out.append(fd)
    return out


def _plausibility(d: FactDraft) -> list[str]:
    flags = []
    if d.concept == "temperature" and d.unit and d.value_num is not None:
        lo, hi, _ = PLAUSIBLE["temperature_c" if d.unit == "C" else "temperature_f"]
        if not lo <= d.value_num <= hi:
            flags.append("implausible_value")
            other = PLAUSIBLE["temperature_f" if d.unit == "C" else "temperature_c"]
            if other[0] <= d.value_num <= other[1]:
                flags.append("unit_may_be_" + ("F" if d.unit == "C" else "C"))
    elif d.concept == "blood_pressure" and d.value_num is not None:
        if not (PLAUSIBLE["systolic_bp"][0] <= d.value_num <= PLAUSIBLE["systolic_bp"][1]) or \
           not (PLAUSIBLE["diastolic_bp"][0] <= (d.value_num2 or 0) <= PLAUSIBLE["diastolic_bp"][1]) or (d.value_num2 or 0) >= d.value_num:
            flags.append("implausible_value")
    elif d.concept in PLAUSIBLE and d.value_num is not None:
        lo, hi, _ = PLAUSIBLE[d.concept]
        if not lo <= d.value_num <= hi:
            flags.append("implausible_value")
    return flags


AGE_RX = [
    re.compile(r"\b(?:i am|i'm|im|age(?:d)?|aged|my age is)\s*:?\s*(\d{1,3})\b(?!\s*(?:/|%|mm|bpm|kg|cm))(?:\s*(?:years?|yrs?|y/?o|saal|sal|वर्ष|साल))?", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:years?|yrs?)[ -]*old\b", re.I),
    re.compile(r"\b(\d{1,3})\s*(?:saal|sal|वर्ष|साल)\s*(?:ka|ki|ke|का|की|के|ho)?\b", re.I),
    re.compile(r"(?:umar|umr|उम्र)\s*(?:hai|है|:)?\s*(\d{1,3})", re.I),
]


def extract_age(text: str, locator: Optional[str] = None) -> list[FactDraft]:
    out = []
    for rx in AGE_RX:
        for m in rx.finditer(text):
            n = int(m.group(1))
            if 0 < n < 121:
                # "I am 45" must not match "I am 3 days into this"; require no duration word right after
                tail = text[m.end(): m.end() + 12].lower()
                if re.match(r"\s*(?:days?|din|hours?|weeks?|months?)\b", tail) and "year" not in m.group(0).lower() and "saal" not in m.group(0).lower():
                    continue
                out.append(FactDraft(category="demographic", concept="age", label="Age", value_num=float(n), unit="years", temporal="current",
                                     excerpt=_excerpt(text, m.start(), m.end(), 25), locator=locator, span=(m.start(), m.end())))
    seen, uniq = set(), []
    for d in out:
        if d.value_num not in seen:
            seen.add(d.value_num)
            uniq.append(d)
    return uniq[:1]


ALLERGY_NONE_RX = re.compile(_BOUND_L + r"(?:no (?:known )?(?:drug )?allerg(?:y|ies)|nkda|allerg(?:y|ies)\s*(?:nahi|nahin|nhi|none|:\s*none)|koi allerg\w* nahi|कोई एलर्जी नहीं|एलर्जी नहीं)" + _BOUND_R, re.I)
ALLERGY_DENY_NAMED_RX = re.compile(r"\b(?:not allergic to|no allergy to|never had (?:an )?allergy to)\s+([A-Za-z][A-Za-z\- ]{2,25}?)(?=$|[.,;\n]|\s+(?:and|but)\b)", re.I)
ALLERGY_RX = re.compile(r"(?<!not )\ballergic to ([A-Za-z][A-Za-z\- ]{2,30}?)(?=$|[.,;\n]|\s+(?:and|but|since|from|which|because)\b)|(?<!no )allergy to ([A-Za-z][A-Za-z\- ]{2,30}?)(?=$|[.,;\n])|([A-Za-z]{4,20}) (?:se|ki) allerg\w*", re.I | re.M)
MED_DENY_NAMED_RX = re.compile(r"\b(?:(?:do not|don'?t|did not|didn'?t|never) (?:take|use)|not (?:taking|using|on)|stopped (?:taking|using))\s+([A-Za-z][A-Za-z\-]{3,25}(?:\s+\d+\s?(?:mg|mcg|g|ml))?)", re.I)
MED_RX = re.compile(r"\b(?:i (?:am |was )?(?:taking|take|use|using)|currently taking|(?<!not )(?<!stopped )(?:taking|prescribed)|medicines? (?:is|are)|dawai(?:yan)? (?:hai|le raha|le rahi))\s+([A-Za-z][A-Za-z\-]{3,25}(?:\s+\d+\s?(?:mg|mcg|g|ml))?)", re.I)
MED_ON_DOSE_RX = re.compile(r"(?<!not )\bon ([A-Za-z][A-Za-z\-]{3,25}\s+\d+(?:\.\d+)?\s?(?:mg|mcg|ml|iu|units?))", re.I)
MED_DENY_RX = re.compile(_BOUND_L + r"(?:no (?:regular )?(?:medicines?|medications?|meds)|not (?:taking|on) any (?:medicines?|medications?)|koi (?:dawai|dawa|medicine) nahi|कोई दवा नहीं)" + _BOUND_R, re.I)
_MED_STOP = {"any", "some", "the", "medicine", "medicines", "medication", "pills", "tablets", "nothing", "this", "that", "care", "it", "them", "no", "not", "also", "regular", "regularly", "daily", "rest", "time", "place", "break", "advantage", "admission", "notes", "steps", "turns", "long", "much", "many", "very", "only", "just", "now"}


MED_LIST_RX = re.compile(r"(?:^|\n|\b)(?:current |regular |home |discharge |past |discontinued |previous )?(?:medicines?|medications?|meds|drugs?|rx)\s*[:\-]\s*([^\n]{3,160})", re.I)
_ITEM_RX = re.compile(r"^\s*(?:tab(?:let)?\.?\s+|cap(?:sule)?\.?\s+|inj\.?\s+)?([A-Za-z][A-Za-z\-]{3,25})(\s+\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|iu|units?))?", re.I)
MED_PROPOSED_RX = re.compile(r"\b(?:doctor\s+(?:discussed|advised|suggested|considered)\s+(?:starting|taking)|consider(?:ing)?\s+starting|to\s+start)\s+([A-Za-z][A-Za-z\-]{3,25}(?:\s+\d+\s?(?:mg|mcg|g|ml))?)", re.I)


def extract_meds_allergies(text: str, locator: Optional[str] = None) -> list[FactDraft]:
    out: list[FactDraft] = []
    sections = parse_document_sections(text)
    for m in MED_LIST_RX.finditer(text):
        sec = _section_at(sections, m.start())
        is_past = bool((sec and sec.section_type == "past_medications") or re.search(r"\b(?:past|discontinued|previous|stopped|prior)\b", m.group(0), re.I))
        for part in re.split(r"[,;/]| and ", m.group(1)):
            im = _ITEM_RX.match(part)
            if not im or im.group(1).lower() in _MED_STOP or im.group(1).lower() in ("none", "nil", "nill", "nothing"):
                continue
            name = (im.group(1) + (im.group(2) or "")).strip()
            out.append(FactDraft(
                category="medication", concept=f"medication:{im.group(1).lower()}", label=f"Medicine: {name}",
                polarity="present", temporal="past" if is_past else "current",
                assertion_status="historical" if is_past else "present",
                section_heading=sec.heading if sec and sec.heading else None,
                value_text=name, excerpt=_excerpt(text, m.start(), m.end(), 10), locator=locator, span=(m.start(), m.end()),
            ))
    for m in ALLERGY_NONE_RX.finditer(text):
        sec = _section_at(sections, m.start())
        out.append(FactDraft(
            category="allergy", concept="allergy_any", label="Any known allergy", polarity="denied", temporal="unknown",
            assertion_status="denied", section_heading=sec.heading if sec and sec.heading else None,
            excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
        ))
    for m in ALLERGY_DENY_NAMED_RX.finditer(text):
        sec = _section_at(sections, m.start())
        name = m.group(1).strip().lower()
        if name and name.split()[0] not in _MED_STOP:
            out.append(FactDraft(
                category="allergy", concept=f"allergy:{name}", label=f"Allergy: {name}", polarity="denied", temporal="unknown",
                assertion_status="denied", section_heading=sec.heading if sec and sec.heading else None,
                excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
            ))
    for m in ALLERGY_RX.finditer(text):
        sec = _section_at(sections, m.start())
        name = next(g for g in m.groups() if g).strip().lower()
        if name and name.split()[0] not in _MED_STOP:
            out.append(FactDraft(
                category="allergy", concept=f"allergy:{name}", label=f"Allergy: {name}", polarity="present", temporal="unknown",
                assertion_status="present", section_heading=sec.heading if sec and sec.heading else None,
                excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
            ))
    for m in MED_DENY_RX.finditer(text):
        sec = _section_at(sections, m.start())
        out.append(FactDraft(
            category="medication", concept="medication_any", label="Any regular medicine", polarity="denied", temporal="unknown",
            assertion_status="denied", section_heading=sec.heading if sec and sec.heading else None,
            excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
        ))
    for m in MED_DENY_NAMED_RX.finditer(text):
        sec = _section_at(sections, m.start())
        name = m.group(1).strip()
        first = name.split()[0].lower()
        is_stopped = bool(re.search(r"\bstopped\b", m.group(0), re.I))
        if first not in _MED_STOP and first not in {c.split("_")[0] for c in C} and not any(rx.fullmatch(first) for rx in _CONCEPT_RX.values()):
            out.append(FactDraft(
                category="medication", concept=f"medication:{first}", label=f"Medicine: {name}", polarity="denied",
                temporal="past" if is_stopped else "current", assertion_status="historical" if is_stopped else "denied",
                section_heading=sec.heading if sec and sec.heading else None,
                value_text=name, excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
            ))
    for m in MED_ON_DOSE_RX.finditer(text):
        sec = _section_at(sections, m.start())
        name = m.group(1).strip()
        first = name.split()[0].lower()
        if first not in _MED_STOP:
            out.append(FactDraft(
                category="medication", concept=f"medication:{first}", label=f"Medicine: {name}", polarity="present", temporal="current",
                assertion_status="present", section_heading=sec.heading if sec and sec.heading else None,
                value_text=name, excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
            ))
    for m in MED_PROPOSED_RX.finditer(text):
        sec = _section_at(sections, m.start())
        name = m.group(1).strip()
        first = name.split()[0].lower()
        if first not in _MED_STOP:
            out.append(FactDraft(
                category="medication", concept=f"medication:{first}", label=f"Medicine (discussed): {name}", polarity="uncertain", temporal="unknown",
                assertion_status="conditional", section_heading=sec.heading if sec and sec.heading else None,
                value_text=name, excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
                flags=["proposed_or_discussed_medication"],
            ))
    for m in MED_RX.finditer(text):
        sec = _section_at(sections, m.start())
        name = m.group(1).strip()
        first = name.split()[0].lower()
        if first in _MED_STOP or first in {c.split("_")[0] for c in C} or any(rx.fullmatch(first) for rx in _CONCEPT_RX.values()):
            continue
        out.append(FactDraft(
            category="medication", concept=f"medication:{first}", label=f"Medicine: {name}", polarity="present", temporal="current",
            assertion_status="present", section_heading=sec.heading if sec and sec.heading else None,
            value_text=name, excerpt=_excerpt(text, m.start(), m.end(), 20), locator=locator, span=(m.start(), m.end()),
        ))
    return out


def extract_all(text: str, locator: Optional[str] = None) -> list[FactDraft]:
    """Everything the lexicon can state with certainty from the text, in reading order."""
    drafts = (extract_symptoms(text, locator) + extract_vitals(text, locator) + extract_age(text, locator)
              + extract_meds_allergies(text, locator))
    return sorted(drafts, key=lambda d: d.span[0])
