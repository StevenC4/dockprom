"""The gateway signing contract. This vector is pinned BYTE-IDENTICALLY in every backend repo
(homelab-slack-gateway, receipt-hopper, garmin-jobs, finance-jobs, dockprom/alert-ack). If
sign.py drifts in any of them, this fails before production does.
"""

from alert_ack.sign import sign, verify

SECRET = "test-secret"
TIMESTAMP = 1700000000
BODY = b'{"kind":"event","slack":{"type":"message"},"meta":{}}'
SIGNATURE = "v1=cba02e25393ae599192c5810f73b16e98298a7d691db540e521d272e6fcdc575"


def test_the_shared_vector():
    assert sign(SECRET, TIMESTAMP, BODY) == SIGNATURE


def test_verify_accepts_a_fresh_signature():
    assert verify(SECRET, str(TIMESTAMP), SIGNATURE, BODY, now=TIMESTAMP)


def test_verify_rejects_a_bad_signature():
    assert not verify(SECRET, str(TIMESTAMP), "v1=deadbeef", BODY, now=TIMESTAMP)


def test_verify_rejects_a_stale_timestamp():
    assert not verify(SECRET, str(TIMESTAMP), SIGNATURE, BODY, now=TIMESTAMP + 3600)


def test_verify_rejects_missing_headers():
    assert not verify(SECRET, None, None, BODY, now=TIMESTAMP)
