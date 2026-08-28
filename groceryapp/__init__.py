import os

from dotenv import load_dotenv
from flask import Flask

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

# Imported for their side effect of registering routes on `app`. Auth first,
# so its before_request hook runs ahead of anything a route might do.
from groceryapp import auth  # noqa: E402,F401
from groceryapp import routes  # noqa: E402,F401
