# Analyzer Contract

The analyzer must inspect files without executing them.

It collects:
- File inventory and language hints.
- Generated, vendor, dependency, build, and cache exclusions.
- Package and module manifests.
- Python imports.
- JavaScript and TypeScript imports.
- Routes where practical.
- Public exports, functions, and classes where practical.
- Test files.
- README and documentation files.
- Config and environment examples.
- Commands from package manifests, pyproject files, Makefiles, and README snippets.

When parsing is incomplete, report confidence and boundary notes instead of guessing silently.
