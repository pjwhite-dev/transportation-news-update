# Transportation News Update

A public Streamlit page that produces a daily, AI-assisted briefing covering the preceding
24 hours of:

- UAS and drones
- UAS security and C-UAS
- eVTOL Integration Pilot Program and advanced air mobility
- Autonomous vehicles
- Other advanced transportation, including civil supersonics and rail innovation
- Federal actions
- Verified Trump Administration wins tied to relevant policy actions and executive orders

The finished edition includes an Executive Summary, Trump Administration Wins, Top
Developments, topic sections, What to Watch, and a **Copy for Outlook** button.

## Repository files

```text
streamlit_app.py
news_engine.py
daily_update.py
requirements.txt
README.md
.streamlit/config.toml
.github/workflows/daily-news-update.yml
data/latest_briefing.json
data/archive/
```

## Required secrets

### Streamlit Secrets

In **Manage app → Settings → Secrets**, use:

```toml
openai_api_key = "sk-proj-..."
owner_password = "YOUR_PRIVATE_OWNER_PASSWORD"
openai_model = "gpt-5.4-mini"
```

The Streamlit key supports the optional owner-only **Run AI analysis now** button.

### GitHub Actions secret

In the GitHub repository, go to:

**Settings → Secrets and variables → Actions → New repository secret**

Create:

```text
Name: OPENAI_API_KEY
Secret: your sk-proj-... key
```

Never place the API key directly in a code, JSON, Markdown, TXT, or YAML file.

## Daily schedule

The workflow runs every day at **4:15 a.m. America/New_York**. It:

1. Collects records published during the preceding 24 hours.
2. Uses OpenAI to remove irrelevant results, cluster true duplicate coverage, summarize the
   news, create the Executive Summary, identify supported Administration wins, and draft
   What to Watch.
3. Writes `data/latest_briefing.json`.
4. Archives the edition in `data/archive/YYYY-MM-DD.json`.
5. Commits the generated files to GitHub, which causes Streamlit to refresh the site.

GitHub scheduled workflows can occasionally run a few minutes late. The generated briefing
always labels its exact 24-hour coverage window.

## First test

After uploading the files and adding the GitHub secret:

1. Open the repository's **Actions** tab.
2. Select **Daily Transportation News Update**.
3. Click **Run workflow**.
4. Wait for the run to complete.
5. Confirm that `data/latest_briefing.json` and a dated archive file were updated.
6. Open the Streamlit site and use **Copy for Outlook**.

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
