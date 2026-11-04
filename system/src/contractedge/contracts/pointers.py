"""One syntax contract for predeclared and observed JSON Pointer strings."""
import re

# The final negative lookahead is an exact end assertion, including newlines;
# unlike `$` it cannot accept a non-pointer consisting of a trailing newline.
JSON_POINTER_PATTERN = r'^(?:/(?:[^~/]|~[01])*)*(?![\s\S])'
JSON_POINTER_GUIDANCE = (
    'Use JSON Pointer strings: empty string for the root, otherwise begin with / '
    '(for example /settings/value or /items/0/value). Escape ~ as ~0 and / as ~1 '
    'inside a key. Dot paths, filenames and URI fragments are not JSON Pointers. '
    'A literal dot or # in a key is allowed after the leading /. '
    'Do not guess missing fields or change predeclared bindings during review.'
)
JSON_POINTER_SCHEMA = {'type': 'string', 'pattern': JSON_POINTER_PATTERN,
                       'description': JSON_POINTER_GUIDANCE}


def validate_json_pointer(pointer):
    if not isinstance(pointer, str) or re.fullmatch(JSON_POINTER_PATTERN, pointer) is None:
        raise ValueError(JSON_POINTER_GUIDANCE)
