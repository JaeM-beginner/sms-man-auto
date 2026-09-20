"""Strict, non-persistent SMS-Man API adapter. Never reflect remote error text."""
from __future__ import annotations
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

API_BASE_URL = "https://api.sms-man.com/control"
MAX_BODY = 1024 * 1024
ERROR_LABELS = {
    "no_free_phones": "사용 가능한 번호가 없습니다.",
    "not_enough_balance": "SMS-Man 잔액이 부족합니다.",
    "wrong_token": "API 토큰을 확인하세요.", "bad_token": "API 토큰을 확인하세요.",
    "wrong_application_id": "서비스 ID를 확인하세요.", "wrong_country_id": "국가 ID를 확인하세요.",
    "wrong_status": "번호 상태를 변경할 수 없습니다. 계정에서 확인하세요.",
}
WAITING_SMS_CODES = {"wait_sms"}


class ApiError(RuntimeError):
    def __init__(self, message, *, retryable=False, uncertain=False):
        super().__init__(message)
        self.retryable, self.uncertain = retryable, uncertain


@dataclass(frozen=True, repr=False)
class Activation:
    request_id: str
    phone_number: str


def positive_id(value):
    value = str(value).strip()
    if not re.fullmatch(r"[0-9]{1,12}", value) or int(value) == 0:
        raise ValueError("국가·서비스·요청 ID는 0보다 큰 정수여야 합니다.")
    return str(int(value))


def price_cap(value, currency):
    if currency not in {"RUB", "USD", "EUR"}:
        raise ValueError("통화는 RUB, USD, EUR 중 선택하세요.")
    if not re.fullmatch(r"[0-9]{1,9}", str(value).strip()):
        raise ValueError("단가 상한은 양의 정수로 입력하세요. 소수는 지원하지 않습니다.")
    amount = Decimal(str(value).strip())
    if amount <= 0:
        raise ValueError("단가 상한은 0보다 커야 합니다.")
    return str(int(amount))


def api_error(payload):
    code = payload.get("error_code") or payload.get("error")
    code = code.lower() if isinstance(code, str) else ""
    retryable = code == "no_free_phones" or (not code and payload.get("error_msg") == "No Numbers, try again.")
    return ApiError(ERROR_LABELS.get("no_free_phones" if retryable else code,
                    "SMS-Man 요청을 처리하지 못했습니다. 계정에서 확인하세요."),
                    retryable=retryable, uncertain=code not in ERROR_LABELS and not retryable)


def object_payload(payload):
    if not isinstance(payload, dict):
        raise ApiError("API 응답 형식이 잘못되었습니다.", uncertain=True)
    if payload.get("success") is False or payload.get("error_code") or payload.get("error"):
        raise api_error(payload)
    return payload


def parse_get_number_response(payload):
    payload = object_payload(payload)
    try:
        rid = positive_id(payload.get("request_id"))
    except ValueError:
        raise ApiError("발급 응답이 불완전합니다. 계정에서 번호를 확인하세요.", uncertain=True) from None
    number = payload.get("number")
    if not isinstance(number, str) or not re.fullmatch(r"\+?[0-9]{5,20}", number):
        raise ApiError("발급 응답이 불완전합니다. 계정에서 번호를 확인하세요.", uncertain=True)
    return Activation(rid, number)


def parse_limits_response(payload, application_id, country_id):
    if isinstance(payload, dict):
        object_payload(payload)
    if not isinstance(payload, list):
        raise ApiError("재고 응답 형식이 잘못되었습니다.")
    matches = []
    for row in payload:
        if not isinstance(row, dict):
            raise ApiError("재고 응답 형식이 잘못되었습니다.")
        try:
            aid, cid = positive_id(row.get("application_id")), positive_id(row.get("country_id"))
            count = str(row["numbers"])
            if not re.fullmatch(r"[0-9]{1,12}", count):
                raise ValueError
        except (ValueError, KeyError):
            raise ApiError("재고 응답 형식이 잘못되었습니다.") from None
        if (aid, cid) == (application_id, country_id):
            matches.append(int(count))
    if len(matches) > 1:
        raise ApiError("중복 재고 응답입니다. 재고를 확정할 수 없습니다.")
    return matches[0] if matches else 0


