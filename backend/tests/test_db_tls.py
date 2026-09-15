"""Database TLS: always verified for a remote host, against the right CA.

Supabase's pooler chains to Supabase's own root CA, which no system trust store
has, so verifying against the default store fails before the first query. The
fix must keep verification on — these tests pin that a missing CA is an error
and never a quiet fall back to an unverified connection.
"""
import ssl
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.core import db

SUPABASE_URL = "postgresql+asyncpg://app_role.ref:pw@aws-1-ap-southeast-1.pooler.supabase.com:5432/postgres"
OTHER_URL = "postgresql+asyncpg://app:pw@db.example.org:5432/app"


def _write_ca(path) -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root CA")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _subjects(context: ssl.SSLContext) -> list[str]:
    return [dict(x[0] for x in c["subject"]).get("commonName") for c in context.get_ca_certs()]


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://app_role:app_role@localhost:5432/warung_pintar",
    "postgresql+asyncpg://app_role:app_role@127.0.0.1:5432/warung_pintar",
])
def test_local_database_uses_no_tls(url):
    assert db.ssl_context_for(url) is None


def test_explicit_root_cert_is_trusted_and_verification_stays_on(tmp_path):
    ca = tmp_path / "ca.crt"
    _write_ca(ca)
    context = db.ssl_context_for(SUPABASE_URL, str(ca))
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert "Test Root CA" in _subjects(context)
    # Supabase Root 2021 CA has no keyUsage extension, which strict mode rejects.
    assert not context.verify_flags & ssl.VERIFY_X509_STRICT


def test_other_hosts_keep_python_strict_verification():
    context = db.ssl_context_for(OTHER_URL)
    default = ssl.create_default_context()
    assert context.verify_flags == default.verify_flags


def test_the_committed_supabase_ca_is_a_real_ca():
    cert = x509.load_pem_x509_certificate(db.BUNDLED_SUPABASE_CA.read_bytes())
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "Supabase Root 2021 CA"
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is True


def test_supabase_host_defaults_to_the_bundled_ca(tmp_path, monkeypatch):
    bundled = tmp_path / "supabase-ca.crt"
    _write_ca(bundled)
    monkeypatch.setattr(db, "BUNDLED_SUPABASE_CA", bundled)
    context = db.ssl_context_for(SUPABASE_URL)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert "Test Root CA" in _subjects(context)


def test_missing_ca_file_is_an_error_not_an_unverified_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "BUNDLED_SUPABASE_CA", tmp_path / "absent.crt")
    with pytest.raises(FileNotFoundError):
        db.ssl_context_for(SUPABASE_URL)
    with pytest.raises(FileNotFoundError):
        db.ssl_context_for(SUPABASE_URL, str(tmp_path / "also-absent.crt"))


def test_other_remote_hosts_verify_against_the_system_store():
    context = db.ssl_context_for(OTHER_URL)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
