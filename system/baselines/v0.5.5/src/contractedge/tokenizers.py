"""Pinned local tokenizer adapters for reproducible pilot configurations."""

from __future__ import annotations

from functools import lru_cache


def utf8_byte_token_bound(text: str) -> int:
    """Conservative byte-fallback token bound; never presented as exact model usage."""
    if not isinstance(text, str):
        raise TypeError('tokenizer input must be text')
    return len(text.encode('utf-8'))


QWEN35_TOKENIZER_ID = "unsloth/Qwen3.5-27B"
QWEN35_TOKENIZER_REVISION = "68443620da0c3847c1ba5042b5dcf1e78c42bfba"


@lru_cache(maxsize=1)
def _qwen35_tokenizer():
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("the pilot tokenizer requires the optional transformers dependency") from exc
    return AutoTokenizer.from_pretrained(QWEN35_TOKENIZER_ID,
        revision=QWEN35_TOKENIZER_REVISION, local_files_only=True)


def qwen35_token_count(text: str) -> int:
    """Count tokens with the locally cached, pinned Qwen3.5 tokenizer revision."""
    if not isinstance(text, str):
        raise TypeError("tokenizer input must be text")
    return len(_qwen35_tokenizer().encode(text, add_special_tokens=False))
