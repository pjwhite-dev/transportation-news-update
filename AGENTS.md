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
- Fetch and use the linked article's actual page headline when metadata is
  available. A nearby description, quotation, or sentence fragment must never
  replace the article headline merely because it appears beside the URL.
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

Treat supplemental-email records as editor-vetted and presume they are relevant
and included. Do not apply the automated-feed relevance exclusions to them and
do not omit them for low importance. If there is any doubt whether a
supplemental item is the same concrete event as an automated item, keep it as a
separate story. When it is genuinely the same event, consolidation is allowed
only if the supplemental URL remains visible as additional coverage.

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
5. Military
6. eVTOL Integration Pilot Program and AAM
7. Autonomous Vehicles
8. Other Advanced Transportation
9. International
10. Federal Actions
11. Regulatory Deadline Tracker
12. What to Watch

Place the Regulatory Deadline Tracker at the bottom of the email immediately
before What to Watch. It is a persistent public-information section, not a list
of news stories. Each tracker item must show agency, action, official source,
comment deadline, days remaining when open, and status. After a comment period
closes, retain its closing date and use a specific pending status such as
“Pending final rule,” “Pending NHTSA action,” or “Pending petition decision.”

The tracker must include the FAA BVLOS/Part 108 rule, the Section 2209 fixed-site
UAS restriction rule, the supersonic-overland-flight rulemaking, key ADS-focused
FMVSS modernization actions, and important Part 555 petitions. Verify dates and
extensions against the latest official Federal Register notice before updating
the curated tracker. Show the tracker on Build Today’s Update before any AI
build, with an Include checkbox for every item. Carry those selections into
Review & Edit, where the owner can change them again. If an already-open browser
session contains a briefing created before tracker support was added, populate
the tracker deterministically without requiring another AI build.
Use concise noun-style action labels such as “Routine BVLOS drone operations /
Part 108”; do not begin tracker labels with boilerplate such as “Create rules
for,” “Enable,” “Modernize,” or “Decide.”

Do not add “new since yesterday” or “materially changed” badges. The email is
already limited to new developments. Do not add By-the-Numbers, Today-at-a-
Glance counts, or a separate Decision Points Ahead feature.

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

When credible records exist, the editorial pass must include at least one
substantive Autonomous Vehicles development and at least one substantive civil
supersonic, high-speed rail, hydrogen rail, maglev, automated rail, or other
genuinely advanced-transportation development. Enforce this with deterministic
fallbacks that continue to reject stock promotion, market reports, consumer
lists, and keyword collisions.

## Military coverage

Place stories centered on the Department of Defense, a military service,
defense acquisition, military operations or exercises, bases, warfighters,
battlefield use, or military-focused contractors in **Military**. This includes
military UAS, counter-UAS, autonomy, advanced aircraft, procurement, testing,
deployments, and operational use. It also includes conflict reporting such as
the war in Ukraine, Russian military operations, attacks on warships, naval or
battlefield strikes, weapons and munitions, and other combat activity. Enforce
this categorization deterministically so military stories are not buried in
generic UAS, UAS Security and C-UAS, or Federal Actions.

## International coverage

Place substantive non-U.S. commercial, regulatory, operational, and technical
advanced-transportation developments in **International**. This includes
foreign drone, eVTOL, autonomous-vehicle, advanced-rail, and civil-supersonic
news. International military and conflict stories remain in **Military**.
Do not classify a U.S.-centered story as International merely because it
mentions a foreign company, supplier, comparison, or market. When a credible
international candidate exists, keep at least one International story in the
briefing.

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
- Check or uncheck Trump Administration Win status for every story and move the
  story into or out of the Win section accordingly.
- Edit Administration Win explanations and optional EO/section citations.
- Edit the Executive Summary and What to Watch.

Directly below the Executive Summary, render **Headlines at a Glance** in a
smaller font. It must contain only linked headlines, grouped under the same
editorial sections used by the final arranged briefing. Build it dynamically
from that final briefing so supplemental items are included and story Include
checkboxes and headline edits are reflected automatically.

The Executive Summary must read as a polished, standalone news briefing for a
senior executive. It must never expose intake or editorial-process language such
as “required supplemental,” automated feed, records, links extracted or
represented, accounting, or how the briefing was assembled.
It must also never discuss whether any story is or is not an Administration Win
or why a story passed or failed the Win test. If a story description or summary
merely repeats its headline, omit the description entirely.
Generate the Executive Summary in a separate final AI pass only after all story
selection, clustering, section placement, summaries, What to Watch items, and
the regulatory tracker have been compiled. Give that final pass only the
compiled reader-facing briefing, never the raw intake records or internal Win
eligibility fields.

When a Win cites a recognized EO and section, append a concise plain-English
summary of that section, for example: “Section 3, advancing domestic
commercialization of UAS technologies at scale.”

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
categorization and coverage fallback, International categorization, the
sectioned headline index, Administration Win eligibility, and end-to-end
supplemental link accounting.

Never request, print, expose, or commit secrets. Do not push, open a pull
request, or merge without the repository owner’s explicit approval.
