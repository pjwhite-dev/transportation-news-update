# Transportation News Update

A GitHub Pages site that publishes a daily, AI-assisted briefing covering the
preceding 24 hours of:

- UAS and drones
- UAS security and C-UAS
- Military applications, operations, procurement, and defense technology
- eVTOL Integration Pilot Program and advanced air mobility
- Autonomous vehicles
- Other advanced transportation, including civil supersonics and rail innovation
- International advanced-transportation developments
- Federal actions
- Verified Trump Administration wins tied to relevant policy actions and executive orders

The finished edition includes an Executive Summary, a compact sectioned
Headlines at a Glance index, Trump Administration Wins, Top Developments, topic
sections, a Regulatory Deadline Tracker, What to Watch, innovative UAS-use
highlights, and a **Copy for email** button.

## Repository files

```text
streamlit_app.py
news_engine.py
daily_update.py
automated_briefing.py
requirements.txt
README.md
.streamlit/config.toml
.github/workflows/daily-news-update.yml
.github/workflows/build-full-briefing.yml
data/latest_raw_news.json
data/raw_archive/
data/latest_briefing.json
data/archive/
coverage_history.py
publication.py
public_site.py
```

## Required secrets

### GitHub Actions secret

In **Repository Settings → Secrets and variables → Actions**, add:

```text
OPENAI_API_KEY
```

An optional `OPENAI_MODEL` repository variable can override the default model.
The OpenAI key is used only by the complete-briefing workflow. Raw collection
does not use OpenAI. Never place a key directly in code, JSON, Markdown, TXT, or
YAML.

## Daily schedule

The GitHub Actions workflow runs every day at **4:15 a.m.
America/New_York**. It:

1. Collects records published during the preceding 24 hours.
2. Writes `data/latest_raw_news.json` and a dated raw archive.
3. Commits the public raw feed safely to GitHub.

The **Build and Publish Complete Briefing** workflow is then started manually or
by the trusted supplemental-email bridge. OpenAI selects, clusters, categorizes,
and summarizes the stories and drafts What to Watch. Only after that briefing
and the regulatory tracker are compiled does a separate final AI pass write the
Executive Summary from the finished reader-facing material. Deterministic
coverage checks keep credible AV, advanced rail/supersonic, and international
developments from disappearing when the raw feed contains suitable records.

Before that editorial pass, the app compares automated candidates with the
prior 45 days of owner-published editions. A likely repeat is omitted unless the
new record contains a concrete later milestone such as a final rule, approval,
contract award, operational launch, completed test, deadline change, permit, or
safety action. Supplemental links remain editor-vetted and are never silently
discarded by this check.

## Public website and archive

GitHub Pages publishes `news.peterjwhite.org`. The latest page advances only
when a complete AI-assisted edition has been generated. Raw collections remain
available to the pipeline but never replace the live briefing. The **Archive**
page catalogs every dated edition, and each edition has a **Copy for email**
button.

The complete-briefing workflow commits both `data/latest_briefing.json` and the
matching dated file under `data/archive/`, then deploys the website in the same
run.

GitHub Pages must use **GitHub Actions** as its build source. The DNS record for
the `news` host should be a CNAME pointing to `pjwhite-dev.github.io`.

GitHub scheduled workflows can occasionally run a few minutes late. The generated briefing
always labels its exact 24-hour coverage window.

## First test

After deploying the files and configuring the Actions secret:

1. Open the repository's **Actions** tab.
2. Select **Daily Transportation Raw News Collection**.
3. Click **Run workflow**.
4. Wait for the run to complete.
5. Confirm that `data/latest_raw_news.json` and a dated raw archive were updated.
6. Select **Build and Publish Complete Briefing** in the Actions tab.
7. Click **Run workflow**.
8. Confirm that the completed edition appears on `news.peterjwhite.org` and use
   **Copy for email** when needed.

If the commit step reports a permissions error, open:

**Repository Settings → Actions → General → Workflow permissions**

Select **Read and write permissions**, save, and run the workflow again.

## Important notes

- The site uses public-source headlines, snippets, links, and Federal Register records.
- Google News RSS is a discovery source and can occasionally return noisy results; the AI
  relevance filter is designed to remove obvious false positives.
- Federal Register API results expose a publication date rather than a precise timestamp in
  the endpoint used here.
- Verify AI-written summaries and political attributions against the linked source.
- Scheduled workflows in inactive public repositories can be disabled by GitHub after a long
  period without repository activity. Check the Actions tab if a daily edition stops appearing.
