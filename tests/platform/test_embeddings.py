import pytest

from jarvis.platform.embeddings import FastEmbedTextEmbeddings


def test_fastembed_generates_normalized_local_query_and_document_vectors(tmp_path) -> None:
    embeddings = FastEmbedTextEmbeddings(cache_directory=tmp_path / "models")

    documents = embeddings.embed_documents(
        ["Yuvraj uses an iPhone 17 Pro.", "Yuvraj plays competitive tennis."]
    )
    query = embeddings.embed_query("Which mobile phone does Yuvraj use?")

    assert embeddings.model_name == "BAAI/bge-small-en-v1.5"
    assert len(query) == embeddings.dimensions == 384
    assert len(documents) == 2
    assert _dot(query, documents[0]) > _dot(query, documents[1])


def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def test_fastembed_rejects_unbounded_configuration_and_inputs(tmp_path) -> None:
    with pytest.raises(ValueError, match="threads"):
        FastEmbedTextEmbeddings(cache_directory=tmp_path, threads=0)
    embeddings = FastEmbedTextEmbeddings(cache_directory=tmp_path)

    assert embeddings.embed_documents([]) == []
    with pytest.raises(ValueError, match="query"):
        embeddings.embed_query("")
