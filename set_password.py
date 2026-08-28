"""Print the APP_PASSWORD_HASH line to paste into .env.

Run it, type the password twice, copy the one line it prints. The password
itself is never stored or echoed — getpass keeps it off the screen, and only
the hash goes into .env, so a leaked .env does not hand over the password.

    python set_password.py
"""
import getpass
import sys

from werkzeug.security import generate_password_hash


def main():
    password = getpass.getpass("New password: ")
    if len(password) < 8:
        sys.exit("Too short — use at least 8 characters.")
    if password != getpass.getpass("Again: "):
        sys.exit("They don't match. Nothing written.")

    print("\nAdd this line to .env (replacing any existing one):\n")
    print(f"APP_PASSWORD_HASH={generate_password_hash(password)}")
    print("\n.env is gitignored, so this stays off GitHub.")


if __name__ == "__main__":
    main()
