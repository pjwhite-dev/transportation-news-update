# Transportation News Update — Gemini AI Version

This version uses the Gemini Developer API for:

- relevance filtering
- duplicate-event clustering
- primary-source selection
- concise draft summaries
- executive-order mapping
- editable “Why this is a win” language
- “Also covered by” links

## Required Streamlit secrets

```toml
gemini_api_key = "YOUR_GOOGLE_AI_STUDIO_API_KEY"
owner_password = "CHOOSE_A_PRIVATE_PASSWORD"
gemini_model = "gemini-3.5-flash"
```

Never put these values in GitHub or commit a `.streamlit/secrets.toml` file.

## Update the deployed app

Replace `streamlit_app.py` and `requirements.txt` in the existing repository. Then add the
three secrets in the deployed app's Streamlit settings.

The free Gemini tier may use submitted content to improve Google products. This app is designed
for public news headlines and snippets only; do not enter internal or sensitive material.
