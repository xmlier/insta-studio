import os
from dotenv import load_dotenv
from instagrapi import Client

# Load environment variables
load_dotenv()

# Load session
cl = Client()
session_path = os.getenv("SESSION_PATH", "session.json")
if os.path.exists(session_path):
    cl.load_settings(session_path)
    print("✓ Loaded session")
    # Login immediately
    username = os.getenv("INSTA_USERNAME")
    password = os.getenv("INSTA_PASSWORD")
    if username and password:
        cl.login(username, password)
        print("✓ Logged in")
else:
    print("✗ No session found. Run login.py first.")

# Use provided code
shortcode = "DeM5L4Nhyxk"
media_pk = cl.media_pk_from_code(shortcode)
print(f"Media PK for {shortcode}: {media_pk}")