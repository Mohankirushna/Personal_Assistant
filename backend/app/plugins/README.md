# Plugins

Drop-in directory for user/community tools. Not implemented until Phase 6+
(the plugin discovery mechanism ships alongside the core tool registry).

## How it will work

A plugin is a Python package placed in this directory that exposes one or more
classes implementing the `Tool` interface from
[`app/tools/base.py`](../tools/base.py) — the exact same interface used by
every first-party tool (Finder, Terminal, Browser, Git, VS Code, Vision,
Clipboard, System). There is no separate plugin API to learn.

At startup, `app/tools/registry.py` scans this directory and registers every
`Tool` subclass it finds, alongside the built-in tools. See
[docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md) section 6 for the full
design.

## Minimal shape (illustrative — not functional until Phase 6+)

```
plugins/
└── my_plugin/
    ├── __init__.py
    └── tool.py   # defines a class MyTool(Tool): ...
```
