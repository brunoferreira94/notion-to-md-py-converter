#!/usr/bin/env python3
import os, sys, hashlib, re, html, json

orig = r'D:\Revelo\notion-to-md-py-converter\output\PR Writer Project Frequently Asked Questions (FAQ)\PR Writer Project Frequently Asked Questions (FAQ) - 20260317-154330.md'
target = r'D:\Revelo\notion-to-md-py-converter\output\PR Writer Project Frequently Asked Questions (FAQ)\PR Writer Project Frequently Asked Questions (FAQ) - 20260317-154330 - normalized.md'

try:
    if not os.path.exists(orig):
        print(json.dumps({"status":"error","message":"original not found","original_path":orig}))
        sys.exit(1)
    if os.path.exists(target):
        print(json.dumps({"status":"error","message":"target already exists","normalized_path":target}))
        sys.exit(2)
    # read original bytes
    b = open(orig,'rb').read()
    orig_hash = hashlib.sha256(b).hexdigest()
    # decode strict utf-8
    try:
        text = b.decode('utf-8','strict')
    except UnicodeDecodeError as e:
        print(json.dumps({"status":"error","message":"utf-8 decode error","error":str(e)}))
        sys.exit(3)
    # normalize newlines
    text = text.replace('\r\n','\n').replace('\r','\n')
    # unescape html entities
    text = html.unescape(text)
    # prepare placeholder substrings and regexes
    substrings = [
        'carregando', 'carregando código', 'carregando código de', 'loading', 'loading code', 'carregando código de plain text',
        'carregando', 'loading', 'loading code', '(click to open)'
    ]
    # dedupe and lower
    substrings = list({s.lower():None for s in substrings}.keys())
    regex_patterns = [r"\bcarregando\b", r"\bloading\b", r"loading code", r"\(click to open\)"]
    regexes = [re.compile(p, re.I) for p in regex_patterns]
    # process lines
    lines = text.split('\n')
    out_lines = []
    for line in lines:
        # trim trailing whitespace
        line = line.rstrip()
        stripped_lower = line.strip().lower()
        if stripped_lower == '':
            out_lines.append('')
            continue
        remove = False
        for sub in substrings:
            if sub in stripped_lower:
                remove = True
                break
        if remove:
            continue
        for rx in regexes:
            if rx.search(line):
                remove = True
                break
        if remove:
            continue
        out_lines.append(line)
    # collapse 3+ blank lines to exactly 2
    final_lines = []
    blank_count = 0
    for ln in out_lines:
        if ln.strip() == '':
            blank_count += 1
        else:
            if blank_count >= 3:
                final_lines.extend(['',''])
            else:
                final_lines.extend([''] * blank_count)
            blank_count = 0
            final_lines.append(ln)
    if blank_count > 0:
        if blank_count >= 3:
            final_lines.extend(['',''])
        else:
            final_lines.extend([''] * blank_count)
    normalized_text = '\n'.join(final_lines)
    # ensure ends with newline
    if not normalized_text.endswith('\n'):
        normalized_text += '\n'
    # write target
    try:
        parent = os.path.dirname(target)
        if parent and not os.path.exists(parent):
            os.makedirs(parent)
        with open(target,'w',encoding='utf-8',newline='\n') as f:
            f.write(normalized_text)
    except Exception as e:
        # cleanup partial
        try:
            if os.path.exists(target):
                os.remove(target)
        except:
            pass
        print(json.dumps({"status":"error","message":"write failed","error":str(e)}))
        sys.exit(4)
    # verify
    nb = open(target,'rb').read()
    new_hash = hashlib.sha256(nb).hexdigest()
    bytes_written = len(nb)
    summary = {
        "status":"success",
        "original_path": orig,
        "original_sha256": orig_hash,
        "normalized_path": target,
        "normalized_sha256": new_hash,
        "bytes_written": bytes_written,
        "encoding":"utf-8"
    }
    print(json.dumps(summary, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"status":"error","message":"unexpected error","error":str(e)}))
    sys.exit(99)
