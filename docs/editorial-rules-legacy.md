# Legacy editorial rules

Carried over from the pre-rebuild AGENTS.md (obsolete OpenAI/GitHub Actions design).
Only the editorial rules are kept here: section placement, relevance exclusions,
supplemental-link accounting. Workflow/process sections were dropped as obsolete.
These rules inform the Phase 2 editorial rebuild (docs/rebuild-plan.md, Section 7.1);
where they conflict with the plan, the plan wins. Note the supplemental-email
intake is subject to the Phase 1 item 7 decision.

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
the curated tracker. Populate it deterministically for every complete briefing
without requiring another AI build.
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

Place counter-UAS technology and capability stories in **UAS Security and
C-UAS**, including detection, tracking, identification, jamming, mitigation,
interceptors, directed energy, acquisition, contracts, tests, deployments,
training, and capability-building. This applies even when a Defense agency,
military service, or defense contractor is involved, unless the story centers
on active combat.

Place other stories centered on the Department of Defense, a military service,
defense acquisition, military operations or exercises, bases, warfighters,
battlefield use, or military-focused contractors in **Military**. This includes
non-C-UAS military UAS, autonomy, advanced aircraft, procurement, testing,
deployments, and operational use. It also includes conflict reporting such as
the war in Ukraine, Russian military operations, attacks on warships, naval or
battlefield strikes, weapons and munitions, and other combat activity. Enforce
this categorization deterministically so military stories are not buried in
generic UAS or Federal Actions. A C-UAS story about battlefield use, war,
Ukraine, Russia, strikes, or other active combat remains in **Military**.

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

A new action by a current executive-branch department, agency, military
service, or Federal program is itself a direct Trump Administration action; the
article does not also have to name President Trump. This does not extend the
nexus to private, State, local, congressional, judicial, or foreign actions
that merely align with Administration policy.

Treat an inaugural operational flight under DOT and FAA's eIPP as a direct
implementation win under EO 14307, Section 6. Treat a current U.S. FIFA World
Cup counter-UAS deployment, enforcement action, federally backed capability,
or retention of World Cup counter-drone equipment for future public events as
a direct implementation win under EO 14305, Section 9. Enforce these two
narrow recognizers deterministically so they are not lost when the AI omits a
Win flag. Do not extend them to generic eIPP explainers, unrelated FIFA
coverage, security criticism, or private promotion.

Also protect concrete current Federal implementation results such as FAA
acceptance of aviation standards, a NHTSA automated-driving rule or firm
rulemaking milestone, and a DARPA or U.S. military operational technology
flight or test when the other gates are supported. Do not reduce these to
generic agency news merely because the headline omits President Trump's name.

Administration Win explanations are published verbatim and must stand alone for
the email reader. Name the Administration, agency, or federal program that
acted; state what it did; and explain the specific American result in ordinary,
active language. Never expose internal editorial tests with phrases such as
“during the window,” “the record shows,” “qualifies as a win,” “direct nexus,”
“concrete benefit,” or “clear federal procurement action.” For contracts and
purchases, say who awarded or ordered what, who will provide it, and which
American mission it supports.

For a supported Win, use neutral, factual language. Attribute the action to the
responsible Administration, agency, or federal program and explain the
documented or clearly stated effect on U.S. safety, jobs, manufacturing,
security, deployment, regulatory progress, or airspace operations. Keep every
claim supportable by the linked source.

## Relevance exclusions

Exclude unrelated keyword collisions, including HHS requests about psychedelic
therapies; medical, pharmaceutical, NIH, FDA, or general health-policy stories;
generic AI stories without a direct transportation connection; stock promotion;
generic market-size reports; and consumer-product lists. Required supplemental
links remain subject to accounting, while unrelated automated records should be
filtered out.

## Generated output

The complete-briefing workflow publishes directly without a Streamlit editing
step. Deterministic validators must enforce required sections, supplemental-link
accounting, section placement, Administration Win gates, innovative UAS labels,
and the presence of the Executive Summary and regulatory tracker before files
are saved or the live site is replaced.
