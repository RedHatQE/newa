"""HTTP request utilities."""

import time
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, overload

import requests
import urllib3.response
from requests_kerberos import HTTPKerberosAuth

if TYPE_CHECKING:
    from typing import TypeAlias

    JSON: TypeAlias = Any


HTTP_STATUS_CODES_OK = [200, 201]


class ResponseContentType(Enum):
    TEXT = 'text'
    JSON = 'json'
    RAW = 'raw'
    BINARY = 'binary'


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.TEXT]) -> str:
    pass


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.BINARY]) -> bytes:
    pass


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.JSON]) -> 'JSON':
    pass


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.RAW]) -> urllib3.response.HTTPResponse:
    pass


def get_request(
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: ResponseContentType = ResponseContentType.TEXT) -> Any:
    """Generic GET request, optionally using Kerberos authentication."""
    while attempts:
        try:
            r = requests.get(
                url,
                auth=HTTPKerberosAuth(delegate=True),
                ) if krb else requests.get(url)
            if r.status_code in HTTP_STATUS_CODES_OK:
                response = getattr(r, response_content.value)
                if callable(response):
                    return response()
                return response
        except requests.exceptions.RequestException:
            # will give it another try
            pass
        time.sleep(delay)
        attempts -= 1

    raise Exception(f"GET request to {url} failed")


@overload
def post_request(
        *,
        url: str,
        json: 'JSON',
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.RAW]) -> urllib3.response.HTTPResponse:
    pass


@overload
def post_request(
        *,
        url: str,
        json: 'JSON',
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.JSON]) -> 'JSON':
    pass


def post_request(
        url: str,
        json: 'JSON',
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: ResponseContentType = ResponseContentType.TEXT) -> Any:
    """Generic POST request, optionally using Kerberos authentication."""
    while attempts:
        try:
            r = requests.post(
                url,
                json=json,
                auth=HTTPKerberosAuth(delegate=True),
                ) if krb else requests.post(url, json=json)
            if r.status_code in HTTP_STATUS_CODES_OK:
                response = getattr(r, response_content.value)
                if callable(response):
                    return response()
                return response
        except requests.exceptions.RequestException:
            # will give it another try
            pass
        time.sleep(delay)
        attempts -= 1

    raise Exception(f"POST request to {url} failed")
