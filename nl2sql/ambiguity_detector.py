import os
import re
import logging

log = logging.getLogger(__name__)


class AmbiguityDetector:
    """Improved AmbiSQL-style ambiguity detection.

    - Skips detection entirely in DEV_MODE.
    - Ignores qualified references like 'artist.name'.
    """

    AMBIGUOUS_TERMS = [
        "recent",
        "top",
        "name",
        "rank",
        "latest",
        "id",
        "title",
        "date",
        "type",
    ]

    def detect(self, query: str, schema_preview: str) -> list[str]:
        # Normalize query
        q_lower = query.lower()

        # Skip ambiguity checks entirely in dev mode
        if os.getenv("DEV_MODE") == "1":
            log.warning("Skipping ambiguity detection (DEV_MODE=1).")
            return []

        hits = []
        for term in self.AMBIGUOUS_TERMS:
            # Match only standalone words, not qualified like 'artist.name'
            pattern = rf"(?<!\.)\b{term}\b"
            if re.search(pattern, q_lower):
                hits.append(f"The term '{term}' is ambiguous in this query.")

        return hits
