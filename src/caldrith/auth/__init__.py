"""GitHub App authentication: per-installation client construction.

A *new* githubkit client is built per installation per job — installation tokens are
never shared across installations. The base URL is configurable so the same factory
serves github.com and (future) GHES.
"""
