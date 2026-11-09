"""Workerではネイティブ署名を使い、従来のPython署名も保持する。"""

import asyncio
import base64
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import rsa
from pyasn1.codec.der import encoder
from pyasn1.type import univ
from pyasn1_modules import rfc5208

import google_auth


_NODE_SIGN = """
const { webcrypto } = require('node:crypto');
let text = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { text += chunk; });
process.stdin.on('end', async () => {
  const input = JSON.parse(text);
  const key = await webcrypto.subtle.importKey(
    input.format, Buffer.from(input.key, 'base64'), input.algorithm,
    input.extractable, input.usages,
  );
  const signature = await webcrypto.subtle.sign(
    input.signAlgorithm, key, Buffer.from(input.message, 'base64'),
  );
  process.stdout.write(Buffer.from(signature).toString('base64'));
});
"""


@dataclass(repr=False)
class SigningKey:
    public: rsa.PublicKey
    private: rsa.PrivateKey
    pkcs8: str


@pytest.fixture(scope="module")
def signing_key():
    """実運用の認証情報を使わず、テスト内だけの鍵を生成する。"""
    public, private = rsa.newkeys(1024)
    info = rfc5208.PrivateKeyInfo()
    info["version"] = 0
    info["privateKeyAlgorithm"]["algorithm"] = univ.ObjectIdentifier("1.2.840.113549.1.1.1")
    info["privateKeyAlgorithm"]["parameters"] = univ.Null("")
    info["privateKey"] = private.save_pkcs1(format="DER")
    encoded = base64.encodebytes(encoder.encode(info)).decode("ascii")
    pkcs8 = "-----BEGIN PRIVATE KEY-----\n" + encoded + "-----END PRIVATE KEY-----\n"
    return SigningKey(public, private, pkcs8)


class NativeBytes:
    def __init__(self, value):
        self.data = bytearray(value)

    def __setitem__(self, index, value):
        self.data[index] = value

    def to_py(self):
        return list(self.data)


class NativeArray(list):
    def push(self, value):
        self.append(value)


class NativeCrypto:
    """JS境界を検査し、署名そのものはNodeのWebCryptoへ渡す。"""

    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.js = SimpleNamespace(
            crypto=SimpleNamespace(subtle=self),
            JSON=SimpleNamespace(parse=lambda text: SimpleNamespace(value=json.loads(text))),
            Array=SimpleNamespace(new=NativeArray),
            Uint8Array=SimpleNamespace(new=NativeBytes),
        )

    async def importKey(self, key_format, key_bytes, algorithm, extractable, usages):  # noqa: N802
        self.calls.append("importKey")
        # Python dictをそのまま渡す経路は、ネイティブ署名の成功とみなさない。
        assert type(algorithm) is SimpleNamespace
        assert algorithm.value == {"name": "RSASSA-PKCS1-v1_5", "hash": {"name": "SHA-256"}}
        assert key_format == "pkcs8" and extractable is False
        assert type(usages) is NativeArray and usages == ["sign"]
        assert type(key_bytes) is NativeBytes
        if self.failure == "import":
            raise RuntimeError("test import failure")
        return {"format": key_format, "key": base64.b64encode(key_bytes.data).decode(),
                "algorithm": algorithm.value, "extractable": extractable, "usages": usages}

    async def sign(self, algorithm, key, message):
        self.calls.append("sign")
        assert algorithm == "RSASSA-PKCS1-v1_5" and type(message) is NativeBytes
        if self.failure == "sign":
            raise RuntimeError("test sign failure")
        node = shutil.which("node")
        if node is None:
            pytest.skip("ネイティブWebCryptoの署名検証にはNode.jsが必要")
        result = subprocess.run(
            [node, "-e", _NODE_SIGN], check=True, capture_output=True, text=True, timeout=10,
            input=json.dumps({**key, "signAlgorithm": algorithm,
                              "message": base64.b64encode(message.data).decode()}),
        )
        return base64.b64decode(result.stdout)