def parse_get_sms_response(payload):
    if isinstance(payload, dict):
        error = payload.get("error_code", payload.get("error"))
        if isinstance(error, str) and error in WAITING_SMS_CODES:
            return None
    payload = object_payload(payload)
    direct = payload.get("sms_code")
    if isinstance(direct, (str, int)) and not isinstance(direct, bool) and re.fullmatch(r"[0-9]{4,10}", str(direct)):
        return str(direct)
    raise ApiError("SMS 응답에 유효한 코드 또는 대기 상태가 없습니다.")


def strip_country_code(phone_number, country_code):
    number, prefix = re.sub(r"\D", "", phone_number), re.sub(r"\D", "", country_code)
    if not prefix or not number.startswith(prefix) or len(number) == len(prefix):
        raise ApiError("번호와 국가 전화 코드를 확인하세요. 예: +82")
    return number[len(prefix):]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpSession:
    def get(self, url, *, params, timeout):
        if url not in {f"{API_BASE_URL}/{x}" for x in ("get-number", "get-sms", "set-status", "limits")}:
            raise ValueError("허용되지 않은 API 주소입니다.")
        request = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), method="GET")
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                raw = response.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    raise ApiError("API 응답 크기 제한을 초과했습니다.", uncertain=True)
                return HttpResponse(response.status, raw.decode("utf-8"))
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            return HttpResponse(code, "")
        except (urllib.error.URLError, OSError, UnicodeError, http.client.HTTPException):
            raise ApiError("통신에 실패했습니다. 요청 결과를 계정에서 확인하세요.", uncertain=True) from None


@dataclass(frozen=True, repr=False)
class HttpResponse:
    status_code: int
    body: str

    def json(self):
        try:
            if len(self.body.encode("utf-8")) > MAX_BODY:
                raise ValueError
            payload = json.loads(self.body)
            if not isinstance(payload, (dict, list)):
                raise ValueError
            return payload
        except (ValueError, RecursionError, UnicodeError):
            raise ApiError("API JSON 응답 형식이 잘못되었습니다.", uncertain=True) from None


class SmsManClient:
    def __init__(self, token, *, session=None, base_url=API_BASE_URL, timeout=15.0):
        if not isinstance(token, str) or not token.strip():
            raise ValueError("API 토큰을 입력하세요.")
        if base_url.rstrip("/") != API_BASE_URL:
            raise ValueError("허용되지 않은 API 주소입니다.")
        self.token, self.session, self.timeout = token.strip(), session or HttpSession(), timeout

    def _get(self, action, **params):
        try:
            response = self.session.get(f"{API_BASE_URL}/{action}", params={"token": self.token, **params}, timeout=self.timeout)
            if response.status_code != 200:
                if response.status_code == 429:
                    raise ApiError("API 요청 제한입니다. 잠시 후 계정 상태를 확인하세요.", uncertain=True)
                raise ApiError("API HTTP 오류입니다. 계정에서 요청 결과를 확인하세요.", uncertain=True)
            return response.json()
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            raise ApiError("통신에 실패했습니다. 계정에서 요청 결과를 확인하세요.", uncertain=True) from None

    def get_number(self, *, application_id, country_id, max_price, currency):
        aid, cid, cap = positive_id(application_id), positive_id(country_id), price_cap(max_price, currency)
        return parse_get_number_response(self._get("get-number", application_id=aid, country_id=cid, maxPrice=cap, currency=currency))

    def get_limits(self, *, application_id, country_id):
        aid, cid = positive_id(application_id), positive_id(country_id)
        return parse_limits_response(self._get("limits", application_id=aid, country_id=cid), aid, cid)

    def get_sms(self, request_id):
        return parse_get_sms_response(self._get("get-sms", request_id=positive_id(request_id)))

    def set_status(self, request_id, status):
        if status != "reject":
            raise ValueError("이 앱은 미사용 번호 거절만 지원합니다.")
        payload = object_payload(self._get("set-status", request_id=positive_id(request_id), status=status))
        if payload.get("success") is not True:
            raise ApiError("번호 취소 결과를 확인할 수 없습니다.", uncertain=True)
