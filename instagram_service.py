import os
import re
import time
import uuid
import random
import threading
import requests
from instagrapi import Client

# Global instagrapi client
cl = None

# Persistent session location
SESSION_DIR = "/app/data" if os.path.isdir("/app/data") else "./data"
os.makedirs(SESSION_DIR, exist_ok=True)
SESSION_FILE = os.path.join(SESSION_DIR, "instagram_session.json")

# ---- Scheduler job state ----
_jobs = {}
_job_lock = threading.Lock()
_active_job_id = None
_cancel_flags = {}


# ---------- Core helpers ----------

def human_delay(min_sec=30, max_sec=90):
    delay = random.uniform(min_sec, max_sec)
    print(f"⏳ Waiting {delay:.1f}s...")
    time.sleep(delay)


def login(username, password):
    global cl
    try:
        cl = Client()
        if os.path.exists(SESSION_FILE):
            try:
                cl.load_settings(SESSION_FILE)
                cl.login(username, password)
                cl.dump_settings(SESSION_FILE)
                return True, "Logged in using saved session"
            except Exception:
                cl = Client()
        cl.login(username, password)
        cl.dump_settings(SESSION_FILE)
        return True, "Login successful"
    except Exception as e:
        msg = str(e).lower()
        if "challenge" in msg:
            return False, ("Challenge required — open the Instagram app on your phone, "
                           "approve the login, then try again here.")
        if "bad_password" in msg or "password" in msg:
            return False, "Incorrect password."
        if "two_factor" in msg or "2fa" in msg:
            return False, "Two-factor required — this app does not support 2FA login yet."
        if "out of date" in msg or "upgrade" in msg:
            if os.path.exists(SESSION_FILE):
                try: os.remove(SESSION_FILE)
                except: pass
            return False, "Instagram flagged the saved session as outdated. The session was cleared—please try clicking Sign In one more time to generate a fresh session."
        if "nonetype" in msg and "int(" in msg:
            return False, "Instagram blocked the login or returned an unrecognized response. Please check your account in the official Instagram app."
        return False, f"Login error: {str(e)}"


def logout():
    global cl
    cl = None
    try:
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
    except Exception:
        pass
    return True, "Logged out"


def _resolve_media_id(value):
    s = str(value).strip()
    if not s:
        raise ValueError("Empty media ID")
    if "instagram.com" in s:
        return cl.media_pk_from_url(s)
    if not s.isdigit():
        return cl.media_pk_from_code(s)
    return int(s)


def get_user_posts(username, count=5):
    """Return a list of media pks for the most recent posts of a username."""
    if not cl:
        return []
    try:
        user_id = cl.user_id_from_username(username)
        medias = cl.user_medias(user_id, amount=count)
        return [m.pk for m in medias]
    except Exception as e:
        print(f"get_user_posts({username}) failed: {e}")
        return []


def _handle_error(e):
    msg = str(e).lower()
    if "challenge" in msg:
        return "Challenge required — resolve it in the Instagram app."
    if "comment" in msg and "limit" in msg:
        return "Comments are limited on this post."
    if "feedback_required" in msg:
        return "Action blocked by Instagram — slow down and try later."
    if "login_required" in msg:
        return "Session expired — log in again."
    if "rate" in msg or "429" in msg:
        return "Rate limited — wait a few minutes before retrying."
    if "media" in msg and "not" in msg:
        return "Media not found — check the post URL or ID."
    return f"Error: {str(e)}"


def comment_on_post(media_id, text):
    if not cl:
        return False, "Not logged in"
    try:
        pk = _resolve_media_id(media_id)
        cl.media_comment(pk, text)
        return True, f"Commented on {pk}"
    except Exception as e:
        return False, _handle_error(e)


