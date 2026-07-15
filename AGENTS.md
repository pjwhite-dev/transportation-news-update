# Repository Maintenance Guide

## Product purpose

This repository provides a Streamlit application that creates the daily
**Advanced Transportation News Update**. Preserve the exact visible title. Do
not display the former subtitle “UAS, C-UAS, and Advanced Transportation.”

The product has two deliberately separate stages. Opening or refreshing the
Streamlit app must never call OpenAI.

## Two-stage workflow

### Stage 1 — GitHub Actions raw collection

- Run daily at 4:15 a.m. in `America/New_York`.
- Collect public-source advanced-transportation news from the preceding 24
  hours.
- Write `data/latest_raw_news.json` and a dated copy under
  `data/raw_archive/`.
- Do not call OpenAI or require an OpenAI secret.
- Commit generated JSON safely, retrying against the latest `main` so routine
  concurrent pushes do not lose the generated feed.

### Stage 2 — owner-triggered Streamlit editorial pass

- Load the automated raw feed.
- Accept an optional, separately pasted supplemental daily news email.
- Clean malformed URL wrappers such as `<https://example.com/article>`, remove
  trailing punctuation, remove duplicate URLs, and associate each link with a
  nearby pasted headline and context.
- Let the editor review and correct extracted headlines before AI processing.
- Run OpenAI only after the authenticated owner clicks a build button.
- Process the automated feed and all supplemental records together in one
  editorial pass.
- When no supplemental records have been extracted, show **Build from
  Automated Feed Only** whenever Owner controls are unlocked. When locked,
  clearly instruct the user to enter the owner password and unlock Owner
  controls before the button can appear.

## Supplemental link invariants

Every unique pasted link must be accounted for in the generated briefing. It
must either:

1. Appear as a distinct story with a specific headline, summary, source, date,
   and link; or
2. Appear as Additional coverage / Also covered by under a genuinely identical
   event.

Never silently discard a required supplemental link. Never merge distinct
events. Never use a publisher label such as MSN, Yahoo, AOL, Reuters, or another
publisher name as the headline. Never generate filler such as “Imported from
the supplemental daily news email.” Never place distinct stories into a generic
“Additional Headlines” bucket.

Display both accounting values using these meanings:

- **Supplemental links extracted**: unique supplemental URLs accepted for the
  editorial pass.
- **Supplemental links represented in the briefing**: those URLs found as a
  primary story link or genuine additional-coverage link in the final output.

Do not claim full accounting merely because the AI returned record IDs; compute
the represented count from URLs actually present in the arranged briefing.

## Required editorial sections

Preserve these explicit sections and ordering:

1. Trump Administration Wins
2. Top Developments
3. UAS and Drones
4. UAS Security and C-UAS
5. eVTOL Integration Pilot Program and AAM
6. Autonomous Vehicles
7. Other Advanced Transportation
8. Federal Actions
9. What to Watch

## Autonomous Vehicles coverage

Treat the following as Autonomous Vehicles when directly relevant:

- NHTSA and FMCSA actions
- FMVSS modernization, including FMVSS 102, 103, 104, and 108
- Part 555 exemptions
- ADS-equipped commercial vehicles and autonomous trucking
- Robotaxis, driverless operations, and automated-driving deployments
- Recalls, investigations, safety actions, and enforcement
- Federal, state, and local rules, permits, and legislation
- Testing, simulation, mapping, validation, and relevant V2X work

AV-specific federal actions normally belong in **Autonomous Vehicles**, not the
generic **Federal Actions** section. Enforce this in deterministic validation,
not only in the AI prompt.

## Trump Administration Win eligibility

Apply the rule conservatively. A story is an Administration Win only when all
four gates are true:

1. The underlying event—not merely publication of an article—occurred in the
   stated 24-hour coverage window.
2. The record supports a direct Trump Administration connection.
3. The event creates a concrete American benefit, such as domestic capability,
   U.S. manufacturing, American jobs, public safety, national security,
   deployment, regulatory progress, or removal of a barrier.
4. The event is not merely a foreign-headquartered company entering or
   expanding in the United States.

A new final rule, approval, contract award, enforcement result, implementation
milestone, operational launch, or regulatory action during the window may
qualify when the other gates are met. A new article about an old NPRM does not.
EO 14307, EO 14305, and EO 14304 are optional citations and may be used only
when the supplied record supports the connection; an EO number is not itself a
mandatory fifth eligibility gate.

Administration Win explanations are published verbatim and must stand alone for
the email reader. Name the Administration, agency, or federal program that
acted; state what it did; and explain the specific American result in ordinary,
active language. Never expose internal editorial tests with phrases such as
“during the window,” “the record shows,” “qualifies as a win,” “direct nexus,”
“concrete benefit,” or “clear federal procurement action.” For contracts and
purchases, say who awarded or ordered what, who will provide it, and which
American mission it supports.

## Relevance exclusions

Exclude unrelated keyword collisions, including HHS requests about psychedelic
therapies; medical, pharmaceutical, NIH, FDA, or general health-policy stories;
generic AI stories without a direct transportation connection; stock promotion;
generic market-size reports; and consumer-product lists. Required supplemental
links remain subject to accounting, while unrelated automated records should be
filtered out.

## Manual review and output

Preserve the **Review & Edit** tab. The authenticated editor must be able to:

- Remove individual stories with an Include checkbox.
- Edit headlines and summaries.
- Edit Administration Win explanations.
- Edit the Executive Summary and What to Watch.

Preserve the Outlook-specific renderer and controls:

- Outlook-safe table HTML with inline styles
- Generous vertical spacing
- Large linked headlines
- Subdued source/date metadata
- Executive Summary and Administration Win callouts
- Copy for Outlook
- Copy Executive Version
- Copy Subject Line

## Required maintenance validation

Before changing behavior, inspect the current repository and git history; do not
assume an earlier proposed or uploaded fix is present. At minimum inspect
`streamlit_app.py`, `news_engine.py`, `daily_update.py`, `requirements.txt`,
`.github/workflows/daily-news-update.yml`, `data/latest_raw_news.json`,
`data/raw_archive/`, and `.streamlit/config.toml`.

Before handing off a change, run:

- `git status` and review the complete diff.
- Python syntax compilation for the application modules.
- A direct `import news_engine`.
- An AST or runtime check that every name imported from `news_engine` by
  `streamlit_app.py` exists.
- Requirements parsing/version validation and `pip check` in an isolated
  environment.
- GitHub Actions YAML parsing and, when available, an Actions-aware validator.
- The focused test suite.
- A local headless Streamlit startup/health test when practical.

Regression coverage must include owner-authenticated and unauthenticated
feed-only button visibility, malformed and duplicate supplemental URL handling,
publisher-only headline rejection, unrelated HHS/psychedelic filtering, AV
categorization, Administration Win eligibility, and end-to-end supplemental
link accounting.

Never request, print, expose, or commit secrets. Do not push, open a pull
request, or merge without the repository owner’s explicit approval.
