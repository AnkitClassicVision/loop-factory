#!/usr/bin/env python3
"""Deterministic substance gate for podcast promotional copy."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# These examples document the contractions reviewers repeatedly expect to hear.
CONTRACTION_EXAMPLES = [
    "don't",
    "isn't",
    "he's",
    "you're",
    "I'm",
    "didn't",
    "wasn't",
    "it's",
    "that's",
    "we've",
    "they're",
    "can't",
    "won't",
    "there's",
    "here's",
    "who's",
    "let's",
    "you'll",
    "I'd",
    "he'd",
]

# Items over 25 words must demonstrate conversational contraction use.
CONTRACTION_MIN_WORDS = 25

# A 0.004 default catches zero-contraction drafts without demanding heavy slang.
DEFAULT_MIN_CONTRACTION_RATE = 0.004

# These phrases are mechanical reviewer rejects, regardless of surrounding copy.
BANNED_PHRASES = [
    "delve",
    "dive into",
    "unpack",
    "leverage",
    "utilize",
    "optimize",
    "robust",
    "seamless",
    "streamline",
    "foster",
    "cultivate",
    "bolster",
    "landscape",
    "ecosystem",
    "holistic",
    "alignment",
    "synergy",
    "bandwidth",
    "revolutionary",
    "game-changing",
    "cutting-edge",
    "elevate",
    "transform",
    "unlock",
    "comprehensive",
    "passionate",
    "I'd be happy to",
    "Great question",
    "It's important to note",
    "Don't miss out",
    "But wait, there's more",
    "We're excited to",
    "just checking in",
    "circling back",
    "touching base",
    "hope this email finds you well",
    "I hope you're doing well",
    "wanted to reach out",
    "I came across",
    "your reviews tell the story",
    "that's on me",
    "my bad",
    "I owe you a note",
    "smart evolution",
    "no agenda here",
    "just wanted to say",
]

# Either of these dash forms reproduces the prohibited emphasis construction.
EMPHASIS_DASH_PATTERNS = ["\u2014", r"[ \t]-[ \t]"]

# Answers of one to three words after a question create the rejected cadence.
SELF_ANSWER_MAX_WORDS = 3

# A post may make one distinct kind of CTA, even if that signal repeats.
MAX_DISTINCT_CTA_SIGNALS = 1

# A runtime mismatch over two minutes is materially misleading.
RUNTIME_TOLERANCE_MIN = 2.0

# Sentences over 38 words reproduce the run-on cadence named in rejections.
MAX_SENTENCE_WORDS = 38

# Four-word shingles catch "one post at three lengths" without matching trivia.
SHINGLE_WORDS = 4
CROSS_ITEM_JACCARD_THRESHOLD = 0.5
MIN_DUPLICATE_SENTENCE_WORDS = 8

# These leading verbs are CTA directives only when used imperatively.
CTA_DIRECTIVES = ["listen", "hear", "watch", "check", "read", "tune"]

# These openers make an unnumbered population claim statistic-shaped.
UNSOURCED_QUANTIFIERS = [
    "most",
    "the majority of",
    "nearly all",
    "hardly any",
    "few",
]

# These nouns identify the population being generalized about.
POPULATION_NOUNS = [
    "people",
    "doctors",
    "physicians",
    "optometrists",
    "patients",
    "owners",
    "businesses",
    "practices",
    "clinics",
    "teams",
    "employees",
    "workers",
    "guests",
    "listeners",
    "customers",
    "clinicians",
    "providers",
]

# These verbs turn a population reference into an outcome claim.
OUTCOME_VERBS = [
    "leave",
    "leaves",
    "left",
    "quit",
    "quits",
    "fail",
    "fails",
    "stop",
    "stops",
    "return",
    "returns",
    "come",
    "comes",
    "buy",
    "buys",
    "choose",
    "chooses",
    "prefer",
    "prefers",
    "suffer",
    "suffers",
    "experience",
    "experiences",
    "struggle",
    "struggles",
    "burn",
    "burns",
    "improve",
    "improves",
    "grow",
    "grows",
    "lose",
    "loses",
    "gain",
    "gains",
    "make",
    "makes",
    "use",
    "uses",
    "need",
    "needs",
    "want",
    "wants",
    "say",
    "says",
    "report",
    "reports",
]

# These markers count as explicit attribution for a statistic-shaped sentence.
ATTRIBUTION_MARKERS = [
    "according to",
    "survey",
    "study",
    "a report",
    "the report",
    "reported by",
    "data",
    "research",
    "source",
    "cited",
]

RULE_RATIONALES = {
    "CONTRACTIONS": "Zero or scarce contractions was the strongest repeated AI-voice tell.",
    "BANNED_PHRASES": "These stock phrases were repeatedly rejected as AI-written.",
    "NO_EM_DASH": "House style uses commas or periods instead of emphasis dashes.",
    "SELF_ANSWERED_QUESTION": "A question followed by a tiny answer is a named AI cadence.",
    "ONE_CTA": "A post should make no more than one distinct kind of ask.",
    "NO_CROSS_ITEM_REUSE": "Posts must not be one draft reproduced at different lengths.",
    "LINK_CONSISTENCY": "Mixed link mechanisms break templating and tracking.",
    "UNSOURCED_STAT": "Population outcome claims need a number and attribution or a hedge.",
    "RUNTIME_CLAIM": "Asserted runtime must agree with the measured episode duration.",
    "SENTENCE_LENGTH": "Long run-ons were a repeated human-voice rejection.",
}

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['\u2019][A-Za-z]{1,2})?")
GENERIC_CONTRACTION_RE = re.compile(r"[A-Za-z]+['\u2019][A-Za-z]{1,2}", re.IGNORECASE)
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]+|[^.!?]+$", re.MULTILINE)
PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*(?:link|url)[^{}]*\}\}", re.IGNORECASE)
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
LINK_IN_BIO_RE = re.compile(r"(?<!\w)link\s+in\s+(?:the\s+)?bio(?!\w)", re.IGNORECASE)
IMPERATIVE_CTA_RE = re.compile(
    r"(?:^|[.!?:;]\s+|\n)\s*(?:please\s+)?"
    r"(?:listen|hear|watch|check|read|tune(?:\s+in)?)\b",
    re.IGNORECASE,
)
RUNTIME_RE = re.compile(
    r"(?<!\w)(?:worth\s+|in\s+)?"
    r"(?P<minutes>\d+(?:\.\d+)?)\s*(?:-\s*)?(?:minutes?|mins?)\b",
    re.IGNORECASE,
)


class InputProblem(ValueError):
    """Raised when content cannot be measured safely."""


class VoiceArgumentParser(argparse.ArgumentParser):
    """Make argparse failures conform to the UNMEASURABLE contract."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"UNMEASURABLE: {message}\n")


