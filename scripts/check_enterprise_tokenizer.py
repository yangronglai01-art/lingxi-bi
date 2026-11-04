"""Offline tokenizer budget verification using the deployed local tokenizer directory."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.enterprise.acceptance import sha256


def measure(tokenizer, messages, context_tokens, output_tokens):
    if not 1 <= output_tokens < context_tokens: raise ValueError('Invalid model budget')
    tokens = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True)
    count = len(tokens)
    return dict(input_tokens=count, reserved_output_tokens=output_tokens, context_tokens=context_tokens,
                passed=count+output_tokens <= context_tokens)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('tokenizer-dir', 'messages', 'model-revision', 'out'): p.add_argument('--'+name, required=True)
    p.add_argument('--context-tokens', required=True, type=int); p.add_argument('--output-tokens', type=int, default=3000)
    a = p.parse_args()
    try:
        from transformers import AutoTokenizer
        root = Path(a.tokenizer_dir).resolve()
        messages = json.loads(Path(a.messages).read_text(encoding='utf-8'))
        if not isinstance(messages, list) or not messages or any(m.get('role') not in {'system','user','assistant'} or not isinstance(m.get('content'),str) for m in messages):
            raise ValueError('Chat messages required')
        tokenizer = AutoTokenizer.from_pretrained(str(root), local_files_only=True, trust_remote_code=False)
        result = measure(tokenizer, messages, a.context_tokens, a.output_tokens)
        files = {str(f.relative_to(root)):sha256(f) for f in root.rglob('*') if f.is_file() and f.suffix in {'.json','.txt','.model','.jinja'}}
        if not files: raise ValueError('Tokenizer artifacts missing')
        result.update(model_revision=a.model_revision, tokenizer_files=files,
            note='Operator must confirm gateway uses these exact tokenizer files, chat template and context limit. This is offline counting, not a live generation or runtime enforcement test.')
    except (ImportError, OSError, ValueError, TypeError, KeyError):
        raise SystemExit('Tokenizer verification unavailable or invalid; no remote code/download allowed; values omitted') from None
    with Path(a.out).open('x', encoding='utf-8') as f: json.dump(result, f, ensure_ascii=False, indent=2)
    print('Tokenizer budget: '+('passed' if result['passed'] else 'exceeded'))
    if not result['passed']: raise SystemExit(2)


if __name__ == '__main__': main()
