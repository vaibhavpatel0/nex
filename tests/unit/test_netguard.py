"""Unit checks for the connection center's URL and credential rules."""
from __future__ import annotations

import pytest

from app.connect.credentials import looks_like_model_key
from app.connect.netguard import EgressError, address_allowed, content_type_ok, parse_target
from app.connect.verification import allowed_domains


@pytest.mark.parametrize("ip,ok", [
    ("93.184.216.34", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.5.4", False), ("192.168.0.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False),
    ("::1", False), ("fe80::1", False), ("fc00::1", False), ("::ffff:127.0.0.1", False), ("::ffff:10.0.0.1", False),
    ("240.0.0.1", False), ("198.18.0.1", False),
])
def test_only_global_addresses_are_allowed(ip, ok):
    assert address_allowed(ip) is ok


@pytest.mark.parametrize("url,code", [
    ("ftp://agent.example/", "https_required"),
    ("http://agent.example/", "https_required"),
    ("https://user@agent.example/", "invalid_url"),
    ("https://93.184.216.34/", "ip_literal"),
    ("https://[2606:4700::1]/", "ip_literal"),
    ("https://agent.example:25/", "port_not_allowed"),
    ("https://agent.example:80/", "port_not_allowed"),
    ("https:///nohost", "invalid_url"),
    ("https://" + "a" * 2050 + ".example/", "invalid_url"),
])
def test_rejected_urls(url, code):
    with pytest.raises(EgressError) as ei:
        parse_target(url)
    assert ei.value.code == code


def test_normalisation():
    t = parse_target("HTTPS://Agent.Example.COM./a2a?x=1#frag")
    assert t.url == "https://agent.example.com/a2a?x=1" and t.host == "agent.example.com" and t.port == 443
    assert parse_target("https://bücher.example/").host == "xn--bcher-kva.example"
    assert parse_target("https://agent.example:8443/").origin == "https://agent.example:8443"


def test_content_types():
    assert content_type_ok("application/json", ("application/json",))
    assert content_type_ok("application/a2a+json", ("application/json",))
    assert not content_type_ok("text/html", ("application/json",))


@pytest.mark.parametrize("secret,vendor", [
    ("sk-proj-" + "a" * 40, "OpenAI, Anthropic or OpenRouter"), ("sk-ant-api03-" + "b" * 40, "OpenAI, Anthropic or OpenRouter"),
    ("AIza" + "c" * 35, "Google"), ("hf_" + "d" * 30, "Hugging Face"),
    ("f" * 32 + ".ExampleOnlyNotARealKey_xyz", "Ollama"), ("agent-scoped-token-123", None),
])
def test_model_keys_are_recognised(secret, vendor):
    assert looks_like_model_key(secret) == vendor


def test_parent_domains_for_dns_challenges():
    assert allowed_domains("a2a.agents.example.com") == ["a2a.agents.example.com", "agents.example.com", "example.com"]
