# NOVA

NOVA is a simple social platform MVP by **A.I. Industries**. It is built with Flask and SQLite so a beginner can read it, run it, and extend it.

## Features

- Register and log in with a username and password
- Share posts in a feed
- Like and unlike posts
- Comment on posts
- View a simple profile page

## Run it locally

1. Install Python 3.10 or newer.
2. Install the one dependency:

   ```bash
   pip install -r requirements.txt
   ```

3. Start the app:

   ```bash
   python app.py
   ```

4. Open http://127.0.0.1:5000 in your browser.

The SQLite database is created automatically at `instance/nova.db`. For a real deployment, set a strong `SECRET_KEY` environment variable and turn off debug mode.
