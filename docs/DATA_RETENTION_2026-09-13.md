# Repository data retention and history maintenance

The 2026-09-13 maintenance removes retired real-project inputs and duplicate
data from ordinary branch history. It is an exact-path rewrite, not a global
blob or file-extension purge: some input copies share bytes with retained
baseline evidence.

## Retained material

- Every historical file under a `baseline` or `baselines` directory.
- The final nine-drawing reports and registered publication results, including
  their supporting data and the nine bundled map fixtures.
- Output QGZ projects and their protected baseline/report dependencies.
- Program source, synthetic regression fixtures and technical documentation.

The explicit QGZ exception is the retired `demo/DS_02_GIS.qgz`, which is removed
with the real TIFF/TFW inputs it references under `demo/source`.

Retained QGZ files may embed actual GeoPackages. Retaining these files and
baseline evidence intentionally retains real project data; this operation does
not anonymize the repository. The software license does not grant rights to
those datasets; see [NOTICE.md](../NOTICE.md).

## Preventing reintroduction

Keep original DWG/raster inputs and temporary conversion outputs outside the
repository. Removed input locations are ignored. Regression tests should use
synthetic fixtures or read the expressly retained baseline materials, rather
than copying real inputs into another test-data directory.

After a branch-history rewrite, start from a fresh checkout or rebase only
reviewed source changes onto the new branch. Merging an old checkout can bring
the removed history back. Preserve local work before replacing a checkout.

## Scope of remote cleanup

The GitHub releases present at this maintenance are immutable. Their tags and
GitHub-managed pull-request refs retain old history and cannot be cleaned by a
normal branch push. Rewriting ordinary branches therefore does not establish
complete removal from GitHub, existing clones, release assets or caches.
Release deletion requires a separate repository-owner decision; server-side
cached views and pull-request refs may require GitHub Support. Local backups
and existing worktrees are retained for recovery and are not sanitized copies.
