"""Fail-closed, database-backed global daily budget for Biquote requests."""
import os
import requests

DEFAULT_DAILY_LIMIT = 100


class BiquoteQuotaError(RuntimeError):
    pass


def _settings():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")
    try:
        limit = int(os.environ.get("BIQUOTE_DAILY_REQUEST_LIMIT", str(DEFAULT_DAILY_LIMIT)))
    except ValueError as exc:
        raise BiquoteQuotaError("Configuración del límite Biquote no válida.") from exc
    if not url or not key or limit < 1:
        raise BiquoteQuotaError("No se puede verificar el límite diario Biquote; consulta bloqueada.")
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = "Bearer " + key
    return url, headers, limit


def _call_rpc(function_name, reserve):
    url, headers, limit = _settings()
    try:
        response = requests.post(
            f"{url}/rest/v1/rpc/{function_name}",
            headers=headers,
            json={"p_daily_limit": limit},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise BiquoteQuotaError(
            "No se pudo confirmar el contador diario Biquote; consulta bloqueada por seguridad."
        ) from exc
    required = {"allowed", "used", "daily_limit", "remaining", "reset_at_utc"}
    if not isinstance(data, dict) or not required.issubset(data):
        raise BiquoteQuotaError("Respuesta del contador Biquote no válida; consulta bloqueada.")
    if reserve and data["allowed"] is not True:
        raise BiquoteQuotaError(
            f"Límite diario Biquote alcanzado ({data['used']}/{data['daily_limit']})."
        )
    return data


def reserve_biquote_request():
    """Atomically reserve one unit before every outbound Biquote HTTP attempt."""
    return _call_rpc("consume_biquote_request", True)


def get_biquote_usage():
    """Read today's UTC usage without consuming a provider request."""
    return _call_rpc("get_biquote_usage", False)
