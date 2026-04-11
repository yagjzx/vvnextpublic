import socket
import datetime
from unittest.mock import patch, MagicMock


from vvnext.health import (
    check_tcp,
    check_udp,
    check_tls_expiry,
    check_wg_tunnel,
    check_service_status,
    check_fleet,
    AlertDebouncer,
    HealthReport,
    CheckResult,
    format_telegram_message,
    send_telegram_alert,
)
from vvnext.inventory import Inventory
from vvnext.settings import Settings


# ---------------------------------------------------------------------------
# TCP checks
# ---------------------------------------------------------------------------

def test_tcp_check_success():
    """Mock socket.connect succeeds -> ok=True"""
    with patch("vvnext.health.socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        result = check_tcp("1.2.3.4", 443)
    assert result.ok is True
    assert result.check_type == "tcp"
    assert result.target == "1.2.3.4:443"
    mock_sock.connect.assert_called_once_with(("1.2.3.4", 443))
    mock_sock.close.assert_called_once()


def test_tcp_check_failure():
    """Mock socket.connect raises -> ok=False"""
    with patch("vvnext.health.socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = ConnectionRefusedError("refused")
        mock_sock_cls.return_value = mock_sock
        result = check_tcp("1.2.3.4", 443)
    assert result.ok is False
    assert "refused" in result.detail


def test_tcp_check_timeout():
    """Mock socket.connect times out -> ok=False"""
    with patch("vvnext.health.socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock.connect.side_effect = socket.timeout("timed out")
        mock_sock_cls.return_value = mock_sock
        result = check_tcp("1.2.3.4", 443, timeout=1.0)
    assert result.ok is False
    assert "timed out" in result.detail


# ---------------------------------------------------------------------------
# UDP checks
# ---------------------------------------------------------------------------

def test_udp_check_timeout():
    """UDP check with timeout (no response) -> ok=True (UDP ports don't always respond)"""
    with patch("vvnext.health.socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock.recvfrom.side_effect = socket.timeout("timed out")
        mock_sock_cls.return_value = mock_sock
        result = check_udp("1.2.3.4", 51941)
    assert result.ok is True
    assert "no response" in result.detail


def test_udp_check_response():
    """UDP port responds with data -> ok=True"""
    with patch("vvnext.health.socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (b"data", ("1.2.3.4", 51941))
        mock_sock_cls.return_value = mock_sock
        result = check_udp("1.2.3.4", 51941)
    assert result.ok is True
    assert "response received" in result.detail


def test_udp_check_refused():
    """UDP port returns ICMP unreachable -> ok=False"""
    with patch("vvnext.health.socket.socket") as mock_sock_cls:
        mock_sock = MagicMock()
        mock_sock.sendto.side_effect = ConnectionRefusedError("ICMP unreachable")
        mock_sock_cls.return_value = mock_sock
        result = check_udp("1.2.3.4", 51941)
    assert result.ok is False
    assert "ICMP unreachable" in result.detail


# ---------------------------------------------------------------------------
# TLS expiry
# ---------------------------------------------------------------------------

def _make_tls_mocks(not_after_str):
    """Helper: set up ssl/socket mocks returning a cert with the given notAfter."""
    mock_ctx = MagicMock()
    mock_raw_sock = MagicMock()
    mock_tls_sock = MagicMock()
    mock_tls_sock.getpeercert.return_value = {"notAfter": not_after_str}
    # context manager support
    mock_tls_sock.__enter__ = MagicMock(return_value=mock_tls_sock)
    mock_tls_sock.__exit__ = MagicMock(return_value=False)
    mock_ctx.wrap_socket.return_value = mock_tls_sock
    mock_raw_sock.__enter__ = MagicMock(return_value=mock_raw_sock)
    mock_raw_sock.__exit__ = MagicMock(return_value=False)
    return mock_ctx, mock_raw_sock


def test_tls_expiry_valid():
    """Mock SSL cert with future expiry -> ok=True"""
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90)
    not_after = future.strftime("%b %d %H:%M:%S %Y GMT")
    mock_ctx, mock_raw_sock = _make_tls_mocks(not_after)

    with patch("vvnext.health.ssl.create_default_context", return_value=mock_ctx), \
         patch("vvnext.health.socket.create_connection", return_value=mock_raw_sock):
        result = check_tls_expiry("1.2.3.4", 20001)

    assert result.ok is True
    assert "expires in" in result.detail
    assert "warning" not in result.detail


def test_tls_expiry_soon():
    """Mock SSL cert expiring within 30 days -> ok=True but detail warns"""
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=10)
    not_after = future.strftime("%b %d %H:%M:%S %Y GMT")
    mock_ctx, mock_raw_sock = _make_tls_mocks(not_after)

    with patch("vvnext.health.ssl.create_default_context", return_value=mock_ctx), \
         patch("vvnext.health.socket.create_connection", return_value=mock_raw_sock):
        result = check_tls_expiry("1.2.3.4", 20001)

    assert result.ok is True
    assert "warning" in result.detail


def test_tls_expiry_expired():
    """Mock SSL cert already expired -> ok=False"""
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=5)
    not_after = past.strftime("%b %d %H:%M:%S %Y GMT")
    mock_ctx, mock_raw_sock = _make_tls_mocks(not_after)

    with patch("vvnext.health.ssl.create_default_context", return_value=mock_ctx), \
         patch("vvnext.health.socket.create_connection", return_value=mock_raw_sock):
        result = check_tls_expiry("1.2.3.4", 20001)

    assert result.ok is False
    assert "expired" in result.detail


def test_tls_expiry_connection_error():
    """TLS connection fails -> ok=False"""
    with patch("vvnext.health.ssl.create_default_context") as _, \
         patch("vvnext.health.socket.create_connection",
               side_effect=socket.timeout("timed out")):
        result = check_tls_expiry("1.2.3.4", 20001)

    assert result.ok is False
    assert "timed out" in result.detail


# ---------------------------------------------------------------------------
# WG tunnel ping
# ---------------------------------------------------------------------------

def test_wg_tunnel_success():
    """Ping succeeds via SSH -> ok=True"""
    mock_ssh = MagicMock()
    mock_ssh.exec.return_value = ("PING OK\n", "", 0)
    result = check_wg_tunnel(mock_ssh, "hk-gcp-a", "10.240.10.3")
    assert result.ok is True
    assert result.check_type == "wg_ping"
    assert "tunnel alive" in result.detail


def test_wg_tunnel_failure():
    """Ping fails via SSH -> ok=False"""
    mock_ssh = MagicMock()
    mock_ssh.exec.return_value = ("", "timeout", 1)
    result = check_wg_tunnel(mock_ssh, "hk-gcp-a", "10.240.10.3")
    assert result.ok is False
    assert "ping failed" in result.detail


# ---------------------------------------------------------------------------
# Service status
# ---------------------------------------------------------------------------

def test_service_status_active():
    """sing-box active -> ok=True"""
    mock_ssh = MagicMock()
    mock_ssh.exec.return_value = ("active\n", "", 0)
    result = check_service_status(mock_ssh, "hk-gcp-a")
    assert result.ok is True
    assert "active" in result.detail


def test_service_status_inactive():
    """sing-box inactive -> ok=False"""
    mock_ssh = MagicMock()
    mock_ssh.exec.return_value = ("inactive\n", "", 3)
    result = check_service_status(mock_ssh, "hk-gcp-a")
    assert result.ok is False
    assert "inactive" in result.detail


# ---------------------------------------------------------------------------
# AlertDebouncer
# ---------------------------------------------------------------------------

def test_debouncer_threshold():
    """3 consecutive failures needed before should_alert returns True"""
    debouncer = AlertDebouncer(threshold=3)
    fail = CheckResult(node="hk-gcp-a", check_type="tcp",
                       target="1.2.3.4:443", ok=False, detail="refused")

    assert debouncer.should_alert("hk-gcp-a", fail) is False  # count=1
    assert debouncer.should_alert("hk-gcp-a", fail) is False  # count=2
    assert debouncer.should_alert("hk-gcp-a", fail) is True   # count=3
    assert debouncer.should_alert("hk-gcp-a", fail) is True   # count=4, still True


def test_debouncer_reset_on_success():
    """Success resets failure counter"""
    debouncer = AlertDebouncer(threshold=3)
    fail = CheckResult(node="hk-gcp-a", check_type="tcp",
                       target="1.2.3.4:443", ok=False, detail="refused")
    ok = CheckResult(node="hk-gcp-a", check_type="tcp",
                     target="1.2.3.4:443", ok=True, detail="port open")

    debouncer.should_alert("hk-gcp-a", fail)  # count=1
    debouncer.should_alert("hk-gcp-a", fail)  # count=2
    assert debouncer.should_alert("hk-gcp-a", ok) is False  # resets
    assert debouncer.should_alert("hk-gcp-a", fail) is False  # count=1 again


def test_debouncer_explicit_reset():
    """reset() clears failure counter"""
    debouncer = AlertDebouncer(threshold=2)
    fail = CheckResult(node="node-a", check_type="tcp",
                       target="x:1", ok=False)
    debouncer.should_alert("node-a", fail)
    debouncer.reset("node-a")
    assert debouncer.should_alert("node-a", fail) is False  # count=1 after reset


def test_debouncer_independent_nodes():
    """Different nodes have independent counters"""
    debouncer = AlertDebouncer(threshold=2)
    fail_a = CheckResult(node="node-a", check_type="tcp",
                         target="x:1", ok=False)
    fail_b = CheckResult(node="node-b", check_type="tcp",
                         target="y:1", ok=False)

    debouncer.should_alert("node-a", fail_a)  # a=1
    debouncer.should_alert("node-b", fail_b)  # b=1
    assert debouncer.should_alert("node-a", fail_a) is True   # a=2
    assert debouncer.should_alert("node-b", fail_b) is True   # b=2


# ---------------------------------------------------------------------------
# HealthReport
# ---------------------------------------------------------------------------

def test_health_report_summary():
    """Test summary string format"""
    report = HealthReport(results=[
        CheckResult(node="a", check_type="tcp", target="x:1", ok=True),
        CheckResult(node="b", check_type="tcp", target="x:2", ok=False, detail="err"),
        CheckResult(node="c", check_type="tcp", target="x:3", ok=True),
    ])
    assert report.summary() == "2/3 checks passed"
    assert report.all_ok is False
    assert len(report.failed) == 1
    assert report.failed[0].node == "b"


def test_health_report_all_ok():
    """All passing -> all_ok True"""
    report = HealthReport(results=[
        CheckResult(node="a", check_type="tcp", target="x:1", ok=True),
        CheckResult(node="b", check_type="tcp", target="x:2", ok=True),
    ])
    assert report.all_ok is True
    assert len(report.failed) == 0
    assert report.summary() == "2/2 checks passed"


def test_health_report_empty():
    """Empty report -> all_ok True, 0/0"""
    report = HealthReport()
    assert report.all_ok is True
    assert report.summary() == "0/0 checks passed"


# ---------------------------------------------------------------------------
# check_fleet
# ---------------------------------------------------------------------------

def _make_inventory_with_residential():
    """Create inventory with near, far, and residential nodes."""
    return Inventory(**{
        "servers": [
            {"name": "hk-gcp-a", "role": "near", "region": "hk", "provider": "gcp",
             "public_ip": "10.0.0.1", "port_base": 20000, "sni": "dl.google.com",
             "hy2_sni": "hk.test.com", "cdn_domain": "hk-cdn.test.com",
             "dns_name": "hk-a.test.com"},
            {"name": "us-gcp-a", "role": "far", "region": "us", "provider": "gcp",
             "public_ip": "10.0.0.3", "wg_port": 51941},
            {"name": "us-home-att2", "role": "residential", "region": "us",
             "provider": "home", "public_ip": "192.168.1.100",
             "tailscale_ip": "100.64.0.5", "wg_port": 51942},
        ]
    })


@patch("vvnext.health.check_tls_expiry")
@patch("vvnext.health.check_udp")
@patch("vvnext.health.check_tcp")
def test_fleet_check_near_ports(mock_tcp, mock_udp, mock_tls):
    """Verify correct ports checked for near nodes"""
    mock_tcp.return_value = CheckResult(node="", check_type="tcp",
                                        target="", ok=True)
    mock_udp.return_value = CheckResult(node="", check_type="udp",
                                        target="", ok=True)
    mock_tls.return_value = CheckResult(node="", check_type="tls_expiry",
                                        target="", ok=True)

    inv = Inventory(**{
        "servers": [
            {"name": "hk-gcp-a", "role": "near", "region": "hk", "provider": "gcp",
             "public_ip": "10.0.0.1", "port_base": 20000, "sni": "dl.google.com",
             "hy2_sni": "hk.test.com", "cdn_domain": "hk-cdn.test.com",
             "dns_name": "hk-a.test.com"},
        ]
    })
    settings = Settings()
    report = check_fleet(inv, settings)

    # Near node should have: 3 TCP (port_base+1, +2, cdn=2053, anytls=8443 is also TCP)
    # Actually: TCP on port_base+1, port_base+2, cdn_port(2053), anytls_port(8443) = 4 TCP
    # UDP on 443 = 1 UDP
    # TLS on port_base+1 = 1 TLS
    # Total = 6 checks for one near node
    assert len(report.results) == 6

    tcp_calls = mock_tcp.call_args_list
    tcp_ports = [call.args[1] for call in tcp_calls]
    assert 20001 in tcp_ports  # Reality overlay
    assert 20002 in tcp_ports  # Reality direct
    assert 2053 in tcp_ports   # CDN
    assert 8443 in tcp_ports   # AnyTLS

    udp_calls = mock_udp.call_args_list
    assert udp_calls[0].args == ("10.0.0.1", 443)  # HY2

    tls_calls = mock_tls.call_args_list
    assert tls_calls[0].args == ("10.0.0.1", 20001)  # TLS on Reality port

    # All results should have node name set
    for r in report.results:
        assert r.node == "hk-gcp-a"


@patch("vvnext.health.check_tls_expiry")
@patch("vvnext.health.check_udp")
@patch("vvnext.health.check_tcp")
def test_fleet_check_far_node(mock_tcp, mock_udp, mock_tls):
    """Far nodes only get UDP check on wg_port"""
    mock_udp.return_value = CheckResult(node="", check_type="udp",
                                        target="", ok=True)

    inv = Inventory(**{
        "servers": [
            {"name": "us-gcp-a", "role": "far", "region": "us", "provider": "gcp",
             "public_ip": "10.0.0.3", "wg_port": 51941},
        ]
    })
    settings = Settings()
    report = check_fleet(inv, settings)

    assert len(report.results) == 1
    assert mock_tcp.call_count == 0
    assert mock_tls.call_count == 0
    mock_udp.assert_called_once_with("10.0.0.3", 51941)


@patch("vvnext.health.check_tls_expiry")
@patch("vvnext.health.check_udp")
@patch("vvnext.health.check_tcp")
def test_fleet_check_residential_uses_tailscale(mock_tcp, mock_udp, mock_tls):
    """Residential nodes use tailscale_ip not public_ip"""
    mock_udp.return_value = CheckResult(node="", check_type="udp",
                                        target="", ok=True)

    inv = _make_inventory_with_residential()
    settings = Settings()

    # Patch near TCP/TLS to not interfere
    mock_tcp.return_value = CheckResult(node="", check_type="tcp",
                                        target="", ok=True)
    mock_tls.return_value = CheckResult(node="", check_type="tls_expiry",
                                        target="", ok=True)

    check_fleet(inv, settings)

    # Find the UDP calls for far/residential nodes
    udp_calls = mock_udp.call_args_list
    # There should be: 1 for near HY2 (10.0.0.1:443),
    #                  1 for far (10.0.0.3:51941),
    #                  1 for residential (100.64.0.5:51942)
    udp_hosts = [(c.args[0], c.args[1]) for c in udp_calls]
    assert ("100.64.0.5", 51942) in udp_hosts  # residential uses tailscale_ip
    assert ("10.0.0.3", 51941) in udp_hosts    # far uses public_ip
    # Ensure the residential's public_ip was NOT used
    residential_ips = [h for h, p in udp_hosts if p == 51942]
    assert "192.168.1.100" not in residential_ips


# ---------------------------------------------------------------------------
# Telegram message formatting
# ---------------------------------------------------------------------------

def test_telegram_message_format():
    """Verify status indicators in message"""
    report = HealthReport(results=[
        CheckResult(node="hk-gcp-a", check_type="tcp",
                    target="10.0.0.1:20001", ok=True, detail="port open"),
        CheckResult(node="hk-gcp-a", check_type="tcp",
                    target="10.0.0.1:20002", ok=False, detail="refused"),
    ])
    msg = format_telegram_message(report)
    assert "1/2 checks passed" in msg
    assert "[FAIL]" in msg
    assert "[X]" in msg
    assert "hk-gcp-a" in msg
    assert "refused" in msg


def test_telegram_message_all_ok():
    """All passing shows OK message"""
    report = HealthReport(results=[
        CheckResult(node="a", check_type="tcp", target="x:1", ok=True),
    ])
    msg = format_telegram_message(report)
    assert "[OK] All checks passed" in msg
    assert "[FAIL]" not in msg


# ---------------------------------------------------------------------------
# send_telegram_alert
# ---------------------------------------------------------------------------

def test_send_telegram_disabled():
    """Telegram disabled -> returns False, no HTTP call"""
    settings = Settings()  # telegram.enabled defaults to False
    report = HealthReport()
    assert send_telegram_alert(report, settings) is False


@patch("vvnext.health.httpx.post")
def test_send_telegram_success(mock_post):
    """Telegram enabled with valid token -> sends and returns True"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_post.return_value = mock_resp

    settings = Settings(**{
        "alerting": {
            "telegram": {
                "enabled": True,
                "bot_token": "123:ABC",
                "chat_id": "-100123",
            }
        }
    })
    report = HealthReport(results=[
        CheckResult(node="a", check_type="tcp", target="x:1", ok=True),
    ])
    assert send_telegram_alert(report, settings) is True
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "123:ABC" in call_kwargs.args[0]
    assert call_kwargs.kwargs["json"]["chat_id"] == "-100123"


@patch("vvnext.health.httpx.post")
def test_send_telegram_http_error(mock_post):
    """HTTP error during send -> returns False"""
    import httpx as _httpx
    mock_post.side_effect = _httpx.HTTPError("connection failed")

    settings = Settings(**{
        "alerting": {
            "telegram": {
                "enabled": True,
                "bot_token": "123:ABC",
                "chat_id": "-100123",
            }
        }
    })
    report = HealthReport()
    assert send_telegram_alert(report, settings) is False


def test_send_telegram_missing_token():
    """Telegram enabled but missing bot_token -> returns False"""
    settings = Settings(**{
        "alerting": {
            "telegram": {
                "enabled": True,
                "bot_token": "",
                "chat_id": "-100123",
            }
        }
    })
    report = HealthReport()
    assert send_telegram_alert(report, settings) is False
