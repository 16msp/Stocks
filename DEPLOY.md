# Hosting the app free (Streamlit Community Cloud + Supabase + GitHub Actions)

```
GitHub Actions (daily, after market close) --writes--> Supabase Postgres <--reads-- Streamlit Community Cloud app
```

Locally nothing changes: with no `DATABASE_URL` the app keeps using the SQLite file.

## 1. Supabase (the shared price store)
1. supabase.com -> New project (free plan). Note the database password.
2. Project -> **Connect** -> **Session pooler** connection string (works over IPv4, which Streamlit Cloud and GitHub Actions need). It looks like
   `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`
3. Tables are created automatically on first run - nothing to set up.

## 2. GitHub secrets (for the daily job)
Repo -> Settings -> Secrets and variables -> Actions -> New repository secret:
- `DATABASE_URL` - the Supabase string above
- `POSITIONS_SHEET_URL` - your Open Positions Google Sheet URL (so your holdings get updated too)

Then Actions tab -> **Daily price update** -> Run workflow, once, to load the first ~15 months of history.
It then runs by itself Mon-Fri 17:00 IST and only downloads new sessions.

## 3. Streamlit Community Cloud (the app)
share.streamlit.io -> New app -> repo `16msp/Stocks`, branch `main`, file `app.py` -> Advanced settings -> Secrets:
```toml
DATABASE_URL = "postgresql://..."
POSITIONS_SHEET_URL = "https://docs.google.com/spreadsheets/d/<id>/edit"
APP_PASSWORD = "choose-a-password"
```
`APP_PASSWORD` puts a password prompt in front of every page. Leave it out locally.

## 4. The "Run price update now" button (optional)
GitHub's schedule is often late or skipped, so the app can start the same job itself. The button is in the sidebar of the home page, Mean Reversion and Dip Watchlist.
1. GitHub -> Settings -> Developer settings -> Personal access tokens -> **Fine-grained tokens** -> Generate new token.
2. Repository access: **Only select repositories** -> `16msp/Stocks`. Permissions -> Repository permissions -> **Actions: Read and write** (Metadata: Read-only is added automatically). Nothing else. Pick an expiry you can live with (e.g. 1 year).
3. Streamlit Cloud -> app -> Settings -> Secrets: add `GITHUB_DISPATCH_TOKEN = "github_pat_..."` (never put it in the repo).
The button only starts a run when you press it, refuses while a run is already waiting or running, and waits a minute between clicks. Anyone who can open the app (see `APP_PASSWORD`) can press it, which can only start the price job. If the token expires or is revoked the sidebar says so and the button stops working; nothing else is affected.

## Things to know
- **Only Position Guide and Sector Scanner are cloud-ready.** The ETF pages keep their data in the local SQLite file and will start empty on the hosted app.
- **Your sheet is link-shared.** The hosted app (and the job) read it through its "anyone with the link" CSV export, so anyone who has that link can read your holdings. Keep the link out of public places.
- **Supabase free pauses after 7 days idle.** The daily job counts as activity. GitHub also switches off scheduled workflows after ~60 days without repo activity - re-enable from the Actions tab if that happens.
- **Yahoo/NSE can block cloud IPs now and then.** If a day's job fails, the app keeps showing the last stored session; rerun the workflow.
- Local secrets live in `.streamlit/secrets.toml` (gitignored); see `.streamlit/secrets.toml.example`.
