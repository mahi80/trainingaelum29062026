"""Pure-python edit distance and CER / WER helpers.

No third-party dependencies. Used by ``ocr_eval`` (CER/WER) and by the
tree-edit-distance routine in the TEDS implementation.

Definitions
-----------
- Levenshtein distance: minimum number of single-token insertions, deletions and
  substitutions to turn ``a`` into ``b`` (unit costs).
- CER (Character Error Rate) = edit_distance(chars) / max(1, len(ref_chars)).
- WER (Word Error Rate)      = edit_distance(words) / max(1, len(ref_words)).

Both rates are computed over a *corpus* by summing distances and reference
lengths across all pairs (micro-average), which is the standard ASR/OCR
convention and is more stable than averaging per-line rates.
"""

from __future__ import annotations

from typing import Iterable, Sequence, Tuple


def levenshtein(a: Sequence, b: Sequence) -> int:
    """Return the unit-cost Levenshtein distance between two sequences.

    Works on any sequence of comparable, hashable tokens (str of chars, list of
    words, tuple of ints, ...). Uses the rolling two-row DP, O(len(a)*len(b))
    time and O(min(len)) space.
    """
    if a is b:
        return 0
    # Make ``b`` the shorter one to minimise the row width.
    if len(a) < len(b):
        a, b = b, a
    n, m = len(a), len(b)
    if m == 0:
        return n
    previous = list(range(m + 1))
    current = [0] * (m + 1)
    for i in range(1, n + 1):
        current[0] = i
        ai = a[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ai == b[j - 1] else 1
            current[j] = min(
                previous[j] + 1,        # deletion
                current[j - 1] + 1,     # insertion
                previous[j - 1] + cost,  # substitution / match
            )
        previous, current = current, previous
    return previous[m]


def normalized_levenshtein(a: Sequence, b: Sequence) -> float:
    """Edit distance scaled to [0, 1] by the longer length (0.0 == identical)."""
    denom = max(len(a), len(b))
    if denom == 0:
        return 0.0
    return levenshtein(a, b) / denom


def char_error_rate(reference: str, hypothesis: str) -> float:
    """CER for a single (reference, hypothesis) string pair."""
    ref = reference or ""
    hyp = hypothesis or ""
    if len(ref) == 0:
        return 0.0 if len(hyp) == 0 else 1.0
    return levenshtein(ref, hyp) / len(ref)


def word_error_rate(reference: str, hypothesis: str) -> float:
    """WER for a single (reference, hypothesis) string pair (whitespace split)."""
    ref = (reference or "").split()
    hyp = (hypothesis or "").split()
    if len(ref) == 0:
        return 0.0 if len(hyp) == 0 else 1.0
    return levenshtein(ref, hyp) / len(ref)


def _corpus_rate(
    pairs: Iterable[Tuple[str, str]],
    tokenizer,
) -> Tuple[float, int, int]:
    """Micro-averaged error rate over a corpus.

    Returns ``(rate, total_distance, total_ref_tokens)``.
    """
    total_dist = 0
    total_ref = 0
    for reference, hypothesis in pairs:
        ref_tokens = tokenizer(reference or "")
        hyp_tokens = tokenizer(hypothesis or "")
        total_dist += levenshtein(ref_tokens, hyp_tokens)
        total_ref += len(ref_tokens)
    rate = total_dist / total_ref if total_ref else (1.0 if total_dist else 0.0)
    return rate, total_dist, total_ref


def corpus_cer(pairs: Iterable[Tuple[str, str]]) -> Tuple[float, int, int]:
    """Micro-averaged CER over many pairs. Tokenizer = characters."""
    return _corpus_rate(pairs, tokenizer=lambda s: s)


def corpus_wer(pairs: Iterable[Tuple[str, str]]) -> Tuple[float, int, int]:
    """Micro-averaged WER over many pairs. Tokenizer = whitespace words."""
    return _corpus_rate(pairs, tokenizer=lambda s: s.split())


def lcs_length(a: Sequence, b: Sequence) -> int:
    """Length of the Longest Common Subsequence (used by GriTS content scoring)."""
    if not a or not b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for ai in a:
        curr = [0] * (len(b) + 1)
        for j, bj in enumerate(b, start=1):
            if ai == bj:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[len(b)]


if __name__ == "__main__":  # tiny smoke test
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("flaw", "lawn") == 2
    assert lcs_length("ABCBDAB", "BDCAB") == 4
    assert abs(char_error_rate("hello", "h3llo") - 0.2) < 1e-9
    assert abs(word_error_rate("the cat sat", "the dog sat") - (1 / 3)) < 1e-9
    print("levenshtein.py self-test OK")
