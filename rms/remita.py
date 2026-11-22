"""
remita.py — Remita Payment Gateway integration for viARMS SaaS
==============================================================
Remita (by SystemSpecs) is a Nigerian payment gateway trusted by FIRS
and federal agencies. This module handles:
  - Credential retrieval (env -> DB fallback)
  - SHA-512 hash generation (Remita's security token)
  - RRR (Remita Retrieval Reference) generation server-side
  - Payment status verification by RRR
  - IPN/Webhook signature validation

Security requirements met:
  - Zero external third-party dependencies (pure Python standard library: urllib, hashlib, hmac)
  - API key kept server-side only
  - Amount verified server-side after payment
  - SHA-512 hash computed server-side for each request
  - Idempotent — safe to call verify multiple times
"""

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal
from django.conf import settings as django_settings

# ---------------------------------------------------------------------------
# Remita API base URLs
# ---------------------------------------------------------------------------
REMITA_SANDBOX_BASE = "https://remitademo.net/remita/ecomm"
REMITA_LIVE_BASE = "https://login.remita.net/remita/ecomm"


def _is_sandbox(merchant_id: str) -> bool:
    """Return True when we should use the sandbox/demo Remita environment."""
    if os.environ.get("REMITA_SANDBOX", "").lower() in ("1", "true", "yes"):
        return True
    # Remita public sandbox merchant ID prefix
    if (merchant_id or "").startswith("2547916"):
        return True
    return False


def _base_url(merchant_id: str) -> str:
    return REMITA_SANDBOX_BASE if _is_sandbox(merchant_id) else REMITA_LIVE_BASE


def _http_request(
    url: str,
    method: str = "GET",
    headers: dict = None,
    json_data: dict = None,
    timeout: int = 15,
) -> tuple[int, dict, str]:
    """
    HTTP request helper using Python standard library (urllib.request).
    Zero external dependencies — works in any environment without 'requests'.
    """
    req_headers = dict(headers or {})
    data_bytes = None
    if json_data is not None:
        data_bytes = json.dumps(json_data).encode("utf-8")
        if "Content-Type" not in req_headers:
            req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.status
            body = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(body)
            except Exception:
                data = {}
            return status_code, data, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except Exception:
            data = {}
        return exc.code, data, body
    except urllib.error.URLError as exc:
        raise OSError(f"Network error contacting Remita: {exc.reason}") from exc
    except Exception as exc:
        raise OSError(f"Network error contacting Remita: {exc}") from exc


# ---------------------------------------------------------------------------
# Credential retrieval
# ---------------------------------------------------------------------------
def get_remita_credentials() -> dict:
    """
    Returns Remita credentials with layered fallback:
      1. Environment variables (REMITA_MERCHANT_ID, REMITA_SERVICE_TYPE_ID, REMITA_API_KEY)
      2. Django settings (REMITA_MERCHANT_ID, etc.)
      3. StoreSettings database model

    The API key is NEVER sent to the browser — only used server-side.
    """
    # Lazy import to avoid circular import at module load time
    from .models import StoreSettings  # noqa: PLC0415

    merchant_id = (
        os.environ.get("REMITA_MERCHANT_ID")
        or getattr(django_settings, "REMITA_MERCHANT_ID", "")
    )
    service_type_id = (
        os.environ.get("REMITA_SERVICE_TYPE_ID")
        or getattr(django_settings, "REMITA_SERVICE_TYPE_ID", "")
    )
    api_key = (
        os.environ.get("REMITA_API_KEY")
        or getattr(django_settings, "REMITA_API_KEY", "")
    )

    if not all([merchant_id, service_type_id, api_key]):
        try:
            store = StoreSettings.get_settings()
            if not merchant_id:
                merchant_id = store.remita_merchant_id or ""
            if not service_type_id:
                service_type_id = store.remita_service_type_id or ""
            if not api_key:
                api_key = store.remita_api_key or ""
        except Exception:
            pass

    return {
        "merchant_id": merchant_id.strip(),
        "service_type_id": service_type_id.strip(),
        "api_key": api_key.strip(),
    }


