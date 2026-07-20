# Thin launcher — app code lives in groceryapp/, not here.
from groceryapp import app

if __name__ == "__main__":
    app.run(debug=True)
