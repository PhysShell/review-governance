"""Tests for the A6h probe. Discoverable by pytest's default naming."""
import pytest

from a6h_probe import expansion_words, word_size


@pytest.mark.parametrize("length,words", [
    (0, 0), (1, 1), (31, 1), (32, 1), (33, 2), (64, 2), (65, 3),
])
def test_a_partial_word_costs_a_whole_one(length, words):
    assert word_size(length) == words


def test_large_lengths_are_exact():
    """The float form rounds silently past 2**53 and stops being a count."""
    assert word_size(2 ** 53 + 1) == (2 ** 53 + 1 + 31) // 32
    assert word_size(2 ** 64) == 2 ** 64 // 32


def test_a_negative_length_is_a_caller_error_not_a_free_buffer():
    with pytest.raises(ValueError):
        word_size(-1)


@pytest.mark.parametrize("current,requested,added", [
    (0, 0, 0), (0, 32, 1), (32, 32, 0), (32, 64, 1), (64, 32, 0), (0, 33, 2),
])
def test_expansion_is_monotone(current, requested, added):
    assert expansion_words(current, requested) == added
