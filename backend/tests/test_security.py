import time

import jwt as pyjwt
import pytest

from app.core.security import (
    create_pairing_token,
    create_registration_token,
    create_token,
    decode_token,
    generate_otp,
    hash_otp,
    hash_pin,
    verify_pin,
)

BID = "11111111-1111-1111-1111-111111111111"
SID = "22222222-2222-2222-2222-222222222222"


def test_pin_roundtrip():
    stored = hash_pin("1234")
    assert verify_pin("1234", stored)
    assert not verify_pin("1235", stored)
    assert not verify_pin("", stored)


def test_pin_hashes_are_salted():
    assert hash_pin("1234") != hash_pin("1234")


def test_verify_pin_garbage_stored_value():
    assert not verify_pin("1234", "not-a-real-hash")
    assert not verify_pin("1234", "")


def test_owner_token_claims():
    claims = decode_token(create_token(business_id=BID, scope="owner"))
    assert claims["business_id"] == BID
    assert claims["scope"] == "owner"
    assert "staff_id" not in claims


def test_pos_token_carries_staff():
    claims = decode_token(create_token(business_id=BID, scope="pos", staff_id=SID))
    assert claims["scope"] == "pos"
    assert claims["staff_id"] == SID


def test_expired_token_rejected():
    token = create_token(business_id=BID, scope="owner", ttl_minutes=-1)
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_token(token)


def test_tampered_token_rejected():
    token = create_token(business_id=BID, scope="owner")
    with pytest.raises(pyjwt.PyJWTError):
        decode_token(token[:-2] + "xx")


def test_registration_and_pairing_scopes():
    assert decode_token(create_registration_token("628123"))["scope"] == "register"
    assert decode_token(create_pairing_token(BID))["scope"] == "pos-pairing"


def test_otp_shape_and_hash():
    code = generate_otp()
    assert len(code) == 6 and code.isdigit()
    assert hash_otp(code) == hash_otp(code)
    assert hash_otp("000000") != hash_otp("000001")
