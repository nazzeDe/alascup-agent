from __future__ import annotations

import json

from src.tools._fingerprint import format_fingerprint


def save_experience(
    symptom: str,
    fingerprint: dict,
    root_cause: str,
    solution: str,
    store,
    embedder,
    similarity_threshold: float = 0.95,
) -> dict:
    """Persist a diagnostic experience with fingerprint-driven dedup.

    Symptom + fingerprint are embedded together. If an existing entry has
    cosine similarity >= threshold, the write is deduplicated (no new record).
    Different fingerprints for the same symptom are treated as distinct root
    causes and create separate records.
    """
    search_text = f"{symptom} {format_fingerprint(fingerprint)}"
    embedding = embedder.embed(search_text)

    existing = store.search(embedding, top_k=1)
    if existing and existing[0].get("score", 0) >= similarity_threshold:
        return {"status": "deduplicated", "record_id": existing[0]["id"]}

    metadata = {
        "symptom": symptom,
        "fingerprint": json.dumps(fingerprint, sort_keys=True),
        "root_cause": root_cause,
        "valid": True,
    }
    doc_id = store.add(embedding, metadata, solution)
    return {"status": "saved", "record_id": doc_id}
