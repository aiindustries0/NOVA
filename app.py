import os
import sqlite3
from datetime import timedelta
from functools import wraps

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "nova.db"))

app = Flask(__name__)
# Keep the key stable across restarts by sourcing it from the environment in production.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "nova-development-secret-key")
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
app.config["DATABASE"] = DATABASE


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            UNIQUE (user_id, post_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE
        );
        """
    )
    db.commit()


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    user = get_db().execute(
        "SELECT id, email, password_hash, created_at, email AS username "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if user is None:
        session.clear()
    return user


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


def load_posts(user_id=None):
    """Load posts for the feed or one profile using the same display shape."""
    db = get_db()
    query = (
        "SELECT p.id, p.user_id, p.title, p.body, p.body AS content, p.created_at, "
        "u.email, u.email AS username, COUNT(DISTINCT l.id) AS like_count "
        "FROM posts AS p JOIN users AS u ON u.id = p.user_id "
        "LEFT JOIN likes AS l ON l.post_id = p.id "
    )
    params = []
    if user_id is not None:
        query += "WHERE p.user_id = ? "
        params.append(user_id)
    query += "GROUP BY p.id ORDER BY p.created_at DESC, p.id DESC"
    return db.execute(query, params).fetchall()


def load_comments(posts):
    if not posts:
        return {}
    post_ids = [post["id"] for post in posts]
    placeholders = ",".join("?" for _ in post_ids)
    rows = get_db().execute(
        "SELECT c.id, c.post_id, c.body AS content, c.created_at, "
        "u.email, u.email AS username "
        "FROM comments AS c JOIN users AS u ON u.id = c.user_id "
        f"WHERE c.post_id IN ({placeholders}) "
        "ORDER BY c.created_at ASC, c.id ASC",
        post_ids,
    ).fetchall()
    comments = {post_id: [] for post_id in post_ids}
    for row in rows:
        comments[row["post_id"]].append(row)
    return comments


def render_feed():
    user = current_user()
    # A feed is global by definition; passing None avoids accidentally applying
    # the profile-only user filter while retaining the shared post shape.
    posts = load_posts(user_id=None)
    liked_post_ids = set()
    if user is not None:
        liked_post_ids = {
            row["post_id"]
            for row in get_db()
            .execute("SELECT post_id FROM likes WHERE user_id = ?", (user["id"],))
            .fetchall()
        }
    return render_template(
        "index.html",
        logged_in_user=user,
        posts=posts,
        liked_post_ids=liked_post_ids,
        comments_by_post=load_comments(posts),
    )


@app.route("/")
def index():
    return render_feed()


@app.route("/feed")
def feed():
    return render_feed()


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = (request.form.get("email") or request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        if not email or not password:
            flash("Email and password are required.")
            return render_template("register.html", logged_in_user=current_user()), 400
        try:
            db = get_db()
            db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, generate_password_hash(password)),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That email is already registered.")
            return render_template("register.html", logged_in_user=current_user()), 409
        flash("Registration complete. Please log in.")
        return redirect(url_for("login"))
    return render_template("register.html", logged_in_user=current_user())


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or request.form.get("username") or "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?", (email,)
        ).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.")
            return render_template("login.html", logged_in_user=current_user()), 401
        session.clear()
        session["user_id"] = user["id"]
        session.permanent = True
        flash("Welcome back.")
        return redirect(url_for("feed"))
    return render_template("login.html", logged_in_user=current_user())


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("index"))


@app.route("/post", methods=["POST"])
@login_required
def create_post():
    body = (request.form.get("body") or request.form.get("content") or "").strip()
    title = request.form.get("title", "").strip()
    if not body:
        flash("A post cannot be empty.")
        return redirect(url_for("feed"))
    if not title:
        title = body.splitlines()[0][:80]
    db = get_db()
    db.execute(
        "INSERT INTO posts (user_id, title, body) VALUES (?, ?, ?)",
        (current_user()["id"], title, body),
    )
    db.commit()
    flash("Your post is live.")
    # Redirect to the global feed so the newly committed post is re-queried
    # immediately and appears with every other user's posts.
    return redirect(url_for("feed"))


@app.route("/like/<int:post_id>", methods=["POST"])
@login_required
def like(post_id):
    db = get_db()
    if db.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        abort(404)
    user_id = current_user()["id"]
    existing = db.execute(
        "SELECT id FROM likes WHERE user_id = ? AND post_id = ?",
        (user_id, post_id),
    ).fetchone()
    if existing is None:
        db.execute("INSERT INTO likes (user_id, post_id) VALUES (?, ?)", (user_id, post_id))
    else:
        db.execute("DELETE FROM likes WHERE id = ?", (existing["id"],))
    db.commit()
    return redirect(request.referrer or url_for("feed"))


@app.route("/comment/<int:post_id>", methods=["POST"])
@login_required
def comment(post_id):
    db = get_db()
    if db.execute("SELECT id FROM posts WHERE id = ?", (post_id,)).fetchone() is None:
        abort(404)
    body = (request.form.get("body") or request.form.get("content") or "").strip()
    if not body:
        flash("A comment cannot be empty.")
        return redirect(request.referrer or url_for("feed"))
    db.execute(
        "INSERT INTO comments (user_id, post_id, body) VALUES (?, ?, ?)",
        (current_user()["id"], post_id, body),
    )
    db.commit()
    return redirect(request.referrer or url_for("feed"))


@app.route("/profile")
@app.route("/profile/<int:user_id>")
def profile(user_id=None):
    if user_id is None:
        username = request.args.get("username", "").strip()
        if username:
            target = get_db().execute(
                "SELECT id FROM users WHERE email = ?", (username,)
            ).fetchone()
            if target is not None:
                user_id = target["id"]
        if user_id is None:
            user = current_user()
            if user is None:
                return redirect(url_for("login"))
            user_id = user["id"]
    user = get_db().execute(
        "SELECT id, email, created_at, email AS username FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if user is None:
        abort(404)
    posts = load_posts(user_id)
    return render_template(
        "profile.html",
        logged_in_user=current_user(),
        user=user,
        posts=posts,
    )


with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False,
    )
