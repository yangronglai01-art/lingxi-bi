"""BM25 + explicit lexical vector fallback, optional real local embedding model."""
import hashlib
import json
import math
import os
import re
from collections import Counter,deque
from functools import lru_cache
from .settings import artifacts


def tokens(text):
    text=text.lower()
    words=re.findall(r"[a-z0-9_]+",text)
    for part in re.findall(r"[\u4e00-\u9fff]+",text):
        words.extend(part)
        words.extend(part[i:i+2] for i in range(len(part)-1))
    return words


@lru_cache(maxsize=4)
def load_catalog(generation):
    folder,manifest=artifacts()
    # Explicit version pin protects against mid-request pointer changes.
    from .settings import DATA
    folder=DATA/generation
    if folder.parent.resolve()!=DATA.resolve(): raise ValueError("目录版本无效")
    return json.loads((folder/"catalog.json").read_text(encoding="utf-8"))


def current_catalog():
    _,manifest=artifacts()
    return load_catalog(manifest["generation"])


def lexical_vector(text):
    vector=Counter()
    for token in tokens(text):
        digest=hashlib.blake2b(token.encode(),digest_size=8).digest()
        vector[int.from_bytes(digest[:4],"little")%512]+=1 if digest[4]%2 else -1
    norm=math.sqrt(sum(v*v for v in vector.values())) or 1
    return {k:v/norm for k,v in vector.items()}


class Index:
    def __init__(self,catalog,model_path=None):
        self.catalog=catalog; self.documents=[]
        for name,t in catalog["tables"].items():
            self.documents.append((name,"table",f"{name} {t['description']} {t['grain']} {' '.join(t['aliases'])}"))
            for col,c in t["columns"].items():
                self.documents.append((name,f"column:{col}",f"{name} {t['description']} {col} {c['description']} {' '.join(map(str,c.get('enum',[])))}"))
        # Metric documents retain approved dependency links. This is an explicit
        # indexed route, separately reported from later mandatory completion.
        for metric in catalog["metrics"]:
            metric_text=f"{metric['name']} {' '.join(metric['aliases'])} {metric['definition']} {metric['grain']}"
            for dependency in metric["dependencies"]:
                self.documents.append((dependency,"metric_dependency:"+metric["id"],metric_text))
        self.counts=[Counter(tokens(d[2])) for d in self.documents]
        self.df=Counter()
        for count in self.counts: self.df.update(count.keys())
        self.avg=sum(sum(x.values()) for x in self.counts)/max(1,len(self.counts))
        self.vectors=[lexical_vector(d[2]) for d in self.documents]
        self.model_path=os.getenv("PETKIT_EMBEDDING_MODEL","") if model_path is None else model_path
        self.dense=None
        if self.model_path:
            from .embedding import corpus
            texts=[]; mapping={}; self.dense_map={}
            for i,(name,kind,text) in enumerate(self.documents):
                if kind.startswith("column:"): continue
                if kind=="table":
                    t=catalog["tables"][name]
                    text+=' '+ ' '.join(f"{c} {v['description']}" for c,v in t['columns'].items())
                key=(name if kind=="table" else kind)
                if key not in mapping: mapping[key]=len(texts); texts.append(text)
                self.dense_map[i]=mapping[key]
            self.dense=corpus(self.model_path,texts,progress=True)

    def search(self,question,allowed,limit=8,method="hybrid"):
        q=tokens(question); qv=lexical_vector(question); scored=[]
        dense_q=None
        if self.dense is not None:
            from .embedding import encode
            dense_q=encode(self.model_path,[question])[0]
        lexical=[]; vector=[]
        for i,(name,kind,text) in enumerate(self.documents):
            if name not in allowed: continue
            count=self.counts[i]; length=sum(count.values()); score=0
            for term in set(q):
                tf=count[term]
                if tf:
                    idf=math.log(1+(len(self.counts)-self.df[term]+.5)/(self.df[term]+.5))
                    score+=idf*tf*2.2/(tf+1.2*(.25+.75*length/self.avg))
            lexical.append((score,i))
            if dense_q is None:
                vector.append((sum(v*self.vectors[i].get(k,0) for k,v in qv.items()),i))
            elif i in self.dense_map:
                vector.append((float(self.dense[self.dense_map[i]]@dense_q),i))
        fused=Counter()
        for route in ([lexical] if method=="bm25" else [vector] if method=="vector" else [lexical,vector]):
            for rank,(score,i) in enumerate(sorted(route,reverse=True)[:80],1):
                if score>0: fused[i]+=1/(60+rank)
        by_table={}
        weighted={i:score*(1.2 if self.catalog["tables"][self.documents[i][0]]["core"] else .6)
                  for i,score in fused.items()}
        for i,score in sorted(weighted.items(),key=lambda x:-x[1]):
            name,kind,text=self.documents[i]
            if name not in by_table:
                by_table[name]=dict(table=name,score=round(score,6),reason=kind,text=text,table_role_weight=1.2 if self.catalog["tables"][name]["core"] else .6)
        return list(by_table.values())[:limit]


