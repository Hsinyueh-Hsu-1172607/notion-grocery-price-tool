# Deploying to PythonAnywhere

The point of hosting this is to scan a receipt from your phone without
turning a laptop on: a fixed HTTPS address, always up. HTTPS matters for
more than tidiness — browsers only grant a page camera access over HTTPS or
on localhost, so the in-page "capture" button works on the hosted site but
never over a LAN IP.

## What changes when it's hosted

PythonAnywhere runs Linux, so Apple's Vision framework isn't there. Without
a replacement the app falls back to Tesseract, which on a real receipt photo
read **none** of the prices — so the hosted app needs Google Cloud Vision,
set through `GOOGLE_VISION_API_KEY`. `requirements.txt` already marks the
`pyobjc-*` packages `sys_platform == "darwin"`, so pip skips them on Linux
rather than failing.

The free tier restricts outbound traffic to an allowlist. Both hosts this
app needs are on it: `api.notion.com` and `.googleapis.com`.

## 1. Get a Google Cloud Vision key

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and
   create a project.
2. Enable **Cloud Vision API** for it.
3. Under **APIs & Services → Credentials**, create an **API key**.
4. Restrict the key to the Cloud Vision API — an unrestricted key that leaks
   can be used for anything on the project.

Google's free allowance is 1,000 images a month, which is far more than a
few receipts a week, but the account still needs billing enabled and a card
on file. Set a budget alert if you want a hard warning.

## 2. Upload the code

On PythonAnywhere, open a **Bash console**:

```bash
git clone <your-repo-url> notion-grocery-price-tool
cd notion-grocery-price-tool
mkvirtualenv --python=/usr/bin/python3.10 grocery
pip install -r requirements.txt
```

If you'd rather not use git, upload a zip through the **Files** tab and
unzip it in the console instead.

## 3. Set the secrets

Never commit these. On PythonAnywhere put them in the WSGI file (below), or
create a `.env` beside `run.py` — `python-dotenv` loads it either way:

```
NOTION_API_KEY=...
NOTION_DATABASE_ID=...
GOOGLE_VISION_API_KEY=...
FLASK_SECRET_KEY=...
APP_PASSWORD_HASH=...
```

`NOTION_PARENT_PAGE_ID` is only needed by `setup_notion.py`, which you
already ran locally — the hosted app doesn't use it.

**`APP_PASSWORD_HASH` is not optional here.** A PythonAnywhere URL is public
and guessable, and without a password anyone who visits can read what you have
bought and write to your Notion database. Generate the hash locally:

```bash
python set_password.py
```

and paste the line it prints. `FLASK_SECRET_KEY` must be a real random string
on the server too — it signs the session cookie, so a guessable one lets
someone forge a signed-in session without the password.

## 4. Point the web app at the code

**Web** tab → **Add a new web app** → **Manual configuration** → Python 3.10.

Then set:

- **Source code**: `/home/<username>/notion-grocery-price-tool`
- **Virtualenv**: `/home/<username>/.virtualenvs/grocery`

Edit the **WSGI configuration file** it links to, replacing its contents:

```python
import os
import sys

path = "/home/<username>/notion-grocery-price-tool"
if path not in sys.path:
    sys.path.insert(0, path)

from dotenv import load_dotenv
load_dotenv(os.path.join(path, ".env"))

from groceryapp import app as application  # noqa: E402
```

Reload the web app. It's live at `https://<username>.pythonanywhere.com`.

## 5. Check the right engine is running

Scan a receipt and look at the result. If prices came through, Google Vision
answered. If item names appear but prices are missing, the key isn't being
read and it fell through to Tesseract — check the `.env` path in the WSGI
file and the PythonAnywhere error log.

## Scanning stays better on the Mac

Run locally when accuracy matters. macOS Vision is still first in the chain,
so `python run.py` on the Mac uses Apple's engine and never calls Google.
Both write to the same Notion database, so it makes no difference to the
data which one you use.
