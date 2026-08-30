"""Word-size accounting, as used by EVM memory and copy-cost formulas.

Apparatus for the A6h governed-review experiment, kept in `governor/pilot`
so it is obvious that it is apparatus. It implements one total function
with a stated contract and is exercised by the test file beside it.
"""


def word_size(byte_length: int) -> int:
    """Number of 32-byte words needed to hold `byte_length` bytes.

    The EVM charges memory and copy costs per word rather than per byte, so
    a partial word costs a whole one: 33 bytes occupy two words. This is
    `ceil(byte_length / 32)`, written with integer arithmetic because the
    float form is wrong for large values — at 2**53 and beyond, division
    silently rounds and the result stops being the number of words.

    Zero bytes occupy zero words. A negative length is not a short buffer;
    it is a caller error, and returning 0 for it would let a bad offset
    reach a cost calculation as if it were free.
    """
    if byte_length < 0:
        raise ValueError(f"byte_length must not be negative, got {byte_length}")
    return (byte_length + 31) // 32


def expansion_words(current_bytes: int, requested_bytes: int) -> int:
    """Words a memory expansion would add, or zero if it fits already.

    Expansion is monotone: memory never shrinks, so a request inside the
    current allocation costs nothing rather than a negative amount.
    """
    return max(0, word_size(requested_bytes) - word_size(current_bytes))
