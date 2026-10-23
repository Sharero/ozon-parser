"""Encode Unicode URLs before passing them to HTTP clients and headers."""
from urllib.parse import quote


def ascii_url(url: str) -> str:
    # Keep reserved delimiters and existing %XX escapes. Encode Unicode as UTF-8.
    return quote(url, safe=":/?#[]@!$&'()*+,;=%")
