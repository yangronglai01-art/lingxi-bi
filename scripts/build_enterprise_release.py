"""Build an unsigned candidate release inventory. This never publishes or approves."""
import argparse
import hashlib
import json
from pathlib import Path


def inventory(root):
    root=Path(root)
    paths=list((root/'src').rglob('*.py'))+list((root/'web/dist').rglob('*'))+[
        root/'requirements-production.txt',root/'deploy/enterprise/runtime-grants.sql']
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths) if p.is_file()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('catalog','data-release','model-revision','embedding-revision','out'):p.add_argument('--'+n,required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[1];catalog=Path(a.catalog)
    result=dict(schema_version=1,status='candidate_not_approved',control_schema_version=3,data_release=a.data_release,
        model_revision=a.model_revision,embedding_revision=a.embedding_revision,catalog_sha256=hashlib.sha256(catalog.read_bytes()).hexdigest(),
        files=inventory(root),approval_inferred=False,
        note='Unsigned local inventory. Validate real data/model/index/metric bindings, frozen evaluation and production evidence with p0_enterprise before publishing. No secrets or environment values exported.')
    with Path(a.out).open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print('Candidate release inventory created; not approved or published.')


if __name__=='__main__':main()
