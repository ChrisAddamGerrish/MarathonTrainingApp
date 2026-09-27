"""Managing sign-in from the command line (the web app has no page for the first admin account).

    .venv\\Scripts\\python.exe -m backend.cli           set a user's password (a user that
                                                     doesn't exist yet is created, as an admin,
                                                     and asked for their plan's start date)
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
    plan_start = today - timedelta(days=today.weekday())
    with SessionLocal() as session:
        is_new = accounts.find_user(session, user) is None
    if is_new:  # an existing account keeps its plan; only a new one needs week 1's Monday
        plan_start = _ask_plan_start(plan_start)
        if plan_start is None:
            return 1
    created = accounts.set_password(user, password, plan_start=plan_start)
    print(f"{'Admin account created' if created else 'Password changed'} for {user}. "
          "Its existing sessions are signed out.")
    return 0


def _ask_plan_start(default: date) -> date | None:
    """Week 1's Monday for a new account. Match the old one when importing a plan backup: the plan
    stores week and weekday, and the dates follow from this."""
    answer = input(f"Plan start, the Monday of week 1 (YYYY-MM-DD) [{default.isoformat()}]: ").strip()
    try:
        start = date.fromisoformat(answer) if answer else default
    except ValueError:
        print("That isn't a date like 2026-09-14.", file=sys.stderr)
        return None
    if start.weekday() != 0:
        print(f"{start.isoformat()} is a {start:%A}; the plan starts on a Monday.", file=sys.stderr)
        return None
    return start


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
