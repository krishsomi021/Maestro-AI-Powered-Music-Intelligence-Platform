import logging

logger = logging.getLogger(__name__)

_embedding_model = None


def _load_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("sentence-transformers model loaded")
    return _embedding_model


def embed_tracks(tracks: list[dict]) -> list[list[float]]:
    """
    Generate 384-dim embeddings from track dicts with keys
    artist_name, track_name, genres (list[str] | None).
    """
    texts = [
        "{} | {} | {}".format(
            t["artist_name"],
            t["track_name"],
            ", ".join(t.get("genres") or []),
        )
        for t in tracks
    ]
    model = _load_embedding_model()
    return model.encode(texts).tolist()
