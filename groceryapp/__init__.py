import os

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# Imported for its side effect of registering routes on `app`.
from groceryapp import routes  # noqa: E402,F401
