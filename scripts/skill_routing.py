#!/usr/bin/env python3
"""Deterministic lexical ranking of skill descriptions.

Extracted from the routing benchmark so that the prompt-submit hook and the
benchmark score a prompt with exactly the same code. A hook carrying its own
copy of this logic could satisfy every checked-in routing case while ranking
something else at runtime, which is the one failure the benchmark exists to
rule out.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, Sequence

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

# Function words carry no evidence about which workflow a prompt wants, but
# cosine similarity spends real mass on them: "add a newline at the end of the
# file" scored 0.264 against verify-web-behavior, three quarters of it from
# "at", "the", and the "end" of "end-to-end flows". That put ordinary editing
# above the firing threshold and genuine multi-file work below it, so the gate
# could only be set high enough to silence both.
ENGLISH_STOPWORDS = frozenset(
    """
    a an and are as at be been but by can do does for from had has have how i
    if in into is it its may must never no not of on only or over per should so
    than that the their then there these they this those to use used uses
    using was were what when where which while who why will with without you
    your yours just also very more most much some any each both other another
    """.split()
)

# Spanish function words, folded. Without these the noise problem the English
# list solves reappears in Spanish: a prompt is mostly function words, and each
# one that survives is cosine mass spent on nothing.
#
# tokenize applies the union to prompts and descriptions alike, so a word
# listed here is not merely ignored in Spanish -- it is deleted from the
# English listings too. That is why these must not overlap the description
# vocabulary, which test_spanish_stopwords_silence_no_description_word holds.
# "no" is the exception that proves the rule: it is a function word in both
# languages and explore-web-adaptively spends a high-idf token on it ("no
# stable reproduction exists yet"), so every Spanish prompt containing "no"
# would drift toward that one skill. It belongs on the English list above,
# where dropping it from both sides is the intended effect rather than a
# violation of this one's invariant.
SPANISH_STOPWORDS = frozenset(
    """
    al algo alguna alguno ahora aqui asi aun cada como con contra cual cuando
    de del desde donde dos el ella ellos en entre era eres es esa ese eso esta
    estan este esto estos fue fueron ha haber habia hace hacer hasta hay la las
    le les lo los mas me mi mientras mucho muy nada ni nos nuestro o os otra
    otro para pero poco por porque puede pueden que quien quiero se sea ser si
    sin sobre solo son su sus tambien tan tanto te tener tengo ti tiene todo
    todos tu un una uno unos usar y ya yo favor porfa gracias hola ok vale
    dame haz usa realiza convierte quita pon dime necesito podrias puedes
    """.split()
)

STOPWORDS = ENGLISH_STOPWORDS | SPANISH_STOPWORDS


# Spanish spelled with its accents is not a smaller version of the same
# problem, it is a different one: TOKEN_PATTERN matches ASCII, so every
# accented word shattered into fragments before any matching could happen.
# "implementacion" became ['implementaci', 'n'] and "codigo" became ['c',
# 'digo'] -- not weak matches, but garbage tokens that also inflated the query
# norm and pushed the real words down. Folding runs before tokenization so the
# words arrive whole; it also turns "n" with a tilde into "n", which is what
# "diseno" and "espanol" need.
def _fold(text: str) -> str:
    # Every skill description is ASCII and they are re-folded on every ranking,
    # so the common input needs no work at all: the guard takes that path from
    # 0.37 ms per ranking to about a microsecond.
    if text.isascii():
        return text
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _stem(token: str) -> str:
    for suffix in (
        "ization",
        "ation",
        "ments",
        "ment",
        "ingly",
        "edly",
        "ing",
        "ies",
        "ed",
        "es",
        "s",
    ):
        if token.endswith(suffix) and len(token) > len(suffix) + 3:
            return token[: -len(suffix)]
    return token


# Spanish content words, mapped onto the English the descriptions are written
# in. Folding alone does not rescue these: the skill listings are English, so a
# correctly tokenized "arregla el fallo intermitente" still shares no word with
# diagnose-systematically. Latinate cognates were tried first and reach only 9
# of 39 probed domain terms -- "implementacion" stems onto "implement", but
# "archivo", "prueba", "fallo", and "navegador" have no English shape to find,
# and stretching the stemmer far enough to invent one produces collisions
# rather than matches. So the mapping is stated rather than guessed.
#
# Every value must be vocabulary some description actually uses; a mapping onto
# a word no skill declares is dead weight that still inflates the query norm,
# and test_every_translation_lands_in_the_description_vocabulary fails on one.
#
# A key spelled the same in both languages rewrites English prompts too, since
# nothing here knows what language it was handed. Identity mappings ("test",
# "version", "visual") are harmless, but "reduce" -- Spanish third person and
# English imperative -- was mapped to "reduced" and quietly moved English
# "Reduce output verbosity" from communicate-efficiently onto solve-efficiently
# by 0.009. Unambiguously Spanish spellings only ("reducir"), unless the
# mapping is identity or the English reading wants the same target anyway
# ("error" and "bug" onto "defect"); the rule is checked by
# test_no_translation_rewrites_an_english_word_into_a_different_stem, which
# scans this repository's own English prose rather than only the descriptions.
# Scanning the descriptions alone is what let "actual" (English: real, not
# current) route "the actual behavior differs from the docs" to
# use-current-docs, and "extension", "simple", "opera", "multiple" and
# "legible" do the same -- none of them appear in a description, so the narrow
# check could not see any of them.
SPANISH_TERMS = {
    # Artifacts and places
    "archivo": "file",
    "fichero": "file",
    "repositorio": "repository",
    "proyecto": "project",
    "modulo": "module",
    "paquete": "packages",
    "codigo": "code",
    "implementacion": "implementation",
    "analisis": "analysis",
    "compactacion": "compaction",
    "version": "version",
    "dependencia": "dependency",
    "biblioteca": "library",
    "libreria": "library",
    "marco": "framework",
    "herramienta": "tool",
    "complemento": "plugin",
    "agente": "agent",
    "navegador": "browser",
    "escritorio": "desktop",
    "ventana": "window",
    "aplicacion": "application",
    "programa": "application",
    "pantalla": "screenshot",
    "sitio": "site",
    "ruta": "path",
    "flujo": "flow",
    "interfaz": "interface",
    "documentacion": "documentation",
    "documento": "documentation",
    "informe": "report",
    "registro": "record",
    "memoria": "memory",
    "contexto": "context",
    "estado": "state",
    "instalacion": "installation",
    "lanzamiento": "release",
    "publicacion": "release",
    "entrega": "delivery",
    "tarea": "task",
    "trabajo": "work",
    "criterio": "criterion",
    "objetivo": "outcomes",
    "limite": "boundaries",
    "frontera": "boundaries",
    "permiso": "permission",
    "condicion": "condition",
    "evidencia": "evidence",
    "recibo": "receipt",
    "hipotesis": "hypotheses",
    "experimento": "experiment",
    "causa": "cause",
    "defecto": "defect",
    "fallo": "defect",
    "error": "defect",
    "bug": "defect",
    "regresion": "regression",
    "rendimiento": "performance",
    "comportamiento": "behavior",
    "prueba": "test",
    "test": "test",
    "senal": "signal",
    "captura": "screenshot",
    "diseno": "design",
    "marca": "brand",
    "calidad": "quality",
    "accesibilidad": "accessibility",
    "habilidad": "skill",
    "capacidad": "capability",
    "usuario": "user",
    "decision": "decision",
    "pregunta": "question",
    "respuesta": "answer",
    "explicacion": "explanation",
    "lenguaje": "language",
    "articulo": "paper",
    "papel": "paper",
    "fuente": "source",
    "cambio": "change",
    "persistencia": "persistence",
    "verificacion": "verification",
    "investigacion": "investigation",
    "diagnostico": "diagnosis",
    "auditoria": "audit",
    "brecha": "gap",
    "hueco": "gap",
    # Actions
    "arregla": "fix",
    "arreglar": "fix",
    "corrige": "fix",
    "corregir": "fix",
    "repara": "fix",
    "reparar": "fix",
    "implementa": "implement",
    "implementar": "implement",
    "ejecuta": "execute",
    "ejecutar": "execute",
    "corre": "run",
    "correr": "run",
    "verifica": "verify",
    "verificar": "verify",
    "comprueba": "verify",
    "comprobar": "verify",
    "revisa": "review",
    "revisar": "review",
    "audita": "audit",
    "auditar": "audit",
    "diagnostica": "diagnose",
    "diagnosticar": "diagnose",
    "investiga": "investigation",
    "investigar": "investigation",
    "reproduce": "reproduce",
    "reproducir": "reproduce",
    "minimiza": "minimize",
    "minimizar": "minimize",
    "demuestra": "prove",
    "demostrar": "prove",
    "explica": "explain",
    "explicar": "explain",
    "documenta": "documentation",
    "documentar": "documentation",
    "mapea": "map",
    "mapear": "map",
    "explora": "explore",
    "explorar": "explore",
    "descubre": "discover",
    "descubrir": "discover",
    "encuentra": "find",
    "encontrar": "find",
    "resuelve": "solve",
    "resolver": "solve",
    "reducir": "reduced",
    "compara": "comparative",
    "comparar": "comparative",
    "guarda": "storage",
    "guardar": "storage",
    "almacena": "storage",
    "almacenar": "storage",
    "reanuda": "resume",
    "reanudar": "resume",
    "sobrevive": "survive",
    "sobrevivir": "survive",
    "instala": "install",
    "instalar": "install",
    "configura": "configured",
    "configurar": "configured",
    "actualiza": "update",
    "actualizar": "update",
    "selecciona": "select",
    "seleccionar": "select",
    "recomienda": "recommend",
    "recomendar": "recommend",
    "navega": "navigation",
    "navegar": "navigation",
    "operar": "operate",
    "falla": "defect",
    "fallar": "defect",
    # Words the corpus reached for that the shorter list missed
    "actualizacion": "update",
    "adapta": "adaptive",
    "adaptar": "adaptive",
    "compacta": "compact",
    "compacto": "compact",
    "comparativa": "comparative",
    "comparativo": "comparative",
    "congelada": "freeze",
    "congelado": "freeze",
    "contrato": "contract",
    "deliberada": "deliberate",
    "deliberado": "deliberate",
    "densa": "dense",
    "denso": "dense",
    "direccion": "direction",
    "entrada": "input",
    "explicame": "explain",
    "instalador": "installer",
    "instruccion": "instruction",
    "pasaje": "passage",
    "preregistro": "registration",
    "preserva": "preserving",
    "preservando": "preserving",
    "preservar": "preserving",
    "progresiva": "progressive",
    "progresivo": "progressive",
    "progreso": "progress",
    "protegida": "guarded",
    "protegido": "guarded",
    "reanudable": "resume",
    "redisena": "redesigns",
    "redisenar": "redesigns",
    "rediseno": "redesigns",
    "registra": "record",
    "relacionada": "unrelated",
    "relacionado": "unrelated",
    "reproduccion": "reproduction",
    "suministrada": "supplied",
    "suministrado": "supplied",
    "tecnica": "technical",
    "tecnico": "technical",
    "testeable": "testable",
    "verbosidad": "brevity",
    # Qualities
    "intermitente": "intermittent",
    "sistematico": "systematically",
    "sistematica": "systematically",
    "reproducible": "reproducible",
    "duradero": "durable",
    "duradera": "durable",
    "eficiente": "efficiently",
    "eficientemente": "efficiently",
    "actualizado": "current",
    "obsoleto": "stale",
    "obsoleta": "stale",
    # "unfamiliar", not "unknown": map-project is the skill that spells this
    # ("a large or unfamiliar tree"), and a mapping onto a word no description
    # uses is dead weight that still inflates the query norm.
    "desconocido": "unfamiliar",
    "desconocida": "unfamiliar",
    "acotada": "bounded",
    "acotado": "bounded",
    "primaria": "primary",
    "primario": "primary",
    "versionada": "version",
    "versionado": "version",
    "nativo": "native",
    "nativa": "native",
    "visible": "visible",
    "publico": "public",
    "publica": "public",
    "visual": "visual",
    "grande": "large",
    "largo": "long",
    "largas": "long",
    "duplicado": "duplicated",
    "duplicada": "duplicated",
    "explicito": "explicit",
    "explicita": "explicit",
    "conciso": "brevity",
    "concisa": "brevity",
    "breve": "brevity",
    "sencillo": "simply",
    "sencilla": "simply",
    "llano": "plain",
    # 1.8.0 vocabulary. The two workflows it added shipped with Spanish cases
    # but no lexicon entries, so all four ranked first and drew no suggestion:
    # every content word that mattered arrived as an unseen token, inflating
    # the query norm it should have been scoring against. "superficiales" is
    # keyed only in the plural because the singular is an English adjective --
    # the "actual"/"simple" class above -- and "red" (red de pruebas) is
    # absent for the same reason: an English prompt about the color would be
    # rewritten. "heredado" lands on the one word only legacy-safe-changes
    # declares, through its name, since 1.7.4 ceded it from refactor-cleanly.
    "superficiales": "shallow",
    "reenvia": "pass",
    "reenvian": "pass",
    "abstraccion": "abstraction",
    "interno": "internals",
    "interna": "internals",
    "filtra": "leaking",
    "filtrar": "leaking",
    "heredado": "legacy",
    "heredada": "legacy",
    "cobertura": "coverage",
}


def _translate(token: str) -> str | None:
    """Map one folded Spanish token onto description vocabulary, or None.

    Plurals are handled by rule rather than by listing every form, but the
    exact spelling is tried first so that a singular ending in -s ("analisis")
    is not mistaken for a plural of something else.
    """
    term = SPANISH_TERMS.get(token)
    if term is not None:
        return term
    if token.endswith("es") and len(token) > 4:
        term = SPANISH_TERMS.get(token[:-2])
        if term is not None:
            return term
    if token.endswith("s") and len(token) > 3:
        return SPANISH_TERMS.get(token[:-1])
    return None


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_PATTERN.findall(_fold(text.casefold())):
        if raw in STOPWORDS:
            continue
        translated = _translate(raw)
        stemmed = _stem(translated if translated is not None else raw)
        if stemmed in STOPWORDS:
            continue
        tokens.append(stemmed)
    return tokens


def _parse_skill_file(skill_file: Path) -> tuple[str, str]:
    # utf-8-sig, not utf-8: PowerShell 5.1 writes a BOM by default, and a BOM
    # made the anchored frontmatter match fail, which used to abort the whole
    # load and leave the router permanently and invisibly silent.
    text = skill_file.read_text(encoding="utf-8-sig")
    match = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
    if match is None:
        raise ValueError(f"missing frontmatter: {skill_file}")
    name_match = re.search(r"^name:\s*(.+?)\s*$", match.group(1), re.MULTILINE)
    description_match = re.search(
        r"^description:\s*(.+?)\s*$", match.group(1), re.MULTILINE
    )
    if name_match is None or description_match is None:
        raise ValueError(f"missing name or single-line description: {skill_file}")
    name = name_match.group(1).strip(" \"'")
    description = description_match.group(1).strip(" \"'")
    if not description or description in {">-", ">", "|", "|-"}:
        # A folded or block scalar leaves the marker as the whole description,
        # so the skill would rank against two junk characters instead of its
        # text. That is a broken skill, not a low-scoring one.
        raise ValueError(f"description is not a single-line scalar: {skill_file}")
    # Rank the text the host actually lists. Claude Code appends
    # when_to_use to description in the skill listing, so scoring the
    # description alone would measure something the model never sees.
    trigger_match = re.search(
        r"^when_to_use:\s*(.+?)\s*$", match.group(1), re.MULTILINE
    )
    if trigger_match is not None:
        # Stripped outside the f-string: a backslash inside a replacement
        # field is a syntax error before Python 3.12, and the support
        # matrix still declares 3.11.
        trigger = trigger_match.group(1).strip(" \"'")
        description = f"{description} {trigger}"
    return name, description


def load_skill_descriptions(root: Path) -> dict[str, str]:
    """Load every skill, refusing the whole set if one cannot be parsed.

    The gates need this strictness: a validator that silently dropped a broken
    skill would report a healthy catalogue. The hook uses the resilient loader
    below instead, because for routing a partial catalogue beats none.
    """
    descriptions: dict[str, str] = {}
    for skill_file in sorted((root / "skills").glob("*/SKILL.md")):
        name, description = _parse_skill_file(skill_file)
        if name in descriptions:
            raise ValueError(f"duplicate skill name: {name}")
        descriptions[name] = description
    return descriptions


def load_parsable_skill_descriptions(root: Path) -> tuple[dict[str, str], list[str]]:
    """Load what parses and name what did not.

    One unparsable SKILL.md used to abort the load, and the hook renders a
    failed load exactly like a prompt that matched nothing. A dead router and a
    quiet one were indistinguishable, for every prompt, forever.
    """
    descriptions: dict[str, str] = {}
    unparsable: list[str] = []
    for skill_file in sorted((root / "skills").glob("*/SKILL.md")):
        try:
            name, description = _parse_skill_file(skill_file)
        except (OSError, UnicodeDecodeError, ValueError):
            unparsable.append(skill_file.parent.name)
            continue
        if name in descriptions:
            unparsable.append(skill_file.parent.name)
            continue
        descriptions[name] = description
    return descriptions, unparsable


def _idf(documents: Sequence[Sequence[str]]) -> dict[str, float]:
    document_count = len(documents)
    frequencies: Counter[str] = Counter()
    for document in documents:
        frequencies.update(set(document))
    return {
        token: math.log((1 + document_count) / (1 + count)) + 1
        for token, count in frequencies.items()
    }


def _unseen_weight(document_count: int) -> float:
    """Weight for a query token that no skill description uses.

    ``_idf`` scores a token held by one description at ``log((1+N)/2)+1``; a
    token held by none is at least that rare, so it takes the count-zero value
    of the same curve. The former fallback of 1.0 was the weight of a token
    every skill shares, which let a prompt made mostly of off-domain words
    score as though those words were unremarkable.
    """
    return math.log(1 + document_count) + 1


def _vector(
    tokens: Iterable[str], idf: Mapping[str, float], unseen: float
) -> dict[str, float]:
    counts = Counter(tokens)
    return {token: count * idf.get(token, unseen) for token, count in counts.items()}


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    common = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


# Asking for a skill out loud drops the hyphen, so the spaced spelling has to
# be recognised -- but on its own it is not a request, it is a noun phrase.
# "verify delivery", "map project", "audit capabilities", and "use current
# docs" are ordinary English, and because an explicit match answers to no
# threshold, accepting them bare turned "verify delivery of the shipment
# before friday" into a full-confidence suggestion. The spaced form therefore
# needs a word that means the user is asking for a workflow; the hyphenated and
# underscored spellings do not, because nobody writes those by accident.
INVOCATION_CUE = re.compile(
    r"(?:^|[^a-z0-9_-])(?:use|using|used|run|runs|invoke|apply|call|start|with|"
    r"via|try|do)\s+(?:the\s+)?$"
)


def _explicit_skill_requested(query: str, name: str) -> bool:
    folded = _fold(query.casefold())
    literal = re.escape(name.casefold())
    for spelling, needs_cue in (
        (literal.replace(r"\-", "[-_]"), False),
        (literal.replace(r"\-", " "), True),
    ):
        pattern = rf"(?<![a-z0-9_-])\$?{spelling}(?![a-z0-9_-])"
        for match in re.finditer(pattern, folded):
            if not needs_cue or INVOCATION_CUE.search(folded[: match.start()]):
                return True
    return False


def _document(name: str, description: str) -> list[str]:
    return tokenize(f"{name.replace('-', ' ')} {description}")


def shared_token_count(query: str, name: str, descriptions: Mapping[str, str]) -> int:
    """Distinct content words the prompt and this skill's listing both use.

    The score alone cannot tell a real match from an incidental one: "reformat
    this file" reaches solve-efficiently on the single word "file", and scores
    as high doing it as a genuine multi-file request does on four words. The
    count is what separates them, so the hook gates on it.
    """
    return len(set(tokenize(query)) & set(_document(name, descriptions[name])))


def rank_skills(query: str, descriptions: Mapping[str, str]) -> list[tuple[str, float]]:
    names = list(descriptions)
    documents = [_document(name, descriptions[name]) for name in names]
    idf = _idf(documents)
    unseen = _unseen_weight(len(documents))
    query_tokens = tokenize(query)
    query_vector = _vector(query_tokens, idf, unseen)
    ranked: list[tuple[str, float]] = []
    for name, tokens in zip(names, documents):
        score = _cosine(query_vector, _vector(tokens, idf, unseen))
        if _explicit_skill_requested(query, name):
            score += EXPLICIT_REQUEST_SCORE
        ranked.append((name, round(score, 8)))
    return sorted(ranked, key=lambda item: (-item[1], item[0]))


# Calibrated against benchmarks/skill_routing_cases.json plus off-domain
# prompts (renames, commits, typo fixes, small talk, formatting). Suggesting a
# workflow for "fix the typo in the README" costs more than staying quiet,
# because the agent stops reading a channel that is usually wrong -- but so
# does staying quiet on the work the plugin exists for, which is what a single
# high floor used to do to two positives in five.
#
# What separates the two is not the score. Ordinary editing and genuine
# workflow requests overlap almost exactly in score; they do not overlap in how
# much vocabulary they share with the skill they land on. Across the case file
# no off-domain prompt shares more than one content word with its top skill,
# and all but one positive shares at least two.
MIN_SHARED_TOKENS = 2
# Below this the shared words are too thin a base to name a workflow even when
# there are several of them.
MIN_SCORE = 0.05
# Either a strong absolute match, or a decisive lead over the runner-up. The
# old gate required both, so a prompt that beat every other skill by 0.2 was
# still discarded for scoring 0.251 against a 0.27 floor.
CONFIDENT_SCORE = 0.14
DECISIVE_MARGIN = 0.03
# A tie is disqualifying on its own terms and cannot be outvoted by a high
# score: two skills the prompt fits equally well are two skills whose wording
# it matched as a family, and a confident score only means it matched the
# family strongly.
MIN_MARGIN = 0.02
# rank_skills adds this when the prompt names a skill outright; such a request
# is explicit rather than inferred and always clears the bar.
EXPLICIT_REQUEST_SCORE = 2.0

# The plugin's own name is a request for its workflows, and solve-efficiently
# advertises exactly that ("when Cognitive Powers is requested by name"). Only
# individual skill names used to be recognised, so the one phrase a user is
# most likely to reach for when the plugin seems idle -- its name -- was the
# one phrase that routed nowhere. It is also the one phrase that survives the
# prompt being written in a language these English descriptions cannot score.
PLUGIN_ALIAS_PATTERN = re.compile(r"(?<![a-z0-9_-])cognitive[-_ ]?powers(?![a-z0-9_-])")

# Naming the plugin is not always asking for it. "turn off cognitive powers",
# "uninstall cognitive-powers", and "cognitive powers is spamming me" all
# matched the alias and were answered with a workflow suggestion -- replying to
# a request to stop by doing it again. These words decide that the plugin is
# the subject of the sentence rather than the instrument.
PLUGIN_COMPLAINT = re.compile(
    r"(?<![a-z0-9_-])(?:off|disable|disabled|uninstall|remove|removing|stop|"
    r"stopped|silence|mute|quiet|revert|broken|breaks|broke|spam|spamming|"
    r"annoying|noisy|desinstala|desactiva|desactivar|quitar|apaga|apagar|"
    r"molesta|molestando|roto|rompe)(?![a-z0-9_-])"
)

# When the alias arrives with no domain vocabulary at all, every score ties at
# zero and rank_skills breaks the tie alphabetically -- naming whichever skill
# sorts first with no evidence behind it. solve-efficiently is the workflow
# that declares itself the answer to a bare request for the plugin, so it is
# named explicitly instead of by accident of sort order.
PLUGIN_DEFAULT_SKILL = "solve-efficiently"


def decide(query: str, descriptions: Mapping[str, str]) -> dict[str, object]:
    """Whether to name a skill for this prompt, and which one.

    Lives beside ``rank_skills`` for the reason in the module docstring: the
    thresholds are as much of the routing decision as the ordering is, and a
    hook holding its own copy of them can pass every checked-in case while
    staying silent at runtime. That is not hypothetical -- it is what shipped.
    The benchmark scored ranking only, reported 0.96, and never observed that
    a third of those ranked-first prompts produced no suggestion at all.
    """
    if len(descriptions) < 2:
        # Margin is meaningless without a runner-up to compare against.
        return {"status": "skipped", "reason": "not enough skills to rank"}

    ranking = rank_skills(query, descriptions)
    name, score = ranking[0]
    margin = round(score - ranking[1][1], 8)
    shared = shared_token_count(query, name, descriptions)
    outcome: dict[str, object] = {
        "status": "suggested",
        "skill": name,
        "score": score,
        "margin": margin,
        "shared_tokens": shared,
    }
    blocked = dict(outcome, status="below-threshold")
    folded = _fold(query.casefold())

    # An explicit request is not an inference, so it clears the evidence gates:
    # the user named the skill, or named the plugin and left the choice of
    # workflow to the ranking. It does not clear the tie gate. Naming two
    # skills is not a request for one of them, and answering "solve
    # efficiently and communicate efficiently" with whichever won by 0.009 is
    # the same coin-flip the tie rule exists to refuse.
    if score >= EXPLICIT_REQUEST_SCORE:
        if margin < MIN_MARGIN:
            return dict(blocked, reason="named two skills")
        return dict(outcome, reason="named skill")
    if PLUGIN_ALIAS_PATTERN.search(folded):
        if PLUGIN_COMPLAINT.search(folded):
            return dict(blocked, reason="named plugin as the subject")
        if score <= 0.0:
            # No domain vocabulary to rank on, so the order is alphabetical
            # noise. Name the declared default, or say nothing if it is absent.
            if PLUGIN_DEFAULT_SKILL not in descriptions:
                return dict(blocked, reason="no default workflow installed")
            return dict(outcome, skill=PLUGIN_DEFAULT_SKILL, reason="named plugin")
        return dict(outcome, reason="named plugin")
    if shared < MIN_SHARED_TOKENS:
        return dict(blocked, reason="too few shared words")
    if score < MIN_SCORE:
        return dict(blocked, reason="weak match")
    if margin < MIN_MARGIN:
        return dict(blocked, reason="near tie")
    if score < CONFIDENT_SCORE and margin < DECISIVE_MARGIN:
        return dict(blocked, reason="no clear winner")
    return dict(outcome, reason="description match")


def description_collisions(
    descriptions: Mapping[str, str], threshold: float
) -> list[dict[str, object]]:
    names = list(descriptions)
    documents = [tokenize(descriptions[name]) for name in names]
    idf = _idf(documents)
    vectors = [
        _vector(tokens, idf, _unseen_weight(len(documents))) for tokens in documents
    ]
    collisions: list[dict[str, object]] = []
    for left_index, left_name in enumerate(names):
        for right_index in range(left_index + 1, len(names)):
            similarity = _cosine(vectors[left_index], vectors[right_index])
            if similarity >= threshold:
                collisions.append(
                    {
                        "left": left_name,
                        "right": names[right_index],
                        "similarity": round(similarity, 4),
                    }
                )
    return sorted(
        collisions, key=lambda item: (-float(item["similarity"]), str(item["left"]))
    )
