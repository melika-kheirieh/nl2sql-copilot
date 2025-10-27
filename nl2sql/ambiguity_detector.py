import re


class AmbiguityDetector:
    """Lightweight AmbiSQL-style ambiguity detection."""

    AMBIGUOUS_TERMS = ["recent", "top", "name", "rank", "latest"]

    def detect(self, query: str, schema_preview: str) -> list[str]:
        hits = []
        q_lower = query.lower()
        for term in self.AMBIGUOUS_TERMS:
            if re.search(rf"\b{term}\b", q_lower):
                hits.append(f"The term '{term}' is ambiguous in this query.'")

        return hits
