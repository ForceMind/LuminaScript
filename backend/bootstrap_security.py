import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = BASE_DIR / ".env"
INSECURE_SECRET_KEYS = {
    "",
    "your-secret-key-change-it-in-prod",
}


def _set_private_permissions(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)


def ensure_secret_key(env_file: Path = DEFAULT_ENV_FILE) -> bool:
    """Ensure .env contains a strong JWT signing key.

    Returns True when the configuration was changed. The key is never printed.
    """
    env_file = Path(env_file).resolve()
    env_file.parent.mkdir(parents=True, exist_ok=True)

    original_text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    lines = original_text.splitlines()
    current_secret = ""
    secret_line_indexes = []

    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "SECRET_KEY":
            current_secret = value.strip().strip("\"'")
            secret_line_indexes.append(index)

    secret_is_strong = (
        current_secret not in INSECURE_SECRET_KEYS
        and len(current_secret) >= 32
    )
    if secret_is_strong and len(secret_line_indexes) == 1:
        _set_private_permissions(env_file)
        return False

    chosen_secret = current_secret if secret_is_strong else secrets.token_urlsafe(64)
    new_line = f"SECRET_KEY={chosen_secret}"
    if not secret_line_indexes:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(new_line)
    else:
        first_index = secret_line_indexes[0]
        lines[first_index] = new_line
        for duplicate_index in reversed(secret_line_indexes[1:]):
            lines.pop(duplicate_index)

    updated_text = "\n".join(lines).rstrip() + "\n"
    temporary_file = env_file.parent / f"{env_file.name}.tmp"
    temporary_file.write_text(updated_text, encoding="utf-8")
    _set_private_permissions(temporary_file)
    os.replace(temporary_file, env_file)
    _set_private_permissions(env_file)
    return True


if __name__ == "__main__":
    changed = ensure_secret_key()
    if changed:
        print("Security configuration initialized or normalized.")
    else:
        print("Security configuration already contains a strong SECRET_KEY.")