@dataclass(frozen=True)
class TextItem:
    name: str
    text: str
    is_post: bool


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def sentences(text: str) -> list[str]:
    return [match.group(0).strip() for match in SENTENCE_RE.finditer(text) if match.group(0).strip()]


def excerpt(text: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "\u2026"


def finding(
    rule: str,
    status: str,
    why: str,
    metrics: dict[str, Any],
    excerpts: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "rule": rule,
        "status": status,
        "rationale": RULE_RATIONALES[rule],
        "why": why,
        "metrics": metrics,
        "offending_excerpts": [excerpt(value) for value in excerpts],
    }


def normalize_apostrophes(text: str) -> str:
    return text.replace("\u2019", "'")


def phrase_pattern(phrase: str) -> re.Pattern[str]:
    normalized = normalize_apostrophes(phrase)
    pieces = [re.escape(piece) for piece in normalized.split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(pieces) + r"(?!\w)", re.IGNORECASE)


BANNED_PATTERNS = [(phrase, phrase_pattern(phrase)) for phrase in BANNED_PHRASES]
EXPLICIT_CONTRACTIONS = {normalize_apostrophes(value).lower() for value in CONTRACTION_EXAMPLES}


def contraction_finding(text: str, minimum_rate: float) -> dict[str, Any]:
    tokens = words(text)
    contraction_tokens = [
        token
        for token in tokens
        if normalize_apostrophes(token).lower() in EXPLICIT_CONTRACTIONS
        or GENERIC_CONTRACTION_RE.fullmatch(token)
    ]
    word_count = len(tokens)
    contraction_count = len(contraction_tokens)
    rate = contraction_count / word_count if word_count else 0.0
    metrics = {
        "word_count": word_count,
        "contraction_count": contraction_count,
        "contraction_rate": rate,
        "minimum_rate": minimum_rate,
        "applies_over_words": CONTRACTION_MIN_WORDS,
    }
    if word_count <= CONTRACTION_MIN_WORDS:
        return finding(
            "CONTRACTIONS",
            "PASS",
            f"Short item exemption applies at {word_count} words; measured rate is {rate:.6f}.",
            metrics,
        )
    if rate < minimum_rate:
        return finding(
            "CONTRACTIONS",
            "FAIL",
            f"Rate {rate:.6f} from {contraction_count}/{word_count} is below {minimum_rate:.6f}.",
            metrics,
            [text],
        )
    return finding(
        "CONTRACTIONS",
        "PASS",
        f"Rate {rate:.6f} from {contraction_count}/{word_count} meets {minimum_rate:.6f}.",
        metrics,
    )


def banned_phrase_finding(text: str) -> dict[str, Any]:
    normalized = normalize_apostrophes(text)
    matches: list[str] = []
    snippets: list[str] = []
    for label, pattern in BANNED_PATTERNS:
        for match in pattern.finditer(normalized):
            matches.append(label)
            snippets.append(match.group(0))
    metrics = {
        "banned_phrase_count": len(matches),
        "distinct_banned_phrase_count": len(set(value.lower() for value in matches)),
        "banned_list_size": len(BANNED_PHRASES),
        "matches": matches,
    }
    if matches:
        return finding(
            "BANNED_PHRASES",
            "FAIL",
            f"Found {len(matches)} banned phrase occurrence(s): {', '.join(matches)}.",
            metrics,
            snippets,
        )
    return finding(
        "BANNED_PHRASES",
        "PASS",
        f"Found 0 matches across {len(BANNED_PHRASES)} banned phrases.",
        metrics,
    )


def no_em_dash_finding(text: str) -> dict[str, Any]:
    em_dash_count = text.count(EMPHASIS_DASH_PATTERNS[0])
    spaced_hyphen_matches = list(re.finditer(EMPHASIS_DASH_PATTERNS[1], text))
    bad_count = em_dash_count + len(spaced_hyphen_matches)
    metrics = {
        "em_dash_count": em_dash_count,
        "spaced_hyphen_count": len(spaced_hyphen_matches),
        "total_emphasis_dash_count": bad_count,
    }
    if bad_count:
        bad_sentences = [
            sentence
            for sentence in sentences(text)
            if EMPHASIS_DASH_PATTERNS[0] in sentence
            or re.search(EMPHASIS_DASH_PATTERNS[1], sentence)
        ]
        return finding(
            "NO_EM_DASH",
            "FAIL",
            f"Found {bad_count} emphasis dash occurrence(s); replace each with a comma or period.",
            metrics,
            bad_sentences or [text],
        )
    return finding(
        "NO_EM_DASH",
        "PASS",
        "Found 0 em dashes and 0 spaced emphasis hyphens.",
        metrics,
    )


def self_answered_question_finding(text: str) -> dict[str, Any]:
    sentence_list = sentences(text)
    matches: list[str] = []
    answer_word_counts: list[int] = []
    for question, answer in zip(sentence_list, sentence_list[1:]):
        answer_count = len(words(answer))
        if (
            question.rstrip().endswith("?")
            and 1 <= answer_count <= SELF_ANSWER_MAX_WORDS
        ):
            matches.append(f"{question} {answer}")
            answer_word_counts.append(answer_count)
    metrics = {
        "sentence_count": len(sentence_list),
        "self_answered_question_count": len(matches),
        "answer_word_counts": answer_word_counts,
        "maximum_short_answer_words": SELF_ANSWER_MAX_WORDS,
    }
    if matches:
        return finding(
            "SELF_ANSWERED_QUESTION",
            "FAIL",
            f"Found {len(matches)} question(s) immediately followed by a 1-to-3-word answer.",
            metrics,
            matches,
        )
    return finding(
        "SELF_ANSWERED_QUESTION",
        "PASS",
        f"Found 0 short self-answers across {len(sentence_list)} sentences.",
        metrics,
    )


def is_direct_audience_question(sentence: str) -> bool:
    if not sentence.rstrip().endswith("?"):
        return False
    lowered = sentence.lower()
    if re.search(r"\b(?:you|your|yours|yourself|yourselves)\b", lowered):
        return True
    return bool(
        re.match(
            r"^\s*(?:ever|what|why|how|which|would|do|did|are|have|has|can|"
            r"could|will|should)\b",
            lowered,
        )
    )


def one_cta_finding(text: str, is_post: bool) -> dict[str, Any]:
    if not is_post:
        return finding(
            "ONE_CTA",
            "SKIPPED",
            "This rule applies only to posts.",
            {"applies": False, "distinct_signal_type_count": 0, "signal_types": []},
        )

    directive_matches = [match.group(0).strip() for match in IMPERATIVE_CTA_RE.finditer(text)]
    link_matches = [match.group(0) for match in URL_RE.finditer(text)]
    link_matches.extend(match.group(0) for match in PLACEHOLDER_RE.finditer(text))
    question_matches = [value for value in sentences(text) if is_direct_audience_question(value)]

    signals: dict[str, list[str]] = {}
    if directive_matches:
        signals["imperative_directive"] = directive_matches
    if link_matches:
        signals["url_or_link_placeholder"] = link_matches
    if question_matches:
        signals["direct_audience_question"] = question_matches

    signal_types = sorted(signals)
    signal_occurrence_count = sum(len(value) for value in signals.values())
    metrics = {
        "applies": True,
        "distinct_signal_type_count": len(signal_types),
        "signal_occurrence_count": signal_occurrence_count,
        "maximum_distinct_signal_types": MAX_DISTINCT_CTA_SIGNALS,
        "signal_types": signal_types,
        "signal_occurrences": signals,
    }
    if len(signal_types) > MAX_DISTINCT_CTA_SIGNALS:
        all_matches = [value for values in signals.values() for value in values]
        return finding(
            "ONE_CTA",
            "FAIL",
            f"Found {len(signal_types)} distinct CTA signal types: {', '.join(signal_types)}.",
            metrics,
            all_matches,
        )
    return finding(
        "ONE_CTA",
        "PASS",
        f"Found {len(signal_types)} distinct CTA signal type(s): "
        f"{', '.join(signal_types) if signal_types else 'none'}.",
        metrics,
    )


def unsourced_stat_finding(text: str) -> dict[str, Any]:
    sentence_list = sentences(text)
    offenders: list[str] = []
    for sentence in sentence_list:
        normalized = normalize_apostrophes(sentence).lower().strip()
        opener = re.match(
            r"^(?:most|the\s+majority\s+of|nearly\s+all|hardly\s+any|few)\b",
            normalized,
        )
        if not opener:
            continue
        if re.search(r"\d|%", normalized):
            continue
        if any(marker in normalized for marker in ATTRIBUTION_MARKERS):
            continue
        population = re.search(
            r"\b(?:" + "|".join(re.escape(value) for value in POPULATION_NOUNS) + r")\b",
            normalized[opener.end() :],
        )
        if not population:
            continue
        population_end = opener.end() + population.end()
        outcome = re.search(
            r"\b(?:" + "|".join(re.escape(value) for value in OUTCOME_VERBS) + r")\b",
            normalized[population_end:],
        )
        if outcome:
            offenders.append(sentence)
    metrics = {
        "sentence_count": len(sentence_list),
        "unsourced_stat_count": len(offenders),
        "quantifier_count": len(UNSOURCED_QUANTIFIERS),
        "population_noun_count": len(POPULATION_NOUNS),
        "outcome_verb_count": len(OUTCOME_VERBS),
        "attribution_marker_count": len(ATTRIBUTION_MARKERS),
    }
    if offenders:
        return finding(
            "UNSOURCED_STAT",
            "FAIL",
            f"Found {len(offenders)} unnumbered, unattributed population outcome claim(s); hedge or cite.",
            metrics,
            offenders,
        )
    return finding(
        "UNSOURCED_STAT",
        "PASS",
        f"Found 0 unsourced statistic-shaped claims across {len(sentence_list)} sentences.",
        metrics,
    )


def runtime_claim_finding(text: str, episode_duration_s: float | None) -> dict[str, Any]:
    matches = list(RUNTIME_RE.finditer(text))
    claims = [float(match.group("minutes")) for match in matches]
    if episode_duration_s is None:
        return finding(
            "RUNTIME_CLAIM",
            "SKIPPED",
            "No --episode-duration-s was supplied, so runtime accuracy cannot be measured and is never guessed.",
            {
                "claim_count": len(claims),
                "claimed_minutes": claims,
                "episode_duration_s": None,
                "true_duration_minutes": None,
                "tolerance_minutes": RUNTIME_TOLERANCE_MIN,
            },
        )

    true_minutes = episode_duration_s / 60.0
    differences = [abs(value - true_minutes) for value in claims]
    bad_indexes = [
        index for index, difference in enumerate(differences) if difference > RUNTIME_TOLERANCE_MIN
    ]
    metrics = {
        "claim_count": len(claims),
        "claimed_minutes": claims,
        "episode_duration_s": episode_duration_s,
        "true_duration_minutes": true_minutes,
        "absolute_differences_minutes": differences,
        "tolerance_minutes": RUNTIME_TOLERANCE_MIN,
        "mismatch_count": len(bad_indexes),
    }
    if bad_indexes:
        bad_matches = [matches[index].group(0) for index in bad_indexes]
        return finding(
            "RUNTIME_CLAIM",
            "FAIL",
            f"Found {len(bad_indexes)} runtime claim(s) more than "
            f"{RUNTIME_TOLERANCE_MIN:.1f} minutes from the true {true_minutes:.3f} minutes.",
            metrics,
            bad_matches,
        )
    return finding(
        "RUNTIME_CLAIM",
        "PASS",
        f"Found {len(claims)} runtime claim(s), all within "
        f"{RUNTIME_TOLERANCE_MIN:.1f} minutes of {true_minutes:.3f}.",
        metrics,
    )


def sentence_length_finding(text: str) -> dict[str, Any]:
    sentence_list = sentences(text)
    lengths = [len(words(value)) for value in sentence_list]
    offenders = [
        value for value, length in zip(sentence_list, lengths) if length > MAX_SENTENCE_WORDS
    ]
    metrics = {
        "sentence_count": len(sentence_list),
        "sentence_word_counts": lengths,
        "maximum_measured_sentence_words": max(lengths, default=0),
        "maximum_allowed_sentence_words": MAX_SENTENCE_WORDS,
        "over_limit_count": len(offenders),
    }
    if offenders:
        return finding(
            "SENTENCE_LENGTH",
            "FAIL",
            f"Found {len(offenders)} sentence(s) over {MAX_SENTENCE_WORDS} words.",
            metrics,
            offenders,
        )
    return finding(
        "SENTENCE_LENGTH",
        "PASS",
        f"Longest sentence is {max(lengths, default=0)} words; limit is {MAX_SENTENCE_WORDS}.",
        metrics,
    )


def item_findings(
    item: TextItem,
    minimum_contraction_rate: float,
    episode_duration_s: float | None,
) -> list[dict[str, Any]]:
    return [
        contraction_finding(item.text, minimum_contraction_rate),
        banned_phrase_finding(item.text),
        no_em_dash_finding(item.text),
        self_answered_question_finding(item.text),
        one_cta_finding(item.text, item.is_post),
        unsourced_stat_finding(item.text),
        runtime_claim_finding(item.text, episode_duration_s),
        sentence_length_finding(item.text),
    ]


def normalized_sentence(value: str) -> str:
    lowered = normalize_apostrophes(value).lower()
    lowered = re.sub(r"[^a-z0-9\s]", "", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def shingles(text: str, size: int = SHINGLE_WORDS) -> set[tuple[str, ...]]:
    normalized_words = re.findall(r"[a-z0-9]+", normalize_apostrophes(text).lower())
    return {
        tuple(normalized_words[index : index + size])
        for index in range(len(normalized_words) - size + 1)
    }


def no_cross_item_reuse_finding(posts: list[TextItem]) -> dict[str, Any]:
    sentence_owners: dict[str, list[str]] = {}
    for post in posts:
        for sentence in sentences(post.text):
            normalized = normalized_sentence(sentence)
            if len(normalized.split()) >= MIN_DUPLICATE_SENTENCE_WORDS:
                sentence_owners.setdefault(normalized, []).append(post.name)

    duplicates = {
        sentence: owners
        for sentence, owners in sentence_owners.items()
        if len(set(owners)) > 1
    }

    pair_scores: list[dict[str, Any]] = []
    similar_pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(posts):
        left_shingles = shingles(left.text)
        for right in posts[left_index + 1 :]:
            right_shingles = shingles(right.text)
            union = left_shingles | right_shingles
            score = len(left_shingles & right_shingles) / len(union) if union else 0.0
            pair = {
                "left": left.name,
                "right": right.name,
                "score": score,
                "left_shingle_count": len(left_shingles),
                "right_shingle_count": len(right_shingles),
                "shared_shingle_count": len(left_shingles & right_shingles),
                "union_shingle_count": len(union),
            }
            pair_scores.append(pair)
            if score >= CROSS_ITEM_JACCARD_THRESHOLD:
                similar_pairs.append(pair)

    metrics = {
        "post_count": len(posts),
        "minimum_duplicate_sentence_words": MIN_DUPLICATE_SENTENCE_WORDS,
        "duplicate_sentence_count": len(duplicates),
        "shingle_words": SHINGLE_WORDS,
        "jaccard_threshold": CROSS_ITEM_JACCARD_THRESHOLD,
        "compared_pair_count": len(pair_scores),
        "similar_pair_count": len(similar_pairs),
        "pair_scores": pair_scores,
    }
    if duplicates or similar_pairs:
        bad_excerpts = [
            f'"{sentence}" reused by {", ".join(sorted(set(owners)))}'
            for sentence, owners in duplicates.items()
        ]
        bad_excerpts.extend(
            f"{pair['left']} vs {pair['right']}: Jaccard {pair['score']:.6f}"
            for pair in similar_pairs
        )
        return finding(
            "NO_CROSS_ITEM_REUSE",
            "FAIL",
            f"Found {len(duplicates)} duplicated sentence(s) and "
            f"{len(similar_pairs)} post pair(s) at or above "
            f"{CROSS_ITEM_JACCARD_THRESHOLD:.2f} Jaccard.",
            metrics,
            bad_excerpts,
        )
    return finding(
        "NO_CROSS_ITEM_REUSE",
        "PASS",
        f"Found 0 duplicated sentences and 0 similar pairs across {len(posts)} posts.",
        metrics,
    )


def link_consistency_finding(posts: list[TextItem]) -> dict[str, Any]:
    placeholder_posts = [
        {"name": post.name, "matches": [match.group(0) for match in PLACEHOLDER_RE.finditer(post.text)]}
        for post in posts
        if PLACEHOLDER_RE.search(post.text)
    ]
    bio_posts = [
        {"name": post.name, "matches": [match.group(0) for match in LINK_IN_BIO_RE.finditer(post.text)]}
        for post in posts
        if LINK_IN_BIO_RE.search(post.text)
    ]
    mixed_pairs = [
        (placeholder_post, bio_post)
        for placeholder_post in placeholder_posts
        for bio_post in bio_posts
        if placeholder_post["name"] != bio_post["name"]
    ]
    metrics = {
        "post_count": len(posts),
        "placeholder_post_count": len(placeholder_posts),
        "link_in_bio_post_count": len(bio_posts),
        "mixed_mechanism_pair_count": len(mixed_pairs),
        "placeholder_posts": placeholder_posts,
        "link_in_bio_posts": bio_posts,
    }
    if mixed_pairs:
        snippets = [
            f"{placeholder_post['name']} uses {placeholder_post['matches'][0]}; "
            f"{bio_post['name']} uses {bio_post['matches'][0]}"
            for placeholder_post, bio_post in mixed_pairs
        ]
        return finding(
            "LINK_CONSISTENCY",
            "FAIL",
            f"Found {len(mixed_pairs)} cross-post placeholder/link-in-bio mismatch pair(s).",
            metrics,
            snippets,
        )
    return finding(
        "LINK_CONSISTENCY",
        "PASS",
        f"Found {len(placeholder_posts)} placeholder post(s), {len(bio_posts)} "
        "link-in-bio post(s), and 0 cross-post mismatches.",
        metrics,
    )


def require_text(value: Any, location: str) -> str:
    if not isinstance(value, str):
        raise InputProblem(f"{location} must be a string.")
    if not value.strip():
        raise InputProblem(f"{location} is blank.")
    return value


def flat_item_is_post(name: str) -> bool:
    """Flat items are posts unless their name clearly identifies non-post copy."""
    return not bool(
        re.search(
            r"\b(?:email|e-mail|subject|show\s*notes?|episode\s*notes?)\b",
            name,
            re.IGNORECASE,
        )
    )


def normalize_content(data: Any) -> tuple[str, list[TextItem]]:
    items: list[TextItem] = []
    if isinstance(data, list):
        detected_shape = "flat list (b)"
        for index, raw_item in enumerate(data, start=1):
            if not isinstance(raw_item, dict):
                raise InputProblem(f"flat item {index} must be an object.")
            name = raw_item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise InputProblem(f"flat item {index}.name must be a nonblank string.")
            text = require_text(raw_item.get("text"), f"flat item {index}.text")
            clean_name = name.strip()
            items.append(
                TextItem(
                    name=clean_name,
                    text=text,
                    is_post=flat_item_is_post(clean_name),
                )
            )
    elif isinstance(data, dict):
        detected_shape = "structured object (a)"
        recognized = {"posts", "email", "show_notes"}
        if not any(key in data for key in recognized):
            raise InputProblem("structured content has none of: posts, email, show_notes.")

        if "posts" in data:
            raw_posts = data["posts"]
            if not isinstance(raw_posts, list):
                raise InputProblem("posts must be a list.")
            for index, raw_post in enumerate(raw_posts, start=1):
                if not isinstance(raw_post, dict):
                    raise InputProblem(f"posts[{index - 1}] must be an object.")
                platform = raw_post.get("platform")
                platform_label = (
                    platform.strip()
                    if isinstance(platform, str) and platform.strip()
                    else "unspecified platform"
                )
                text = require_text(raw_post.get("text"), f"posts[{index - 1}].text")
                items.append(
                    TextItem(
                        name=f"post {index} ({platform_label})",
                        text=text,
                        is_post=True,
                    )
                )

        if "email" in data:
            raw_email = data["email"]
            if not isinstance(raw_email, dict):
                raise InputProblem("email must be an object.")
            subject = require_text(raw_email.get("subject"), "email.subject")
            body = require_text(raw_email.get("body"), "email.body")
            items.append(TextItem(name="email subject", text=subject, is_post=False))
            items.append(TextItem(name="email body", text=body, is_post=False))

        if "show_notes" in data:
            show_notes = require_text(data["show_notes"], "show_notes")
            items.append(TextItem(name="show notes", text=show_notes, is_post=False))
    else:
        raise InputProblem("top-level JSON must be a structured object or a flat list.")

    if not items:
        raise InputProblem("content contains zero text items.")
    return detected_shape, items


def evaluate_data(
    data: Any,
    minimum_contraction_rate: float,
    episode_duration_s: float | None,
) -> dict[str, Any]:
    try:
        detected_shape, items = normalize_content(data)
    except InputProblem as exc:
        return {
            "exit_code": 2,
            "overall": "UNMEASURABLE",
            "detected_shape": None,
            "why": str(exc),
            "items": [],
            "global_findings": [],
        }

    item_reports = [
        {
            "name": item.name,
            "kind": "post" if item.is_post else "text",
            "character_count": len(item.text),
            "findings": item_findings(item, minimum_contraction_rate, episode_duration_s),
        }
        for item in items
    ]
    posts = [item for item in items if item.is_post]
    global_findings = [
        no_cross_item_reuse_finding(posts),
        link_consistency_finding(posts),
    ]
    all_findings = [
        report
        for item_report in item_reports
        for report in item_report["findings"]
    ] + global_findings
    failed = any(report["status"] == "FAIL" for report in all_findings)
    return {
        "exit_code": 1 if failed else 0,
        "overall": "FAIL" if failed else "PASS",
        "detected_shape": detected_shape,
        "why": (
            "At least one deterministic voice rule failed."
            if failed
            else "Every applicable deterministic voice rule passed."
        ),
        "minimum_contraction_rate": minimum_contraction_rate,
        "episode_duration_s": episode_duration_s,
        "item_count": len(items),
        "post_count": len(posts),
        "items": item_reports,
        "global_findings": global_findings,
    }


def read_and_evaluate(
    content_path: str,
    minimum_contraction_rate: float,
    episode_duration_s: float | None,
) -> dict[str, Any]:
    if not content_path.strip():
        return {
            "exit_code": 2,
            "overall": "UNMEASURABLE",
            "detected_shape": None,
            "why": "--content is empty.",
            "items": [],
            "global_findings": [],
        }
    path = Path(content_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "exit_code": 2,
            "overall": "UNMEASURABLE",
            "detected_shape": None,
            "why": f"cannot read --content {path}: {exc}",
            "items": [],
            "global_findings": [],
        }
    if not raw.strip():
        return {
            "exit_code": 2,
            "overall": "UNMEASURABLE",
            "detected_shape": None,
            "why": f"--content {path} is empty.",
            "items": [],
            "global_findings": [],
        }
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {
            "exit_code": 2,
            "overall": "UNMEASURABLE",
            "detected_shape": None,
            "why": f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}",
            "items": [],
            "global_findings": [],
        }
    return evaluate_data(data, minimum_contraction_rate, episode_duration_s)


def metrics_text(metrics: dict[str, Any]) -> str:
    return json.dumps(metrics, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))


