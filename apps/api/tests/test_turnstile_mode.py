"""Detecta las claves de prueba publicas de Cloudflare (no protegen nada)."""

from app.security import turnstile


def test_modes(monkeypatch):
    s = turnstile.settings
    monkeypatch.setattr(s, "turnstile_secret_key", "")
    assert turnstile.turnstile_mode() == "off"
    monkeypatch.setattr(s, "turnstile_secret_key", "1x0000000000000000000000000000000AA")
    assert turnstile.turnstile_mode() == "test"
    monkeypatch.setattr(s, "turnstile_secret_key", "0x4AAAAAAAreal-secret-value")
    assert turnstile.turnstile_mode() == "real"
