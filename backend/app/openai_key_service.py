"""The OpenAI key EasyCanvas uses: one in-app key, else the .env key.

Rules: the key is stored only in the local database file (mode 0600), is never returned by any API, never logged,
and never placed in an error message. Usage is tagged with a one-way fingerprint and the key's last 4 characters.
"""

import hashlib
import os
import re

MIN_KEY_LENGTH, MAX_KEY_LENGTH = 20, 400
KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9_\-]+$")


class KeyRejected(ValueError):
    """The key is malformed or OpenAI refused it. The message is safe to show and never contains the key."""


class KeyUnverifiable(RuntimeError):
    """OpenAI could not be reached or answered unexpectedly, so the key was not saved."""


def fingerprint(api_key):
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def hint(api_key):
    return api_key[-4:]


def resolve_api_key(store=None):
    """Return (key, source). A saved in-app key wins; the .env key is the fallback.

    Without a store (tests, tools) only the environment is consulted, so a developer's saved key can never
    change the behavior of code that did not ask for it.
    """
    if store is not None:
        try:
            saved = store.load_api_key()
        except Exception:  # noqa: BLE001 - an unreadable database must not break the .env fallback.
            saved = None
        if isinstance(saved, str) and saved.strip():
            return saved.strip(), "saved"
    env = (os.getenv("OPENAI_API_KEY") or "").strip()
    return (env, "environment") if env else (None, None)


def active_identity(store=None):
    """Non-secret identity of the key in use, or None."""
    key, source = resolve_api_key(store)
    if not key:
        return None
    return {"fingerprint": fingerprint(key), "hint": hint(key), "source": source}


def validate_format(value):
    """Reject obviously wrong input before any network call. Messages never echo the input."""
    if not isinstance(value, str):
        raise KeyRejected("Enter your OpenAI API key.")
    key = value.strip()
    if not key:
        raise KeyRejected("Enter your OpenAI API key.")
    if len(key) < MIN_KEY_LENGTH or len(key) > MAX_KEY_LENGTH or not KEY_PATTERN.match(key):
        raise KeyRejected("That does not look like an OpenAI API key (it should start with \"sk-\").")
    return key


def verify_key(api_key, client_factory=None):
    """Ask OpenAI to list models: free, and proves the key is accepted. Raises KeyRejected or KeyUnverifiable."""
    from openai import AuthenticationError, OpenAI, OpenAIError, PermissionDeniedError
    factory = client_factory or (lambda key: OpenAI(api_key=key, timeout=20, max_retries=0))
    try:
        client = factory(api_key)
        try:
            next(iter(client.models.list()), None)
        finally:
            close = getattr(client, "close", None)
            if close:
                close()
    except AuthenticationError:
        raise KeyRejected("OpenAI rejected this key. Check it and try again.") from None
    except PermissionDeniedError:
        # Restricted keys may lack permission to list models yet still work for the calls EasyCanvas makes.
        raise KeyUnverifiable("OpenAI would not let this key list models, so it could not be verified. It was not saved.") from None
    except OpenAIError:
        raise KeyUnverifiable("Could not verify the key with OpenAI right now. It was not saved. Check your connection and retry.") from None


def key_status(store):
    """What Settings may show: whether a key is in use, where it came from, and its last 4 characters."""
    identity = active_identity(store)
    if not identity:
        return {"configured": False, "source": None, "hint": None}
    return {"configured": True, "source": identity["source"], "hint": identity["hint"]}


def attribute_existing_usage(store):
    """Give pre-tracking usage records to the key active right now, once, before that key can change."""
    identity = active_identity(store)
    if identity:
        store.tag_untagged_api_usage(identity["fingerprint"], identity["hint"])


def save_key(store, value, client_factory=None):
    key = validate_format(value)
    verify_key(key, client_factory)
    attribute_existing_usage(store)  # the previous key keeps what it spent
    store.save_api_key(key)
    return key_status(store)


def remove_key(store):
    attribute_existing_usage(store)
    store.delete_api_key()
    return key_status(store)
