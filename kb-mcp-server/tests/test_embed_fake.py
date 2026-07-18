from tests.fakes import FakeEmbedder


def test_fake_embedder_is_deterministic_and_right_dim():
    e = FakeEmbedder()
    a = e.embed(["hello", "world"])
    assert len(a) == 2 and len(a[0]) == e.dim
    assert e.embed(["hello"])[0] == a[0]  # deterministic
