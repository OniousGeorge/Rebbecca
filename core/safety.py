"""Finds safety emergencies in a customer's email, in code, so safety never depends on an LLM.

A phrase like "smell gas" matches when its words appear in that order, with up to MAX_GAP other
words between them and common word endings allowed. So it also matches "smelling gas",
"I can smell natural gas" and "smells like gas", but never part of a longer word.
"""
import re

MAX_GAP = 2
ENDINGS = ["s", "es", "ed", "ing", "y"]  # "smell" also matches smells, smelled, smelling, smelly


def words_of(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def _word_matches(phrase_word, word):
    if word == phrase_word:
        return True
    for ending in ENDINGS:
        if word == phrase_word + ending:
            return True
    if phrase_word.endswith("e") and word == phrase_word[:-1] + "ing":  # smoke -> smoking
        return True
    return False


def _phrase_found(phrase_words, words):
    for start in range(len(words)):
        if not _word_matches(phrase_words[0], words[start]):
            continue
        position = start
        matched = True
        for phrase_word in phrase_words[1:]:
            found_at = None
            last = min(position + 1 + MAX_GAP, len(words) - 1)
            for look in range(position + 1, last + 1):
                if _word_matches(phrase_word, words[look]):
                    found_at = look
                    break
            if found_at is None:
                matched = False
                break
            position = found_at
        if matched:
            return True
    return False


def find_emergency(text, phrases):
    """The first configured emergency phrase found in the text, or None."""
    words = words_of(text)
    for phrase in phrases:
        phrase_words = words_of(phrase)
        if len(phrase_words) > 0 and _phrase_found(phrase_words, words):
            return phrase
    return None
