# Task: add IsPalindrome to the strutil package

## Goal

Add `IsPalindrome(s string) bool` to the strutil package. It reports whether
the string is a palindrome.

## Acceptance criteria

- Compare rune-by-rune, verbatim: no case folding, no Unicode normalization,
  every character (including spaces and punctuation) is significant.
- The empty string returns true.
- Minimum example set:
  - `IsPalindrome("") == true`
  - `IsPalindrome("a") == true`
  - `IsPalindrome("abc") == false`
  - `IsPalindrome("Abba") == false` (case-sensitive)
  - `IsPalindrome("a b a") == true` (spaces compared verbatim)
  - A CJK palindrome made of the runes U+4E0A U+6D77 U+6D77 U+4E0A returns
    true. Write this test string using Go Unicode escape sequences, not
    literal CJK characters.
- Unit tests cover the example set.

## Out of scope

- Do not modify the existing Reverse function.
- No new APIs, no CLI.
- No options for case folding, Unicode normalization, or ignoring punctuation.

## Notes

- The existing code base is already ASCII-only. Keep every artifact (spec,
  plan, code, tests) ASCII-only. Represent any
  non-ASCII character as a Unicode escape sequence or a U+XXXX code point
  reference, per AGENTS.md.