def render_finding(report: dict[str, Any], indent: str = "  ") -> None:
    print(f"{indent}[{report['status']}] {report['rule']}: {report['why']}")
    print(f"{indent}  RATIONALE: {report['rationale']}")
    print(f"{indent}  MEASURED: {metrics_text(report['metrics'])}")
    for bad_excerpt in report["offending_excerpts"]:
        print(f'{indent}  OFFENDING: "{bad_excerpt}"')


def render_report(report: dict[str, Any]) -> None:
    print(f"OVERALL: {report['overall']} (exit {report['exit_code']})")
    if report["detected_shape"] is None:
        print("DETECTED SHAPE: none")
        print(f"WHY: {report['why']}")
        return
    print(f"DETECTED SHAPE: {report['detected_shape']}")
    print(
        "MEASURED: "
        f"items={report['item_count']}, posts={report['post_count']}, "
        f"min_contraction_rate={report['minimum_contraction_rate']:.6f}, "
        f"episode_duration_s={report['episode_duration_s']!r}"
    )
    print(f"WHY: {report['why']}")
    for item in report["items"]:
        print(
            f"\nITEM: {item['name']} "
            f"(kind={item['kind']}, characters={item['character_count']})"
        )
        for item_finding in item["findings"]:
            render_finding(item_finding)
    print("\nCROSS-POST RULES:")
    for global_finding in report["global_findings"]:
        render_finding(global_finding)


