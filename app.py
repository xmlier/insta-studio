from flask import Flask, render_template, request, jsonify
import os

from instagram_service import (
    login, logout, get_profile_data,
    start_job, get_job, cancel_job,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-key")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"success": False, "message": "Username and password required"}), 400
    success, message = login(username, password)
    return jsonify({"success": success, "message": message})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    success, message = logout()
    return jsonify({"success": success, "message": message})


@app.route("/api/schedule", methods=["POST"])
def api_schedule():
    data = request.get_json(silent=True) or {}
    job_id, error = start_job(
        targets_text=data.get("targets", ""),
        action=data.get("action", "both"),
        comment_text=data.get("comment_text", ""),
        times=data.get("times", 1)
    )
    if error:
        return jsonify({"success": False, "message": error}), 400
    return jsonify({"success": True, "message": "Job started", "job_id": job_id})


@app.route("/api/job/<job_id>", methods=["GET"])
def api_job(job_id):
    job = get_job(job_id)
    if not job:
        return jsonify({"success": False, "message": "Job not found"}), 404
    return jsonify({"success": True, "job": job})


@app.route("/api/job/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    ok, msg = cancel_job(job_id)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/profile", methods=["GET"])
def api_profile():
    data = get_profile_data()
    if data:
        return jsonify({"success": True, "data": data})
    return jsonify({"success": False, "message": "Not logged in"}), 401


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)