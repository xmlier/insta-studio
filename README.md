# Instagram Playground

Flask app using instagrapi to comment/repost on Instagram posts, with a scheduled batch job system, deployable to Railway.

## ⚠️ Warning
Uses Instagram's private API, violates ToS, use only on a burner account.

## Local Run
```
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

## Deploy to Railway
1. Push this repo to a private GitHub repository
2. On railway.app → New Project → Deploy from GitHub repo → select repo
3. Add a Volume mounted at /app/data (1 GB minimum)
4. Set environment variable SECRET_KEY (generate with:
   python3 -c "import secrets; print(secrets.token_hex(32))")
5. Generate a public domain under Settings → Networking
6. Open the URL, log in with a burner account, test a small job

## Known Limitations
- Video reposts require ffmpeg (not installed on Railway by default)
- Only one job runs at a time
- Jobs are not resumed after a server restart
- First deploy takes 3–5 minutes (Pillow/pydantic compile from source)