def repost_media(media_id, caption=""):
    if not cl:
        return False, "Not logged in"

    temp_file = None
    try:
        pk = _resolve_media_id(media_id)
        info = cl.media_info(pk)
        if not info:
            return False, "Could not fetch media info"

        if info.media_type == 2:
            media_url = info.video_url
            ext = ".mp4"
        else:
            media_url = info.thumbnail_url
            ext = ".jpg"

        if not media_url:
            return False, "No downloadable media URL available"

        r = requests.get(media_url, stream=True, timeout=60)
        if r.status_code != 200:
            return False, f"Download failed (HTTP {r.status_code})"

        temp_file = os.path.join(SESSION_DIR, f"temp_{uuid.uuid4().hex[:6]}{ext}")
        with open(temp_file, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

        owner = info.user.username if info.user else "unknown"
        if not caption:
            caption = f"Reposted from @{owner}"
        else:
            caption = caption.replace("{username}", owner)

        if info.media_type == 2:
            cl.video_upload(temp_file, caption=caption)
        else:
            cl.photo_upload(temp_file, caption=caption)

        return True, f"Reposted @{owner}'s post"

    except Exception as e:
        return False, _handle_error(e)
    finally:
        if temp_file and os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


def get_profile_data():
    if not cl:
        return None
    try:
        return cl.user_info(cl.user_id).dict()
    except Exception:
        return None


# ---------- Scheduler ----------

def _now():
    return time.time()


def _log(job_id, message, level="info"):
    with _job_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["log"].append({"ts": _now(), "level": level, "message": message})
        job["log"] = job["log"][-300:]
        job["last_message"] = message
    print(f"[job {job_id[:6]}] {message}")


def _expand_targets(text):
    """Parse user input into a deduplicated list of media pks.

    Each line is one of:
      - A post URL, shortcode, or numeric ID
      - @username:N   (fetch N recent posts from that user, max 20)
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        raise ValueError("No targets provided")

    resolved = []
    seen = set()

    for line in lines:
        m = re.match(r"^@([A-Za-z0-9_.]+)(?::(\d+))?$", line)
        if m:
            username = m.group(1)
            count = min(int(m.group(2) or 5), 20)
            pks = get_user_posts(username, count)
            if not pks:
                raise ValueError(f"Could not fetch posts from @{username}")
            for pk in pks:
                if pk not in seen:
                    resolved.append(pk)
                    seen.add(pk)
            continue

        try:
            pk = _resolve_media_id(line)
        except Exception as e:
            raise ValueError(f"Could not resolve '{line}': {e}")

        if pk not in seen:
            resolved.append(pk)
            seen.add(pk)

    return resolved


def _interruptible_sleep(job_id, seconds):
    """Sleep that can be cancelled. Returns False if cancelled."""
    cancel = _cancel_flags.get(job_id)
    slept = 0.0
    while slept < seconds:
        if cancel and cancel.is_set():
            with _job_lock:
                _jobs[job_id]["status"] = "cancelled"
            _log(job_id, "Cancelled.", "error")
            return False
        time.sleep(1)
        slept += 1
    return True


def _run_job(job_id):
    global _active_job_id
    with _job_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = _now()

    targets = job["targets"]
    times = job.get("times", 1)
    action = job["action"]
    comment_text = job["comment_text"]

    success = 0
    failed = 0

    try:
        for iteration in range(times):
            for i, pk in enumerate(targets):
                if _cancel_flags.get(job_id, threading.Event()).is_set():
                    with _job_lock:
                        _jobs[job_id]["status"] = "cancelled"
                    return

                _log(job_id, f"[Iter {iteration+1}/{times}] Processing {pk}")
                ok = True

                if action in ("comment", "both"):
                    c_ok, c_msg = comment_on_post(pk, comment_text)
                    _log(job_id, f"  Comment: {c_msg}", "success" if c_ok else "error")
                    if not c_ok: ok = False

                    if action == "both":
                        delay = random.uniform(300, 600)  # 5 to 10 minutes
                        _log(job_id, f"Waiting {int(delay/60)}m {int(delay%60)}s before reposting...")
                        if not _interruptible_sleep(job_id, delay):
                            return

                if action in ("repost", "both"):
                    r_ok, r_msg = repost_media(pk)
                    _log(job_id, f"  Repost: {r_msg}", "success" if r_ok else "error")
                    if not r_ok: ok = False

                if ok:
                    success += 1
                else:
                    failed += 1

                with _job_lock:
                    _jobs[job_id]["progress"] += 1
                    _jobs[job_id]["success_count"] = success
                    _jobs[job_id]["fail_count"] = failed

                # Wait between 5 to 10 minutes before next action unless we are completely finished
                if not (iteration == times - 1 and i == len(targets) - 1):
                    delay = random.uniform(300, 600)
                    _log(job_id, f"Waiting {int(delay/60)}m {int(delay%60)}s before next action...")
                    if not _interruptible_sleep(job_id, delay):
                        return

        with _job_lock:
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["finished_at"] = _now()
        _log(job_id, f"Completed. Success: {success}, Failed: {failed}")

    except Exception as e:
        _log(job_id, f"Job crashed: {e}", "error")
        with _job_lock:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["finished_at"] = _now()
    finally:
        with _job_lock:
            _active_job_id = None
        _cancel_flags.pop(job_id, None)


def start_job(targets_text, action, comment_text, times=1):
    global _active_job_id

    if not cl:
        return None, "Not logged in"
    if action not in ("comment", "repost", "both"):
        return None, "Invalid action"
    if not comment_text and action in ("comment", "both"):
        return None, "Comment text is required for that action"

    try:
        times = int(times)
        if times < 1:
            times = 1
    except (TypeError, ValueError):
        times = 1

    try:
        targets = _expand_targets(targets_text)
    except ValueError as e:
        return None, str(e)

    if not targets:
        return None, "No valid targets found"

    with _job_lock:
        if _active_job_id is not None:
            return None, "Another job is already running. Cancel it or wait."
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {
            "id": job_id,
            "targets": targets,
            "action": action,
            "comment_text": comment_text,
            "times": times,
            "count": len(targets) * times,
            "status": "pending",
            "progress": 0,
            "success_count": 0,
            "fail_count": 0,
            "log": [],
            "last_message": "",
            "error": None,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
        }
        _active_job_id = job_id
        _cancel_flags[job_id] = threading.Event()

    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    t.start()
    return job_id, None


def get_job(job_id):
    with _job_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def cancel_job(job_id):
    with _job_lock:
        if job_id not in _jobs:
            return False, "Job not found"
    event = _cancel_flags.get(job_id)
    if event:
        event.set()
        return True, "Cancel requested"
    return False, "Job is not running"