def assertion(monkeypatch, signing_key, *, pkcs1=False):
    public, private, pkcs8 = signing_key.public, signing_key.private, signing_key.pkcs8
    monkeypatch.setattr(google_auth.time, "time", lambda: 1000)
    monkeypatch.setattr(google_auth, "uuid4", lambda: "test-jti")
    token = asyncio.run(google_auth._build_service_account_assertion({
        "client_email": "service-account@example.test", "private_key_id": "test-key-id",
        "private_key": private.save_pkcs1().decode() if pkcs1 else pkcs8,
    }, google_auth._GOOGLE_CALENDAR_SCOPES))
    assert token is not None
    header, payload, signature = token.split(".")

    def decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    assert json.loads(decode(header)) == {"alg": "RS256", "typ": "JWT", "kid": "test-key-id"}
    assert json.loads(decode(payload)) == {
        "iss": "service-account@example.test", "scope": google_auth._GOOGLE_CALENDAR_SCOPES,
        "aud": "https://oauth2.googleapis.com/token", "iat": 1000, "exp": 4600, "jti": "test-jti",
    }
    assert rsa.verify(f"{header}.{payload}".encode(), decode(signature), public) == "SHA-256"


def python_imports(monkeypatch):
    calls = []
    original = google_auth.importlib.import_module

    def track(name):
        calls.append(name)
        return original(name)

    monkeypatch.setattr(google_auth.importlib, "import_module", track)
    return calls


def test_worker_uses_real_webcrypto_before_python_signers(monkeypatch, signing_key):
    native = NativeCrypto()
    monkeypatch.setitem(sys.modules, "js", native.js)
    imports = python_imports(monkeypatch)

    assertion(monkeypatch, signing_key)

    assert native.calls == ["importKey", "sign"]
    assert imports == []


@pytest.mark.parametrize("failure", ["import", "sign"])
def test_native_failure_preserves_google_auth_fallback(monkeypatch, signing_key, failure):
    native = NativeCrypto(failure)
    monkeypatch.setitem(sys.modules, "js", native.js)
    imports = python_imports(monkeypatch)

    assertion(monkeypatch, signing_key)

    assert native.calls == (["importKey"] if failure == "import" else ["importKey", "sign"])
    assert "google.auth.crypt" in imports and "google.auth.jwt" in imports


def test_cpython_keeps_existing_google_auth_signer(monkeypatch, signing_key):
    monkeypatch.setitem(sys.modules, "js", None)
    imports = python_imports(monkeypatch)

    assertion(monkeypatch, signing_key)

    assert "google.auth.crypt" in imports and "google.auth.jwt" in imports


def test_missing_subtle_sign_keeps_existing_google_auth_signer(monkeypatch, signing_key):
    monkeypatch.setitem(sys.modules, "js", SimpleNamespace(crypto=SimpleNamespace(subtle=SimpleNamespace())))
    imports = python_imports(monkeypatch)

    assertion(monkeypatch, signing_key)

    assert "google.auth.crypt" in imports and "google.auth.jwt" in imports


def test_throwing_js_proxy_keeps_existing_google_auth_signer(monkeypatch, signing_key):
    class UnavailableProxy:
        @property
        def crypto(self):
            raise RuntimeError("test proxy getter failure")

    monkeypatch.setitem(sys.modules, "js", UnavailableProxy())
    imports = python_imports(monkeypatch)

    assertion(monkeypatch, signing_key)

    assert "google.auth.crypt" in imports and "google.auth.jwt" in imports


def test_pkcs1_key_keeps_existing_google_auth_signer(monkeypatch, signing_key):
    native = NativeCrypto()
    monkeypatch.setitem(sys.modules, "js", native.js)
    imports = python_imports(monkeypatch)

    assertion(monkeypatch, signing_key, pkcs1=True)

    assert native.calls == []
    assert "google.auth.crypt" in imports and "google.auth.jwt" in imports