@lru_cache(maxsize=2)
def index_for(generation,model_path="",fingerprint=""): return Index(load_catalog(generation),model_path)


def retrieve(question,principal,metric=None):
    cat=current_catalog(); allowed={n for n,t in cat["tables"].items() if principal.allowed(t)}
    model_path=os.getenv("PETKIT_EMBEDDING_MODEL","")
    fingerprint=""
    if model_path:
        from .embedding import signature
        fingerprint=signature(model_path)
    evidence=index_for(cat["generation"],model_path,fingerprint).search(question,allowed)
    selected=list(dict.fromkeys([e["table"] for e in evidence]))
    required=metric["dependencies"] if metric else []
    if set(required)-allowed: raise PermissionError("所需指标依赖超出当前主体授权范围")
    reasons={n:"检索命中" for n in selected}
    for name in required:
        if name not in selected: selected.append(name); reasons[name]="指标依赖补全"
    # Approved bounded shortest paths; do not expand every foreign-key neighbor.
    graph={}
    for r in cat["relationships"]:
        if r["left"] in allowed and r["right"] in allowed:
            graph.setdefault(r["left"],[]).append(r["right"])
            graph.setdefault(r["right"],[]).append(r["left"])
    anchors=list(required) or selected[:4]
    if anchors:
        for target in anchors[1:]:
            queue=deque([(anchors[0],[anchors[0]])]); visited=set()
            while queue:
                node,path=queue.popleft()
                if node==target:
                    for name in path:
                        if name not in selected: selected.append(name); reasons[name]="批准关系路径补全"
                    break
                if node in visited or len(path)>=5: continue
                visited.add(node)
                queue.extend((n,path+[n]) for n in graph.get(node,[]) if n not in visited)
    # Keep whole schemas for at most 14 tables; dependencies take precedence.
    ordered=list(dict.fromkeys(required+[n for n in selected if reasons[n]=="批准关系路径补全"]+selected))
    if len(required)>14: raise ValueError("指标依赖超出上下文预算")
    ordered=ordered[:14]
    tables={n:cat["tables"][n] for n in ordered}
    relationships=[r for r in cat["relationships"] if r["left"] in tables and r["right"] in tables]
    return dict(generation=cat["generation"],version=cat["version"],tables=tables,
        relationships=relationships,metric=metric,evidence=evidence,
        selection=[dict(table=n,reason=reasons[n]) for n in ordered],
        retrieval_method="BM25 + sentence-transformers + RRF" if os.getenv("PETKIT_EMBEDDING_MODEL") else "BM25 + 字符哈希词法向量 + RRF（非语义Embedding）",
        data_as_of=cat["as_of"],synthetic=True)
