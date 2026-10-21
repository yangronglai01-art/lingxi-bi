import numpy as np
from src.petkit import embedding

def test_vector_validation():
    assert embedding.valid(np.eye(2,dtype=np.float32),2)
    assert not embedding.valid(np.zeros((2,3)),2)
    assert not embedding.valid(np.array([[np.nan]]),1)
    assert not embedding.valid(np.eye(2),3)

def test_cache_reuse_and_invalidation(tmp_path,monkeypatch):
    root=tmp_path/'model'; root.mkdir()
    weight=root/'weights.bin'; weight.write_bytes(b'one')
    monkeypatch.setattr(embedding,'DATA',tmp_path/'data')
    calls=[]
    def fake(path,texts,progress=False):
        calls.append(list(texts))
        return np.tile(np.array([[1.,0.]],dtype=np.float32),(len(texts),1))
    monkeypatch.setattr(embedding,'encode',fake)
    embedding.corpus(str(root),['a','b'])
    embedding.corpus(str(root),['a','b'])
    assert len(calls)==1
    embedding.corpus(str(root),['a','c'])
    assert len(calls)==2
    weight.write_bytes(b'changed')
    embedding.corpus(str(root),['a','b'])
    assert len(calls)==3
    for p in (tmp_path/'data/vector_cache').glob('*.npz'): p.write_bytes(b'broken')
    embedding.corpus(str(root),['a','b'])
    assert len(calls)==4
