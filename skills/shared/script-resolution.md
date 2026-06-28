# Script Resolution

The analyzer ships with this plugin at `scripts/agent_contracts.py`.

When a skill is loaded, resolve it from the skill directory:

```bash
SCRIPT="$CLAUDE_SKILL_DIR/../../scripts/agent_contracts.py"
python3 "$SCRIPT" map --repo .
```

If `CLAUDE_SKILL_DIR` is not available, use the absolute path to this plugin checkout. Do not assume the target repository contains `scripts/agent_contracts.py` unless you are working inside this plugin repository.
