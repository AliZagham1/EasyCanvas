"""Import this from any test module that exercises AI code with fake providers or mocked HTTP.

Production denies AI unless a saved, current consent record exists. Most existing tests build providers without a
store, so importing this module makes `consent_service.is_ai_enabled` return True for the whole test process.
`REAL_IS_AI_ENABLED` is the real function; test_ai_consent.py restores it to test enforcement.
"""
from backend.app import consent_service

REAL_IS_AI_ENABLED = getattr(consent_service, "_REAL_IS_AI_ENABLED", consent_service.is_ai_enabled)
consent_service._REAL_IS_AI_ENABLED = REAL_IS_AI_ENABLED
consent_service.is_ai_enabled = lambda store: True
