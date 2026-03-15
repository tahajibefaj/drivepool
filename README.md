# DrivePool 🌊
**One Dashboard for All Your Google Drives**

Connect 5, 10, or unlimited Google Drive accounts → get N × 15 GB free storage.  
Hosted online for free on Render.com. Mobile responsive. Auto-sleep when idle, wakes in seconds.

---

## How hosting works

| Behavior | Details |
|---|---|
| **Sleep** | Server sleeps automatically after ~15 min of no activity |
| **Wake** | Visit your Render URL from any device → server wakes in ~30 seconds |
| **During uploads** | Server stays awake (keep-alive ping every 30s) |
| **During downloads** | Server stays awake automatically |
| **Cost** | $0 forever (Render free tier) |

---

## Full Deploy Guide (one-time, ~15 minutes)

### Step 1 — Fork this repo

Go to GitHub → Fork this repo to your account.

### Step 2 — Get Google OAuth credentials (free)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Click **Select Project → New Project**
3. Name it `DrivePool` → Create
4. Left menu: **APIs & Services → Library**
   - Search `Google Drive API` → Enable
   - Search `Google People API` → Enable  
   *(People API is for fetching your name and avatar)*
5. Left menu: **APIs & Services → OAuth consent screen**
   - User type: **External** → Create
   - App name: `DrivePool`
   - User support email: your email
   - Developer contact: your email → Save & Continue
   - Scopes: skip for now → Save & Continue
   - **Test users**: click "Add Users" → add **every Google account email** you want to connect → Save
   - *(You can add more test users later)*
6. Left menu: **APIs & Services → Credentials**
   - Click **+ Create Credentials → OAuth client ID**
   - Application type: **Web application**
   - Name: `DrivePool`
   - **Authorized redirect URIs**: you'll add this after you get your Render URL. For now put `http://localhost:8000/auth/callback` as placeholder.
   - Click **Create**
7. Click **Download JSON** on the created credential → you get a file named `client_secret_*.json`
8. Open it in a text editor and **copy the entire contents**

### Step 3 — Deploy on Render.com

1. Go to [render.com](https://render.com) → Sign up free (use GitHub login)
2. Click **+ New → Web Service**
3. Connect your GitHub account → select your forked `drivepool` repo
4. Render auto-detects `render.yaml`. Review settings:
   - **Name**: drivepool (or anything you like)
   - **Region**: Oregon (or nearest)
   - **Plan**: Free
5. Click **Create Web Service**
6. Wait for the first build (2-3 minutes)
7. You'll get a URL like `https://drivepool-xxxx.onrender.com` — **copy it**

### Step 4 — Set environment variables

In Render dashboard → your service → **Environment** tab → add:

| Key | Value |
|---|---|
| `BASE_URL` | `https://drivepool-xxxx.onrender.com` (your exact Render URL) |
| `GOOGLE_SECRETS_JSON` | Paste the **entire JSON content** of your downloaded credentials file |
| `DATA_DIR` | `/data` (already set in render.yaml) |

Click **Save Changes** → Render redeploys automatically.

### Step 5 — Add your Render URL to Google Console

1. Go back to Google Cloud Console → **Credentials → your OAuth client → Edit**
2. Under **Authorized redirect URIs**, add:
   `https://drivepool-xxxx.onrender.com/auth/callback`
3. **Remove** the localhost placeholder
4. Save

### Step 6 — Connect your Google accounts

1. Visit `https://drivepool-xxxx.onrender.com`
2. Wait ~30 seconds for first wake-up
3. Click **Open Dashboard → + Add Google Account**
4. Sign in with your first Google account → authorize DrivePool
5. Repeat for each Google account (add as many as you want)
6. Click the 🔄 sync button → files load from all accounts

---

## Alternative free hosting options

### Koyeb (always-on, no sleep)

1. Go to [koyeb.com](https://koyeb.com) → sign up free
2. New App → GitHub → select repo
3. Set env vars: `BASE_URL`, `GOOGLE_SECRETS_JSON`, `DATA_DIR=/data`
4. Deploy
5. Koyeb free tier: 512 MB RAM, never sleeps

### Railway

1. Go to [railway.app](https://railway.app) → sign up
2. New Project → Deploy from GitHub → select repo
3. Add env vars
4. $5/month free credit — enough for low-traffic personal use

### Google Cloud Run (scales to zero, fastest wake)

Fastest cold start (~1-2 seconds) but requires a bit more setup:
```bash
gcloud run deploy drivepool \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars BASE_URL=https://drivepool-xxx.run.app,GOOGLE_SECRETS_JSON='...'
```
Free tier: 2M requests/month, 360,000 GB-seconds compute.

---

## Adding more Google accounts

No limit on accounts. For each new account:
1. Add the email as a **Test User** in Google Cloud Console → OAuth consent screen
2. Go to DrivePool Dashboard → Add Google Account → sign in with that account

When you're ready (optional): submit your OAuth app for verification to remove the "Test Users" limit and the "app not verified" warning.

---

## Using as object storage (API)

Expose DrivePool as a backend for your projects:

```bash
# Upload
curl -X POST https://your-app.onrender.com/api/upload \
  -F "file=@myfile.pdf"

# List files
curl https://your-app.onrender.com/api/files

# Download
curl https://your-app.onrender.com/api/files/{gid}/download -o file.pdf

# Stats
curl https://your-app.onrender.com/api/stats
```

---

## Security

- OAuth tokens stored on Render's persistent disk (`/data/tokens/`)
- Files are **streamed** directly from Google Drive — never stored on the server
- `GOOGLE_SECRETS_JSON` is stored as a Render environment secret (encrypted)
- Add a Render IP allowlist if you want extra protection

## File structure

```
drivepool/
├── main.py              ← FastAPI backend
├── startup.py           ← Writes secrets from env var on boot
├── requirements.txt
├── render.yaml          ← Render auto-deploy config
├── README.md
└── frontend/
    ├── index.html       ← Landing page
    └── dashboard.html   ← Dashboard UI
```

## .gitignore

```
client_secrets.json
data/
__pycache__/
*.pyc
.venv/
.env
```
