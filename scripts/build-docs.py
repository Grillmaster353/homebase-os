#!/usr/bin/env python3
"""Render the tracked installation tutorial; never executes its commands."""
from pathlib import Path
import html
try:
    import markdown
except ImportError:
    raise SystemExit('Install the optional documentation dependencies in a virtual environment: python -m pip install -r requirements-docs.txt')
root=Path(__file__).resolve().parents[1]
source=(root/'docs/INSTALL.md').read_text()
engine=markdown.Markdown(extensions=['fenced_code','tables','toc'],extension_configs={'toc':{'toc_depth':'2'}})
body=engine.convert(source)
page='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#0c1018"><meta name="description" content="Complete HomeBase OS installation: Raspberry Pi OS Lite, SSH, Docker, HTTPS and device setup."><title>Install HomeBase OS · Complete Raspberry Pi Guide</title><link rel="stylesheet" href="site.css"><link rel="stylesheet" href="guide.css"><script defer src="guide.js"></script></head><body><a class="skip" href="#guide">Skip to guide</a><header><a class="brand" href="index.html">HomeBase<span> OS</span></a><nav aria-label="Guide navigation"><a href="index.html">Overview</a><a href="setup.html" aria-current="page">Full setup guide</a><a href="https://github.com/Grillmaster353/homebase-os">Source</a></nav></header><main class="guide-shell"><aside class="guide-toc"><div class="eyebrow">FROM BLANK CARD TO DASHBOARD</div><h2>Your installation map</h2>'''+engine.toc+'''<p class="guide-meta">v1.0.0 · Raspberry Pi OS Lite 64-bit<br>Docker Engine + Compose</p></aside><article id="guide" class="guide-content">'''+body+'''</article></main><footer><span>HomeBase OS · Local by design</span><a href="INSTALL.md">Download the Markdown guide</a></footer></body></html>'''
(root/'docs/setup.html').write_text(page)
print('Rendered docs/setup.html from docs/INSTALL.md')