def all_findings(report: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for item in report.get("items", []):
        yield from item["findings"]
    yield from report.get("global_findings", [])


def rule_failed(report: dict[str, Any], rule: str) -> bool:
    return any(
        value["rule"] == rule and value["status"] == "FAIL"
        for value in all_findings(report)
    )


def run_self_test() -> int:
    repeated_sentence = (
        "licensed in Ontario, Manitoba, and Newfoundland and building a travel optometry team."
    )
    red_fixtures: list[tuple[str, str, Any, float | None]] = [
        (
            "contractions",
            "CONTRACTIONS",
            [
                {
                    "name": "rejected post",
                    "text": (
                        "He is now building a team of travel optometrists. "
                        "He is licensed in Ontario. If you have ever struggled "
                        "to cover exam lanes, this is the episode."
                    ),
                }
            ],
            None,
        ),
        (
            "self_answered_question",
            "SELF_ANSWERED_QUESTION",
            [{"name": "rejected post", "text": "What made it possible? Burnout."}],
            None,
        ),
        (
            "unsourced_stat",
            "UNSOURCED_STAT",
            [
                {
                    "name": "rejected post",
                    "text": "Most doctors who burn out leave optometry and don't come back.",
                }
            ],
            None,
        ),
        (
            "runtime_claim",
            "RUNTIME_CLAIM",
            [{"name": "rejected post", "text": "this one is worth 45 minutes"}],
            2218.0,
        ),
        (
            "cross_item_reuse",
            "NO_CROSS_ITEM_REUSE",
            {
                "posts": [
                    {"platform": "LinkedIn", "text": repeated_sentence},
                    {"platform": "X", "text": repeated_sentence},
                ]
            },
            None,
        ),
        (
            "link_consistency",
            "LINK_CONSISTENCY",
            {
                "posts": [
                    {"platform": "LinkedIn", "text": "The full episode is here: {{episode_link}}"},
                    {"platform": "X", "text": "Link in bio."},
                ]
            },
            None,
        ),
        (
            "one_cta",
            "ONE_CTA",
            {
                "posts": [
                    {
                        "platform": "LinkedIn",
                        "text": (
                            "Listen to the conversation at {{episode_link}}. "
                            "What would you try first?"
                        ),
                    }
                ]
            },
            None,
        ),
        (
            "sentence_length",
            "SENTENCE_LENGTH",
            [{"name": "rejected post", "text": " ".join(["word"] * 45) + "."}],
            None,
        ),
        (
            "banned_phrases",
            "BANNED_PHRASES",
            [{"name": "rejected post", "text": "Use it to leverage a seamless process."}],
            None,
        ),
        (
            "no_em_dash",
            "NO_EM_DASH",
            [{"name": "rejected post", "text": "The clinic was ready\u2014the schedule was not."}],
            None,
        ),
    ]

    green_fixture = {
        "posts": [
            {
                "platform": "LinkedIn",
                "text": (
                    "Marcus didn't leave patient care when coverage got hard. "
                    "He's licensed in three provinces and built a travel team after testing "
                    "the idea himself. The 20-minute conversation covers what the first "
                    "assignments changed. {{episode_link}}"
                ),
            },
            {
                "platform": "X",
                "text": (
                    "Coverage isn't theoretical when patients are already booked. "
                    "We talked about licensing delays, the first clinic request, and what "
                    "he'd change before taking another assignment. Would your practice use "
                    "a traveling optometrist during a packed month?"
                ),
            },
        ],
        "email": {
            "subject": "Your episode is ready",
            "body": (
                "Your episode's ready, and I've checked the title, notes, and playback. "
                "It doesn't use a public link yet. You'll get the final page after the "
                "scheduled review, so nothing needs to be shared today."
            ),
        },
        "show_notes": (
            "Marcus didn't start with a national plan. He'd already seen clinics lose "
            "appointment capacity when a doctor was away, so he tested one assignment, "
            "learned the licensing steps, and kept the model practical."
        ),
    }

    failures: list[str] = []
    for fixture_name, expected_rule, data, duration in red_fixtures:
        print(f"\nSELF-TEST RED FIXTURE: {fixture_name}")
        report = evaluate_data(data, 0.004, duration)
        render_report(report)
        if report["exit_code"] != 1:
            failures.append(
                f"{fixture_name}: expected exit 1, got {report['exit_code']}"
            )
        if not rule_failed(report, expected_rule):
            failures.append(f"{fixture_name}: {expected_rule} did not fail")

    print("\nSELF-TEST GREEN FIXTURE")
    green_report = evaluate_data(green_fixture, 0.004, 1200.0)
    render_report(green_report)
    if green_report["exit_code"] != 0:
        failures.append(f"green: expected exit 0, got {green_report['exit_code']}")

    print("\nSELF-TEST UNUSABLE FIXTURE")
    unusable_report = evaluate_data([], 0.004, None)
    render_report(unusable_report)
    if unusable_report["exit_code"] != 2:
        failures.append(
            f"unusable: expected exit 2, got {unusable_report['exit_code']}"
        )

    if failures:
        print(f"\nSELF-TEST: FAIL ({len(failures)} assertion failure(s))")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print(
        f"\nSELF-TEST: PASS "
        f"({len(red_fixtures)} red, 1 green, 1 unusable fixture)"
    )
    return 0


def write_json_report(path_value: str, report: dict[str, Any]) -> None:
    path = Path(path_value)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = VoiceArgumentParser(
        description="Deterministic human-voice gate for podcast promotional drafts."
    )
    parser.add_argument("--content", help="JSON file containing drafts")
    parser.add_argument(
        "--episode-duration-s",
        type=float,
        help="measured episode duration in seconds",
    )
    parser.add_argument(
        "--min-contraction-rate",
        type=float,
        default=DEFAULT_MIN_CONTRACTION_RATE,
        help="minimum contractions per word for items over 25 words (default: 0.004)",
    )
    parser.add_argument("--json", metavar="OUT", help="also write the full report as JSON")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run hermetic in-code fixtures",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.self_test and args.content is not None:
        parser.error("--self-test and --content are mutually exclusive")
    if not args.self_test and args.content is None:
        parser.error("one of --content or --self-test is required")
    if not math.isfinite(args.min_contraction_rate) or args.min_contraction_rate < 0:
        parser.error("--min-contraction-rate must be a finite number at or above 0")
    if args.episode_duration_s is not None and (
        not math.isfinite(args.episode_duration_s) or args.episode_duration_s <= 0
    ):
        parser.error("--episode-duration-s must be a finite number above 0")

    if args.self_test:
        if args.json:
            parser.error("--json is not supported with --self-test")
        return run_self_test()

    report = read_and_evaluate(
        args.content,
        args.min_contraction_rate,
        args.episode_duration_s,
    )
    render_report(report)
    if args.json:
        try:
            write_json_report(args.json, report)
        except OSError as exc:
            print(f"UNMEASURABLE: cannot write --json {args.json}: {exc}", file=sys.stderr)
            return 2
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
