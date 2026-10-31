import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from markupsafe import Markup
from pymysql.err import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from db import close_db, execute, init_db, query

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-production")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
app.teardown_appcontext(close_db)

BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
URGENCY_LEVELS = ["normal", "urgent", "critical"]

# recipient blood group -> donor groups that can give to it
COMPATIBLE_DONORS = {
    "O-": ["O-"],
    "O+": ["O+", "O-"],
    "A-": ["A-", "O-"],
    "A+": ["A+", "A-", "O+", "O-"],
    "B-": ["B-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "AB-": ["AB-", "A-", "B-", "O-"],
    "AB+": BLOOD_GROUPS,
}

DONATION_GAP_DAYS = 90
MAX_UNITS = 20
LOCAL_TZ = timezone(timedelta(hours=int(os.getenv("TZ_OFFSET_HOURS", "5"))))  # Pakistan Standard Time

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9\s-]{6,18}$")

# max lengths match the column sizes in db.py
LIMITS = {"full_name": 100, "email": 120, "city": 60, "phone": 20,
          "patient_name": 100, "hospital": 120, "contact": 20, "message": 255}


def today():
    return datetime.now(LOCAL_TZ).date()


def clean(form, field):
    """Stripped form value, or None if it's missing, empty or too long."""
    value = (form.get(field) or "").strip()
    if not value or len(value) > LIMITS.get(field, 255):
        return None
    return value


def parse_past_date(value):
    """Return a date that is not in the future, None for blank, or False if invalid."""
    if not value:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return False
    return d if d <= today() else False


def safe_next(target):
    """Only allow redirects back to a path on this site."""
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


# ---------------------------------------------------------------- CSRF protection
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Your session expired or the form was invalid. Please try again.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def current_donor():
    if "user_id" not in session:
        return None
    return query("SELECT * FROM donors WHERE user_id=%s", (session["user_id"],), one=True)


def is_eligible(last_donation):
    return last_donation is None or (today() - last_donation).days >= DONATION_GAP_DAYS


def placeholders(items):
    return ",".join(["%s"] * len(items))


@app.context_processor
def inject_globals():
    return {
        "BLOOD_GROUPS": BLOOD_GROUPS,
        "is_eligible": is_eligible,
        "csrf_field": lambda: Markup(f'<input type="hidden" name="_csrf" value="{csrf_token()}">'),
        "today": today,
        "LIMITS": LIMITS,
        "MAX_UNITS": MAX_UNITS,
    }


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(405)
@app.errorhandler(500)
def error_page(err):
    code = getattr(err, "code", 500) or 500
    messages = {400: "That request couldn't be processed.",
                404: "We couldn't find that page.",
                405: "That action isn't allowed here.",
                500: "Something went wrong on our side."}
    detail = getattr(err, "description", None) if code == 400 else None
    return render_template("error.html", code=code, message=detail or messages.get(code, "Error")), code


# ---------------------------------------------------------------- health
@app.route("/health")
def health():
    try:
        query("SELECT 1", one=True)
        return {"status": "ok", "database": "connected"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "database": str(e)}, 503