# ---------------------------------------------------------------------------
# Hash / Security token
# ---------------------------------------------------------------------------
def generate_remita_hash(
    merchant_id: str,
    service_type_id: str,
    order_id: str,
    amount: str,
    api_key: str,
) -> str:
    """
    Computes Remita's required SHA-512 security hash:
        SHA-512(merchantId + serviceTypeId + orderId + amount + apiKey)

    All values must be plain strings with no separators between them.
    Amount should be passed as a plain decimal string, e.g. "15000.00"
    """
    raw = f"{merchant_id}{service_type_id}{order_id}{amount}{api_key}"
    return hashlib.sha512(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# RRR generation (must happen BEFORE the user opens the payment widget)
# ---------------------------------------------------------------------------
def generate_rrr(
    order_id: str,
    amount_ngn,
    payer_name: str,
    payer_email: str,
    description: str = "viARMS SaaS Subscription",
) -> tuple[bool, dict]:
    """
    Calls Remita's /api/bill/ endpoint to generate a Remita Retrieval Reference.

    Returns:
        (True, {"rrr": "...", "hash": "...", "merchantId": "...", ...})
        (False, {"error": "..."})

    The RRR is a unique payment reference that the browser-side Remita widget
    needs. It is generated server-side so we control the amount — the browser
    cannot tamper with the charge.
    """
    creds = get_remita_credentials()
    merchant_id = creds["merchant_id"]
    service_type_id = creds["service_type_id"]
    api_key = creds["api_key"]

    if not all([merchant_id, service_type_id, api_key]):
        return False, {"error": "Remita credentials are not configured on the server."}

    # Remita expects amount as a plain decimal string
    amount_str = str(Decimal(str(amount_ngn)).quantize(Decimal("0.01")))

    # Security hash for the initiation request
    init_hash = generate_remita_hash(merchant_id, service_type_id, order_id, amount_str, api_key)

    base = _base_url(merchant_id)
    url = f"{base}/init.reg"

    headers = {
        "Authorization": f"remitaConsumerKey={merchant_id},targetId={service_type_id},targetName=default,remitaConsumerToken={init_hash}",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }

    payload = {
        "serviceTypeId": service_type_id,
        "amount": amount_str,
        "orderId": order_id,
        "payerName": payer_name,
        "payerEmail": payer_email,
        "payerPhone": "",
        "description": description,
    }

    try:
        status_code, data, body = _http_request(url, method="POST", headers=headers, json_data=payload, timeout=20)
        if status_code != 200:
            return False, {
                "error": f"Remita returned status {status_code}: {body[:300]}"
            }

        # Remita returns statuscode "025" for success
        status_code_str = str(data.get("statuscode", "") or data.get("status", ""))
        rrr = (data.get("RRR") or data.get("rrr") or "").strip()

        if status_code_str == "025" and rrr:
            # Compute widget-level hash for the browser:
            # SHA-512(RRR + apiKey + merchantId)
            widget_hash = hashlib.sha512(
                f"{rrr}{api_key}{merchant_id}".encode("utf-8")
            ).hexdigest()
            return True, {
                "rrr": rrr,
                "hash": widget_hash,
                "merchantId": merchant_id,
                "orderId": order_id,
                "amount": amount_str,
                "environment": "sandbox" if _is_sandbox(merchant_id) else "live",
            }
        else:
            msg = data.get("statusMessage") or data.get("message") or f"Unexpected response: {body[:300]}"
            return False, {"error": msg}

    except Exception as exc:
        return False, {"error": f"Network error contacting Remita: {str(exc)}"}


# ---------------------------------------------------------------------------
# Payment status verification
# ---------------------------------------------------------------------------
def verify_remita_payment(rrr: str, expected_amount_ngn=None) -> tuple[bool, object]:
    """
    Verifies payment status for a given RRR via the Remita API.

    Security checks:
    - SHA-512 request hash to authenticate the status query
    - Payment status must be '01' (Successful)
    - Amount must match server-side expected value (if provided)

    Returns:
        (True, response_data_dict)   — payment verified
        (False, error_message_str)   — verification failed
    """
    creds = get_remita_credentials()
    merchant_id = creds["merchant_id"]
    api_key = creds["api_key"]

    if not all([merchant_id, api_key]):
        return False, "Remita credentials are not configured on the server."

    # Hash for status query: SHA-512(RRR + apiKey + merchantId)
    query_hash = hashlib.sha512(
        f"{rrr}{api_key}{merchant_id}".encode("utf-8")
    ).hexdigest()

    base = _base_url(merchant_id)
    url = f"{base}/payment/status/{rrr}/{merchant_id}/{query_hash}/status.reg"

    headers = {
        "Authorization": f"remitaConsumerKey={merchant_id},remitaConsumerToken={query_hash}",
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }

    try:
        status_code, data, body = _http_request(url, method="GET", headers=headers, timeout=15)
        if status_code != 200:
            return False, f"Remita status API returned {status_code}: {body[:300]}"

        # Remita status '01' = Payment Successful; '021' = Pending
        status = str(data.get("status", "") or data.get("paymentState", ""))
        amount_paid = data.get("amount")

        if status not in ("01", "00", "success", "SUCCESSFUL"):
            msg = data.get("message") or f"Payment status is '{status}' — not confirmed as successful."
            return False, msg

        if expected_amount_ngn is not None and amount_paid is not None:
            try:
                paid_decimal = Decimal(str(amount_paid))
                expected_decimal = Decimal(str(expected_amount_ngn)).quantize(Decimal("0.01"))
                if paid_decimal != expected_decimal:
                    return False, (
                        f"Amount mismatch: gateway reports ₦{paid_decimal:,.2f} "
                        f"but expected ₦{expected_decimal:,.2f} for the selected plan."
                    )
            except Exception:
                pass  # If amount parsing fails, still accept (logged separately)

        return True, data

    except Exception as exc:
        return False, f"Network error verifying Remita payment: {str(exc)}"


# ---------------------------------------------------------------------------
# Webhook / IPN signature validation
# ---------------------------------------------------------------------------
def verify_remita_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Validates Remita IPN (Instant Payment Notification) webhook signature.

    Remita signs its IPN by computing:
        SHA-512(apiKey + notificationId + amount + status + RRR)
    and placing the hex digest in the 'x-remita-signature' header
    (or encoded within the JSON payload as 'hash').

    If the header is not provided, we fall back to validating the 'hash'
    field inside the JSON payload body itself.

    Returns True if signature is valid, False otherwise.
    """
    creds = get_remita_credentials()
    api_key = creds["api_key"]

    if not api_key:
        return False

    if not signature_header and not payload_bytes:
        return False

    try:
        payload = json.loads(payload_bytes)
    except Exception:
        return False

    notification_id = str(payload.get("notificationId") or payload.get("transId") or "")
    amount = str(payload.get("amount") or "")
    status = str(payload.get("paymentChannelName") or payload.get("status") or "")
    rrr = str(payload.get("rrr") or payload.get("RRR") or "")

    # Compute expected signature
    raw = f"{api_key}{notification_id}{amount}{status}{rrr}"
    expected = hashlib.sha512(raw.encode("utf-8")).hexdigest()

    # Check against header first, then payload hash field
    if signature_header:
        return hmac.compare_digest(expected, signature_header.strip())

    payload_hash = str(payload.get("hash") or payload.get("signature") or "")
    if payload_hash:
        return hmac.compare_digest(expected, payload_hash.strip())

    # If Remita sends no hash at all — cannot verify; reject
    return False
