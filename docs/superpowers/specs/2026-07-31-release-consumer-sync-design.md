# Release and consumer pin synchronization

## Goal

Make toolkit releases reproducible and make downstream pin updates explicit,
validated operations. The toolkit remains organization-agnostic: it defines the
release and consumer contracts, while every consumer repository owns its remote
URLs, pin location, CI provider, review policy, and rollout schedule.

## Boundaries

The toolkit never discovers, enumerates, authenticates to, or pushes to consumer
repositories. A toolkit release and a consumer rollout are separate transactions:

1. the toolkit validates and publishes an immutable SemVer tag;
2. each consumer validates that tag and changes its own pin through its normal
   review workflow;
3. consumer onboarding installs exactly the reviewed pin.

This avoids hidden cross-perimeter writes and allows multiple organizations to
adopt releases independently.

## Toolkit release guard

Add a Python CLI that supports validation and local annotated-tag creation.
Before creating a tag it must verify:

- the requested version is strict `vMAJOR.MINOR.PATCH`;
- the version is greater than every existing SemVer tag and does not already
  exist;
- the worktree is clean, is on the configured release branch, and its HEAD is
  identical to the fetched remote release branch;
- `CHANGELOG.md` contains a dated section for the requested version;
- the configured full verification command exits successfully.

The CLI creates a local annotated tag only after all checks pass. It never pushes
commits or tags. The explicit push remains a separately visible maintainer action.

The GitHub release workflow repeats the immutable checks on the pushed tag,
fetches full history, runs the same complete test suite, and only then creates the
GitHub Release. This is defense in depth: the local guard prevents bad tags, while
CI prevents an accidentally or manually created tag from producing a release.

## Generic consumer pin helper

Add a second Python CLI that accepts all consumer-specific values as arguments:

- upstream Git URL;
- pin file path;
- target tag when bumping.

It provides two operations:

- `check`: validate that the pin file contains exactly one strict SemVer tag and
  that the tag exists in the configured upstream;
- `bump`: validate the requested upstream tag, then replace only the pin file
  value.

It does not commit, push, open a pull/merge request, or contain provider-specific
credentials. Consumer repositories wrap this helper with their own hook and CI
configuration.

## Documentation contract

The generic documentation must describe:

- the two-transaction release model;
- upstream and consumer responsibilities;
- the required order: CHANGELOG → verification → tag → upstream release →
  consumer pin review → onboarding;
- how a consumer integrates the pin helper into pre-push and CI;
- failure recovery, including deleting an unpushed local tag and never silently
  retagging a published version.

Examples use neutral placeholders such as `<consumer-repo>` and
`<toolkit-upstream-url>`. No organization names, internal domains, contours,
profiles, or credentials are allowed.

## Error handling

All validation failures are fail-closed and return a non-zero exit status with a
specific reason. Commands that change state perform every read-only validation
first. A pin bump writes only the requested pin file. Tag creation does not push.

Network failure while checking an upstream tag is a validation failure, not a
reason to assume that the tag exists.

## Testing

Unit and integration-style tests use temporary local Git repositories and bare
remotes. They cover:

- malformed, duplicate, non-incrementing, and missing-CHANGELOG versions;
- dirty worktrees, wrong branches, and HEAD differing from the remote branch;
- failing verification commands;
- successful annotated-tag creation with no push;
- valid, malformed, and missing consumer pins;
- bumping to an existing tag and refusing an unknown tag;
- preservation of every consumer file except the pin.

The complete existing test suite remains green. The workflow is checked with a
YAML parser, and the first real release and one real consumer rollout serve as the
end-to-end acceptance test.

## Rollout

The first patch release includes the SSH UTF-8 fix, the EDT-safe pre-push
dispatcher fix already merged into main, and this release-contract
implementation. After its GitHub Release succeeds, the current consumer updates
its pin through a separate merge request, runs its pin checks, merges, and
creates its own next patch tag according to its repository policy.
