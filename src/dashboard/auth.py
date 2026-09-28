"""
auth.py — Dashboard Authentication & Authorization Middleware.

Enforces:
1. Local session token validation for state-changing endpoints.
2. Explicit human confirmation parameter check for destructive/state-changing operations.
3. Zero API keys/secrets exposure to frontend templates or responses.
"""

import os
import secrets
import logging
from functools import wraps
from flask import request, jsonify, current_app

logger = logging.getLogger(__name__)

# Active dashboard auth token (persistent across request lifecycle)
_AUTH_TOKEN = os.environ.get("ZENITH_DASHBOARD_SECRET") or secrets.token_hex(16)


def get_dashboard_token() -> str:
    """Return the active dashboard session token."""
    global _AUTH_TOKEN
    return _AUTH_TOKEN


def validate_token(token: str) -> bool:
    """Validate user provided token against server token."""
    if not token:
        return False
    return secrets.compare_digest(str(token).strip(), str(_AUTH_TOKEN).strip())


def require_dashboard_auth(require_confirmation: bool = False):
    """
    Decorator for Flask routes requiring authorization.
    
    Checks:
    1. Header 'X-Zenith-Token', 'Authorization' (Bearer), or query parameter 'token'.
    2. If require_confirmation=True, requires body {'confirmation': True}.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Extract token from headers or query
            token = (
                request.headers.get("X-Zenith-Token")
                or request.headers.get("Authorization", "").replace("Bearer ", "")
                or request.args.get("token")
            )

            if not validate_token(token):
                logger.warning(
                    f"[DASHBOARD AUTH] Unauthorized request to {request.path} "
                    f"from {request.remote_addr}"
                )
                return jsonify({
                    "success": False,
                    "error": "Unauthorized",
                    "message": "Valid dashboard authorization token required."
                }), 401

            # Check explicit confirmation on mutating actions
            if require_confirmation and request.method in ("POST", "PUT", "DELETE"):
                data = request.get_json(silent=True) or {}
                if not data.get("confirmation"):
                    return jsonify({
                        "success": False,
                        "error": "ConfirmationRequired",
                        "message": "This state-changing action requires explicit confirmation: true."
                    }), 400

            return fn(*args, **kwargs)
        return wrapper
    return decorator
