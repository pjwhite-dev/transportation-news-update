name: Daily Transportation News Update

on:
  workflow_dispatch:
  schedule:
    - cron: "15 4 * * *"
      timezone: "America/New_York"

permissions:
  contents: write

concurrency:
  group: daily-transportation-news-update
  cancel-in-progress: false

jobs:
  build-daily-update:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      - name: Check out repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Generate preceding 24-hour briefing
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
          OPENAI_MODEL: gpt-5.4-mini
        run: python daily_update.py

      - name: Commit the new edition
        shell: bash
        run: |
          set -e
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          git add data/latest_briefing.json data/archive/

          if git diff --cached --quiet; then
            echo "No briefing changes to commit."
            exit 0
          fi

          git commit -m "Daily news update $(date -u +%Y-%m-%d)"

          # A person may commit to main while this workflow is generating the
          # briefing. If that happens, update this checkout and retry the push.
          for attempt in 1 2 3; do
            echo "Push attempt ${attempt}..."

            if git push origin HEAD:main; then
              echo "Daily briefing committed successfully."
              exit 0
            fi

            echo "The remote branch changed. Rebasing on the latest main branch..."
            git fetch origin main

            if ! git rebase origin/main; then
              echo "Automatic rebase failed because the same generated file changed remotely."
              git rebase --abort || true
              exit 1
            fi

            sleep $((attempt * 2))
          done

          echo "Unable to push after three attempts."
          exit 1
