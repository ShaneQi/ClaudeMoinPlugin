"""Test signing keys for Cloudflare Access JWTs."""

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from moinmoin_mcp.access import AccessConfig, clear_access_keys, set_access_keys

ISSUER = "https://test-team.cloudflareaccess.com"
AUDIENCE = "test-aud-tag"
CONFIG = AccessConfig(ISSUER, (AUDIENCE,))
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwks(key=KEY, kid="k1"):
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    return {"keys": [{**jwk, "kid": kid, "alg": "RS256", "use": "sig"}]}


def install_keys():
    clear_access_keys()
    set_access_keys(ISSUER, public_jwks())


def sign(key=KEY, kid="k1", alg="RS256", **claims):
    payload = {"iss": ISSUER, "aud": [AUDIENCE], "exp": int(time.time()) + 3600, **claims}
    payload = {name: value for name, value in payload.items() if value is not None}
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})
