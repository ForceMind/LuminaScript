import getpass
import os
import sys

import upgrade_admin


def main() -> None:
    print("LuminaScript administrator provisioning")
    username = input("Administrator username [admin]: ").strip() or "admin"

    while True:
        password = getpass.getpass("New administrator password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            print("Passwords do not match. Try again.")
            continue
        if len(password) < 10:
            print("Password must contain at least 10 characters.")
            continue
        if len(password.encode("utf-8")) > 72:
            print("Password cannot exceed 72 UTF-8 bytes.")
            continue
        break

    os.environ["UPDATE_ADMIN"] = "true"
    os.environ["ADMIN_USER"] = username
    os.environ["ADMIN_PASS"] = password
    try:
        upgrade_admin.upgrade_schema()
    finally:
        os.environ.pop("ADMIN_PASS", None)

    print("Administrator credentials updated successfully.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
