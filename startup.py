"""
Startup helper: writes GOOGLE_SECRETS_JSON env var to client_secrets.json
so you don't have to store the file on git.

Called automatically before main.py starts via start.sh / Procfile.
"""
import os, json, sys
from pathlib import Path

def setup():
    # Write secrets from env var (Render / Railway / Koyeb)
    raw = os.getenv("GOOGLE_SECRETS_JSON","")
    if raw.strip():
        try:
            parsed = json.loads(raw)
            Path("client_secrets.json").write_text(json.dumps(parsed))
            print("✅ client_secrets.json written from GOOGLE_SECRETS_JSON env var")
        except Exception as e:
            print(f"⚠️  Could not parse GOOGLE_SECRETS_JSON: {e}")
            sys.exit(1)
    elif Path("client_secrets.json").exists():
        print("✅ client_secrets.json found on disk")
    else:
        print("⚠️  No client_secrets.json and no GOOGLE_SECRETS_JSON env var set.")
        print("   Set GOOGLE_SECRETS_JSON in your hosting dashboard.")

    # Fix BASE_URL for OAuth callback
    base = os.getenv("BASE_URL","")
    if not base:
        port = os.getenv("PORT","10000")
        print(f"⚠️  BASE_URL not set. Defaulting to http://localhost:{port}")
        print("   Set BASE_URL=https://your-app.onrender.com in Render env vars.")

if __name__ == "__main__":
    setup()
    # Now start the actual app
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
