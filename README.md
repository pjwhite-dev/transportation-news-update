# Transportation News Update

A public Streamlit page that produces a daily, AI-assisted briefing covering the preceding
24 hours of:

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
sections, a Regulatory Deadline Tracker, What to Watch, and a **Copy for
Outlook** button.

## Repository files

```text
streamlit_app.py
news_engine.py
daily_update.py
requirements.txt
README.md
.streamlit/config.toml
.github/workflows/daily-news-update.yml
data/latest_raw_news.json
data/raw_archive/
```

## Required secrets

### Streamlit Secrets

In **Manage app → Settings → Secrets**, use:

```toml
openai_api_key = "sk-proj-..."
owner_password = "YOUR_PRIVATE_OWNER_PASSWORD"
openai_model = "gpt-5.4-mini"
```

The OpenAI key is used only after the authenticated owner starts the editorial
build in Streamlit. The scheduled GitHub Actions collection does not use OpenAI
and does not need an OpenAI secret. Never place a key directly in a code, JSON,
Markdown, TXT, or YAML file.

## Daily schedule

The GitHub Actions workflow runs every day at **4:15 a.m.
America/New_York**. It:

1. Collects records published during the preceding 24 hours.
2. Writes `data/latest_raw_news.json` and a dated raw archive.
3. Commits the public raw feed safely to GitHub.

The authenticated owner then starts the Streamlit editorial build. OpenAI first
selects, clusters, categorizes, and summarizes the stories and drafts What to
Watch. Only after that briefing and the regulatory tracker are compiled does a
separate final AI pass write the Executive Summary from the finished
reader-facing material. Deterministic coverage checks keep credible AV,
advanced rail/supersonic, and international developments from disappearing when
the raw feed contains suitable records.

GitHub scheduled workflows can occasionally run a few minutes late. The generated briefing
always labels its exact 24-hour coverage window.

## First test

After deploying the files and configuring Streamlit Secrets:

1. Open the repository's **Actions** tab.
2. Select **Daily Transportation Raw News Collection**.
3. Click **Run workflow**.
4. Wait for the run to complete.
5. Confirm that `data/latest_raw_news.json` and a dated raw archive were updated.
6. Open the Streamlit site, unlock Owner controls, and build the update.
7. Review the edition and use **Copy for Outlook**.

If the commit step reports a permissions error, open:

**Repository Settings → Actions → General → Workflow permissions**

Select **Read and write permissions**, save, and run the workflow again.

## Important notes

- The site uses public-source headlines, snippets, links, and Federal Register records.
- Google News RSS is a discovery source and can occasionally return noisy results; the AI
  relevance filter is designed to remove obvious false positives.
- Federal Register API results expose a publication date rather than a precise timestamp in
  the endpoint used here.
- Review AI-written summaries and political attributions before sending an email.
- Scheduled workflows in inactive public repositories can be disabled by GitHub after a long
  period without repository activity. Check the Actions tab if a daily edition stops appearing.
