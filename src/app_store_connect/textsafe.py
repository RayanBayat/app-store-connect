"""Make text safe to show a human in a form, and to hand to the model as data.

Why: text from the API or from the model can contain characters that change how a form looks
(newlines that fake extra lines, right-to-left overrides that reorder what is read) or that
look like our own safety markers. `escape_display` turns every such character into visible
text, so what the human reads is exactly what would be sent.
"""

from __future__ import annotations

import unicodedata
from typing import Final

NEWLINE_SYMBOL: Final = "\u23ce"  # the return symbol, shown instead of a line break
# Control, format (includes bidi marks and isolates), line/paragraph separators, plus
# surrogates and private-use characters, which have no fixed meaning.
_HIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co"})


def _escape_char(char: str) -> str:
    """Return `char` unchanged if it is visible, else a visible escape such as \\u202e."""
    code = ord(char)
    if unicodedata.category(char) not in _HIDDEN_CATEGORIES:
        return char
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def escape_display(value: object) -> str:
    """Return `value` as text with line breaks shown as a symbol and hidden characters escaped."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(NEWLINE_SYMBOL if c == "\n" else _escape_char(c) for c in text)
