# Owner editor

This small server-backed site edits the current published edition without
exposing the owner password or GitHub publishing credential to the browser.

Deploy this directory as its own Vercel project. Configure the five variables
listed in `.env.example` as server-side environment variables. Use a long,
unique owner password, a separate random session secret, and a fine-grained
GitHub token limited to Contents read/write for this repository.

The editor saves `data/latest_briefing.json` and the matching dated archive in
one Git commit. The `publish-owner-edits.yml` workflow then rebuilds GitHub
Pages so changes appear on the public site.
