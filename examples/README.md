# MemKraft examples

Copy-paste patterns for integrating MemKraft into real agent stacks.

| File | What it shows |
|------|---------------|
| [`claude_code/AGENTS.md`](claude_code/AGENTS.md) | Drop-in AGENTS.md block for Claude Code / ОpenClaw |
| [`claude_code/example_session.md`](claude_code/example_session.md) | An actual Claude Code conversation that exercises the API |
| [`openai_function_calling.py`](openai_function_calling.py) | Expose MemKraft as OpenAI function-calling tools |
| [`minimal_rag.py`](minimal_rag.py) | 10-line retrieval → prompt injection RAG loop |

> All examples assume `memkraft` is installed:
>
> ```bash
> pipx install memkraft
> # or inside a project:
> pip install memkraft
> ```

## Generate your own integration block

Any of the above blocks can be regenerated with the CLI:

```bash
memkraft agents-hint claude-code
memkraft agents-hint openai
memkraft agents-hint langchain
memkraft agents-hint cursor
memkraft agents-hint mcp
memkraft agents-hint openclaw
```
