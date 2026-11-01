# Contributing to MemKraft

Thank you for your interest in contributing!

## How to Contribute

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## Development Setup

```bash
git clone https://github.com/seojoonkim/memkraft.git
cd memkraft
pipx install -e .
```

## Guidelines

- Keep the core framework-agnostic (no OpenClaw, no Claude-specific dependencies)
- All data stays as plain Markdown — no proprietary formats
- Source attribution is mandatory for all factual claims
- Write tests for new features
- Follow existing code style

## Reporting Issues

Use GitHub Issues for bugs, feature requests, or questions.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

## Merging pull requests

`main` keeps a linear history, and CI checks that every feature commit listed in
`release_manifest.json` exists on `main`. Squash and rebase merges rewrite the PR
commits, so the listed commits disappear and the first `main` run fails until a
follow-up "point manifest at merged commit" PR lands.

To avoid that, maintainers merge by **fast-forwarding `main` to the PR head**:

1. Rebase the PR branch onto the latest `main` and wait for all required checks
   on that exact head to pass.
2. Push the same commit to `main`:

   ```bash
   scripts/ff_merge_pr.sh <pr-number>
   ```

   GitHub marks the PR as merged because `main` now points at its head commit.
   No new commit is created, so the manifest stays valid and no follow-up PR is needed.

Do not use the squash or rebase buttons for PRs that touch `release_manifest.json`.
