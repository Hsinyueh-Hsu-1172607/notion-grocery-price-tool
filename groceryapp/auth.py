"""A single-user lock on the whole app.

There is one person using this, so there is no sign-up, no user table and no
username — only a password, checked against a hash kept in the environment.
Storing the hash rather than the password means a leaked .env does not hand
over the password itself.

The guard is a before_request hook rather than a decorator on each route: a
new page is then protected by default, instead of protected only if someone
remembers to decorate it.
"""
import os
from datetime import timedelta

from flask import redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from groceryapp import app

# Long enough that scanning a receipt on a phone does not start with typing a
# password, short enough that a lost phone is not open forever.
app.permanent_session_lifetime = timedelta(days=30)

# Endpoints reachable without logging in. "static" is here because the login
# page needs its stylesheet, and leaving it out is a blank white login form.
_PUBLIC_ENDPOINTS = {"login", "static"}


@app.before_request
def require_login():
    if request.endpoint in _PUBLIC_ENDPOINTS or session.get("signed_in"):
        return None
    return redirect(url_for("login", next=request.full_path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    password_hash = os.environ.get("APP_PASSWORD_HASH", "")
    if not password_hash:
        # Better to say so than to reject a correct password in silence.
        return render_template(
            "login.html",
            error="No password is set on this server. See DEPLOY.md.",
        )

    if not check_password_hash(password_hash, request.form.get("password", "")):
        return render_template("login.html", error="Wrong password.")

    session.permanent = True
    session["signed_in"] = True

    # Only ever a path on this site: an absolute URL here would let a crafted
    # link bounce someone off to another site after a genuine login.
    target = request.form.get("next", "")
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("index")
    return redirect(target)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
