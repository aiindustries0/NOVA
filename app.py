"""NOVA: a tiny social-network MVP built with Flask and SQLite.

This file keeps the app intentionally small so it is easy to read and extend.
"""

import os
import sqlite3
from functools import wraps

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["DATABASE"] = os.path.join(app.instance_path, "nova.db")


# Create the instance folder before SQLite tries to create the database file.
os.makedirs(app.instance_path, exist_ok=True)


def get_db():
    """Open one database connection for the current request."""
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(error=None):
    """Close the database connection when the request is finished."""
    database = g.pop("db", None)
    if database is not None:
        database.close()


def init_db():
    """Create the tables the first time the app starts."""
    database = get_db()
    database.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS likes (
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, post_id),
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (post_id) REFERENCES posts (id)
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (post_id) REFERENCES posts (id)
        );
        """
    )
    database.commit()


with app.app_context():
    init_db()


def login_required(view):
    """Send visitors to login before they use a member-only page."""
    @wraps(view)
    def wrapped_view(**kwargs):
        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return view(**kwargs)

    return wrapped_view


@app.context_processor
def add_logged_in_user():
    """Make the logged-in user's name available to every template."""
    user = None
    if "user_id" in session:
        user = get_db().execute(
            "SELECT id, username FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
    return {"logged_in_user": user}


@app.route("/")
def index():
    """Show every post, newest first, with its likes and comments."""
    database = get_db()
    posts = database.execute(
        """
        SELECT posts.id, posts.content, posts.created_at, users.username,
               COUNT(DISTINCT likes.user_id) AS like_count
        FROM posts
        JOIN users ON users.id = posts.user_id
        LEFT JOIN likes ON likes.post_id = posts.id
        GROUP BY posts.id
        ORDER BY posts.created_at DESC, posts.id DESC
        """
    ).fetchall()

    comments_by_post = {}
    for post in posts:
        comments_by_post[post["id"]] = database.execute(
            """
            SELECT comments.content, comments.created_at, users.username
            FROM comments
            JOIN users ON users.id = comments.user_id
            WHERE comments.post_id = ?
            ORDER BY comments.created_at ASC, comments.id ASC
            """,
            (post["id"],),
        ).fetchall()

    liked_post_ids = set()
    if "user_id" in session:
        liked_rows = database.execute(
            "SELECT post_id FROM likes WHERE user_id = ?", (session["user_id"],)
        ).fetchall()
        liked_post_ids = {row["post_id"] for row in liked_rows}

    return render_template(
        "index.html",
        posts=posts,
        comments_by_post=comments_by_post,
        liked_post_ids=liked_post_ids,
    )


@app.route("/register", methods=("GET", "POST"))
def register():
    """Create a new account."""
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        error = None

        if not username:
            error = "Username is required."
        elif not password:
            error = "Password is required."
        elif len(username) > 30:
            error = "Username must be 30 characters or fewer."

        if error is None:
            try:
                database = get_db()
                database.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, generate_password_hash(password)),
                )
                database.commit()
            except sqlite3.IntegrityError:
                error = "That username is already taken."
            else:
                flash("Account created. You can now log in.")
                return redirect(url_for("login"))

        flash(error)

    return render_template("register.html")


@app.route("/login", methods=("GET", "POST"))
def login():
    """Log a user in with a username and password."""
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect username or password.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Log the current user out."""
    session.clear()
    flash("You are logged out.")
    return redirect(url_for("index"))


@app.route("/post", methods=("POST",))
@login_required
def create_post():
    """Add a short post to the feed."""
    content = request.form["content"].strip()
    if not content:
        flash("A post cannot be empty.")
    elif len(content) > 500:
        flash("Posts must be 500 characters or fewer.")
    else:
        database = get_db()
        database.execute(
            "INSERT INTO posts (user_id, content) VALUES (?, ?)",
            (session["user_id"], content),
        )
        database.commit()
        flash("Post published!")
    return redirect(url_for("index"))


@app.route("/post/<int:post_id>/like", methods=("POST",))
@login_required
def like(post_id):
    """Toggle the current user's like on a post."""
    database = get_db()
    existing_like = database.execute(
        "SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?",
        (session["user_id"], post_id),
    ).fetchone()

    if existing_like:
        database.execute(
            "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
            (session["user_id"], post_id),
        )
    else:
        database.execute(
            "INSERT OR IGNORE INTO likes (user_id, post_id) VALUES (?, ?)",
            (session["user_id"], post_id),
        )
    database.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/post/<int:post_id>/comment", methods=("POST",))
@login_required
def comment(post_id):
    """Add a comment to a post."""
    content = request.form["content"].strip()
    if not content:
        flash("A comment cannot be empty.")
    elif len(content) > 300:
        flash("Comments must be 300 characters or fewer.")
    else:
        database = get_db()
        post_exists = database.execute(
            "SELECT 1 FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        if post_exists:
            database.execute(
                "INSERT INTO comments (user_id, post_id, content) VALUES (?, ?, ?)",
                (session["user_id"], post_id, content),
            )
            database.commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/profile/<username>")
def profile(username):
    """Show a user's profile and posts."""
    database = get_db()
    user = database.execute(
        "SELECT id, username FROM users WHERE username = ?", (username,)
    ).fetchone()
    if user is None:
        return "User not found", 404

    posts = database.execute(
        """
        SELECT posts.id, posts.content, posts.created_at,
               COUNT(likes.user_id) AS like_count
        FROM posts
        LEFT JOIN likes ON likes.post_id = posts.id
        WHERE posts.user_id = ?
        GROUP BY posts.id
        ORDER BY posts.created_at DESC, posts.id DESC
        """,
        (user["id"],),
    ).fetchall()
    return render_template("profile.html", user=user, posts=posts)


if __name__ == "__main__":
    app.run(debug=True)
