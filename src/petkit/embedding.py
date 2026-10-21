"""Offline CPU embeddings with validated, atomic corpus caches."""
import hashlib
import json
import os
from pathlib import Path
import threading
from functools import lru_cache
import numpy as np
from .settings import DATA

_lock=threading.RLock()


def signature(path):
    root=Path(path).resolve()
    if not root.is_dir(): raise ValueError('Embedding模型必须是已有本地目录')
    records=[]
    for p in sorted(root.rglob('*')):
        if p.is_file():
            stat=p.stat()
            records.append((str(p.relative_to(root)),stat.st_size,stat.st_mtime_ns))
    return hashlib.sha256(json.dumps([str(root),records,256],ensure_ascii=False).encode()).hexdigest()


@lru_cache(maxsize=1)
def model(path,fingerprint):
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(4)
    result=SentenceTransformer(path,device='cpu',local_files_only=True,trust_remote_code=False)
    result.max_seq_length=256
    return result


def encode(path,texts,progress=False):
    with _lock:
        result=model(path,signature(path)).encode(texts,batch_size=8,
            normalize_embeddings=True,show_progress_bar=progress,convert_to_numpy=True)
    return np.asarray(result,dtype=np.float32)


def valid(values,count):
    return (values.ndim==2 and values.shape[0]==count and values.shape[1]>0
            and bool(np.isfinite(values).all())
            and bool(np.allclose(np.linalg.norm(values,axis=1),1,atol=.002)))


def corpus(path,texts,progress=False):
    from filelock import FileLock
    digest=hashlib.sha256(json.dumps([signature(path),texts],ensure_ascii=False).encode()).hexdigest()
    folder=DATA/'vector_cache'; folder.mkdir(parents=True,exist_ok=True)
    target=folder/(digest+'.npz')
    with FileLock(str(target)+'.lock',timeout=1800):
        if target.exists():
            try:
                with np.load(target,allow_pickle=False) as cached: values=cached['vectors']
                if valid(values,len(texts)): return values
            except (ValueError,OSError,KeyError): pass
        values=encode(path,texts,progress)
        if not valid(values,len(texts)): raise ValueError('模型返回无效向量')
        import uuid
        temporary=folder/(digest+'.'+uuid.uuid4().hex+'.npz')
        try:
            np.savez_compressed(temporary,vectors=values)
            os.replace(temporary,target)
        finally:
            temporary.unlink(missing_ok=True)
        return values