# ---------------------------------------------------------------- module 1: auth
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = clean(request.form, "full_name")
        email = (clean(request.form, "email") or "").lower()
        password = request.form.get("password", "")
        if not name or not EMAIL_RE.match(email) or not 6 <= len(password) <= 128:
            flash("Enter your name, a valid email, and a password of 6-128 characters.", "danger")
        elif query("SELECT id FROM users WHERE email=%s", (email,), one=True):
            flash("An account with that email already exists.", "danger")
        else:
            try:
                uid = execute(
                    "INSERT INTO users (full_name, email, password_hash) VALUES (%s,%s,%s)",
                    (name, email, generate_password_hash(password)),
                )
            except IntegrityError:  # same email submitted twice at once
                flash("An account with that email already exists.", "danger")
                return render_template("auth/register.html")
            session.clear()
            session.update(user_id=uid, user_name=name)
            flash("Welcome to BloodLink! Register as a donor to start saving lives.", "success")
            return redirect(url_for("dashboard"))
    return render_template("auth/register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = query("SELECT * FROM users WHERE email=%s", (email,), one=True)
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session.update(user_id=user["id"], user_name=user["full_name"])
            flash(f"Welcome back, {user['full_name']}.", "success")
            return redirect(safe_next(request.args.get("next")) or url_for("dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("auth/login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- module 2: donors
@app.route("/donor/profile", methods=["GET", "POST"])
@login_required
def donor_profile():
    donor = current_donor()
    if request.method == "POST":
        bg = request.form.get("blood_group")
        city = clean(request.form, "city")
        phone = clean(request.form, "phone")
        last = parse_past_date(request.form.get("last_donation"))
        available = 1 if request.form.get("is_available") else 0
        if bg not in BLOOD_GROUPS or not city or not phone:
            flash("Please fill in blood group, city (max 60 chars) and phone.", "danger")
        elif not PHONE_RE.match(phone):
            flash("Enter a valid phone number, e.g. 0300-1234567.", "danger")
        elif last is False:
            flash("Last donation date must be a valid date that isn't in the future.", "danger")
        elif donor:
            execute(
                "UPDATE donors SET blood_group=%s, city=%s, phone=%s, last_donation=%s, is_available=%s WHERE id=%s",
                (bg, city.title(), phone, last, available, donor["id"]),
            )
            flash("Donor profile updated.", "success")
            return redirect(url_for("donor_profile"))
        else:
            # upsert so a double-submitted form can't trip the unique user_id constraint
            execute(
                """INSERT INTO donors (user_id, blood_group, city, phone, last_donation, is_available)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE blood_group=VALUES(blood_group), city=VALUES(city),
                     phone=VALUES(phone), last_donation=VALUES(last_donation), is_available=VALUES(is_available)""",
                (session["user_id"], bg, city.title(), phone, last, available),
            )
            flash("You are now registered as a donor. Thank you!", "success")
            return redirect(url_for("donor_profile"))
    return render_template("donors/profile.html", donor=donor)


@app.route("/donors")
def donor_search():
    bg = request.args.get("blood_group", "")
    city = request.args.get("city", "").strip()[:LIMITS["city"]]
    sql = """SELECT d.*, u.full_name FROM donors d JOIN users u ON u.id=d.user_id
             WHERE d.is_available=1"""
    args = []
    if bg in BLOOD_GROUPS:
        sql += f" AND d.blood_group IN ({placeholders(COMPATIBLE_DONORS[bg])})"
        args += COMPATIBLE_DONORS[bg]
    else:
        bg = ""
    if city:
        sql += " AND d.city LIKE %s"
        args.append("%" + city.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%")
    sql += " ORDER BY d.city, d.blood_group"
    donors = [d for d in query(sql, args) if is_eligible(d["last_donation"])]
    return render_template("donors/search.html", donors=donors, bg=bg, city=city,
                           compatible=COMPATIBLE_DONORS.get(bg))


# ---------------------------------------------------------------- module 3: blood requests
@app.route("/requests")
def request_list():
    status = request.args.get("status", "open")
    status = status if status in ("open", "fulfilled") else "open"
    reqs = query(
        """SELECT r.*, u.full_name AS requester,
                  (SELECT COUNT(*) FROM responses x WHERE x.request_id=r.id) AS response_count
           FROM blood_requests r JOIN users u ON u.id=r.user_id
           WHERE r.status=%s
           ORDER BY FIELD(r.urgency,'critical','urgent','normal'), r.created_at DESC""",
        (status,),
    )
    return render_template("requests/list.html", reqs=reqs, status=status)


@app.route("/requests/new", methods=["GET", "POST"])
@login_required
def request_new():
    if request.method == "POST":
        f = request.form
        fields = {k: clean(f, k) for k in ("patient_name", "hospital", "city", "contact")}
        try:
            units = int(f.get("units", ""))
        except ValueError:
            units = 0
        urgency = f.get("urgency", "normal")
        if f.get("blood_group") not in BLOOD_GROUPS or not all(fields.values()):
            flash("Please fill in every field (text fields have length limits).", "danger")
        elif not 1 <= units <= MAX_UNITS:
            flash(f"Units must be between 1 and {MAX_UNITS}.", "danger")
        elif urgency not in URGENCY_LEVELS:
            flash("Choose a valid urgency level.", "danger")
        elif not PHONE_RE.match(fields["contact"]):
            flash("Enter a valid contact number, e.g. 0300-1234567.", "danger")
        else:
            rid = execute(
                """INSERT INTO blood_requests
                   (user_id, patient_name, blood_group, units, hospital, city, contact, urgency)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (session["user_id"], fields["patient_name"], f["blood_group"], units,
                 fields["hospital"], fields["city"].title(), fields["contact"], urgency),
            )
            flash("Blood request posted. Matching donors are shown below.", "success")
            return redirect(url_for("request_detail", rid=rid))
    return render_template("requests/new.html")


def can_donor_respond(donor, r, groups, already):
    return bool(donor and not already and r["status"] == "open"
                and donor["user_id"] != r["user_id"]
                and donor["blood_group"] in groups and is_eligible(donor["last_donation"]))


@app.route("/requests/<int:rid>")
def request_detail(rid):
    r = query(
        """SELECT r.*, u.full_name AS requester FROM blood_requests r
           JOIN users u ON u.id=r.user_id WHERE r.id=%s""",
        (rid,), one=True,
    )
    if not r:
        abort(404)
    groups = COMPATIBLE_DONORS[r["blood_group"]]
    matches = query(
        f"""SELECT d.*, u.full_name FROM donors d JOIN users u ON u.id=d.user_id
            WHERE d.is_available=1 AND d.city=%s AND d.user_id<>%s
            AND d.blood_group IN ({placeholders(groups)})""",
        [r["city"], r["user_id"], *groups],
    )
    matches = [m for m in matches if is_eligible(m["last_donation"])]
    responses = query(
        """SELECT x.*, u.full_name, d.blood_group, d.phone FROM responses x
           JOIN donors d ON d.id=x.donor_id JOIN users u ON u.id=d.user_id
           WHERE x.request_id=%s ORDER BY x.created_at""",
        (rid,),
    )
    donor = current_donor()
    already = bool(donor and any(x["donor_id"] == donor["id"] for x in responses))
    return render_template("requests/detail.html", r=r, matches=matches, responses=responses,
                           donor=donor, already=already, groups=groups,
                           can_respond=can_donor_respond(donor, r, groups, already))


@app.route("/requests/<int:rid>/respond", methods=["POST"])
@login_required
def request_respond(rid):
    donor = current_donor()
    r = query("SELECT * FROM blood_requests WHERE id=%s", (rid,), one=True)
    if not r:
        abort(404)
    if not donor:
        flash("Create a donor profile before responding to requests.", "warning")
        return redirect(url_for("donor_profile"))
    groups = COMPATIBLE_DONORS[r["blood_group"]]
    if not can_donor_respond(donor, r, groups, already=False):
        flash("You can't respond to this request.", "danger")
        return redirect(url_for("request_detail", rid=rid))
    execute(
        "INSERT IGNORE INTO responses (request_id, donor_id, message) VALUES (%s,%s,%s)",
        (rid, donor["id"], (request.form.get("message") or "").strip()[:LIMITS["message"]]),
    )
    flash("Thank you! The requester can now see your contact details.", "success")
    return redirect(url_for("request_detail", rid=rid))


@app.route("/requests/<int:rid>/fulfill", methods=["POST"])
@login_required
def request_fulfill(rid):
    r = query("SELECT * FROM blood_requests WHERE id=%s", (rid,), one=True)
    if not r:
        abort(404)
    if r["user_id"] == session["user_id"]:
        execute("UPDATE blood_requests SET status='fulfilled' WHERE id=%s", (rid,))
        flash("Request marked as fulfilled.", "success")
    else:
        flash("Only the person who posted this request can close it.", "danger")
    return redirect(url_for("request_detail", rid=rid))


# ---------------------------------------------------------------- module 4: dashboard
@app.route("/")
def index():
    stats = {
        "donors": query("SELECT COUNT(*) c FROM donors WHERE is_available=1", one=True)["c"],
        "open": query("SELECT COUNT(*) c FROM blood_requests WHERE status='open'", one=True)["c"],
        "fulfilled": query("SELECT COUNT(*) c FROM blood_requests WHERE status='fulfilled'", one=True)["c"],
    }
    urgent = query(
        """SELECT * FROM blood_requests WHERE status='open'
           ORDER BY FIELD(urgency,'critical','urgent','normal'), created_at DESC LIMIT 4"""
    )
    return render_template("index.html", stats=stats, urgent=urgent)


@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]
    by_group = {row["blood_group"]: row["c"] for row in query(
        "SELECT blood_group, COUNT(*) c FROM donors WHERE is_available=1 GROUP BY blood_group")}
    need_by_group = {row["blood_group"]: row["c"] for row in query(
        "SELECT blood_group, COUNT(*) c FROM blood_requests WHERE status='open' GROUP BY blood_group")}
    my_requests = query(
        """SELECT r.*, (SELECT COUNT(*) FROM responses x WHERE x.request_id=r.id) AS response_count
           FROM blood_requests r WHERE r.user_id=%s ORDER BY r.created_at DESC""", (uid,))
    donor = current_donor()
    my_responses = []
    next_eligible = None
    if donor:
        my_responses = query(
            """SELECT x.*, r.patient_name, r.blood_group, r.hospital, r.status FROM responses x
               JOIN blood_requests r ON r.id=x.request_id WHERE x.donor_id=%s
               ORDER BY x.created_at DESC""", (donor["id"],))
        if donor["last_donation"]:
            next_eligible = donor["last_donation"] + timedelta(days=DONATION_GAP_DAYS)
    max_count = max([*by_group.values(), *need_by_group.values(), 1])
    return render_template("dashboard.html", by_group=by_group, need_by_group=need_by_group,
                           my_requests=my_requests, my_responses=my_responses, donor=donor,
                           next_eligible=next_eligible, max_count=max_count)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
