"""Managing sign-in from the command line (the web app has no page for the first admin account).

    .venv\\Scripts\\python.exe -m backend.cli           set a user's password (a user that
                                                     doesn't exist yet is created, as an admin)
    .venv\\Scripts\\python.exe -m backend.cli invite    print a new invite code
    .venv\\Scripts\\python.exe -m backend.cli check     exit code 0 if anyone can sign in

start.ps1 runs the first on first start and with -ResetLogin. A new password signs out every
existing session of that user.
"""
import getpass
import sys
from datetime import date, timedelta

from backend.app.core.auth import MIN_PASSWORD_LENGTH, USERNAME_PATTERN, secret_key
from backend.app.core.database import SessionLocal, init_db
from backend.app.services import accounts


def _set_password() -> int:
    user = input("Username: ").strip()
    if not USERNAME_PATTERN.match(user):
        print("The username may only contain letters, digits, dot, dash and underscore.", file=sys.stderr)
        return 1
    password = getpass.getpass(f"Password ({MIN_PASSWORD_LENGTH}+ characters): ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Password again: ") != password:
        print("The passwords did not match.", file=sys.stderr)
        return 1
    today = date.today()
    created = accounts.set_password(user, password, plan_start=today - timedelta(days=today.weekday()))
    print(f"{'Admin account created' if created else 'Password changed'} for {user}. "
          "Its existing sessions are signed out.")
    return 0


def _invite() -> int:
    code, expires = accounts.create_invite(created_by=None)
    print(f"Invite code: {code}  (single use, expires {expires[:10]})")
    return 0


def _check() -> int:
    with SessionLocal() as session:
        return 0 if accounts.any_can_sign_in(session) else 1


def main(argv: list[str]) -> int:
    commands = {(): _set_password, ("invite",): _invite, ("check",): _check}
    command = commands.get(tuple(argv[1:]))
    if command is None:
        print(__doc__, file=sys.stderr)
        return 2
    secret_key()
    init_db()
    return command()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
