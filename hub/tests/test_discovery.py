from coco_hub import discovery, identity
from coco_hub.settings import Tls


def test_service_info_describes_coco_service(settings):
    ident = identity.load_or_create(settings.data_dir)
    info = discovery.build_service_info(settings, addresses=["192.168.1.20"], ident=ident)
    assert info.type == "_coco._tcp.local."
    assert info.name == f"{settings.discovery.name}._coco._tcp.local."
    assert info.port == settings.port
    assert info.parsed_addresses() == ["192.168.1.20"]
    assert info.properties[b"api"] == b"v1"


def test_txt_record_names_the_hub_and_its_fingerprint(settings):
    ident = identity.load_or_create(settings.data_dir)
    info = discovery.build_service_info(settings, addresses=["192.168.1.20"], ident=ident)
    assert info.properties[b"id"] == ident.hub_id.encode()
    assert info.properties[b"fp"] == ident.short_fingerprint.encode()


def test_txt_record_has_no_fingerprint_without_tls(settings):
    ident = identity.load_or_create(settings.data_dir)
    plain = settings.model_copy(update={"tls": Tls(enabled=False)})
    info = discovery.build_service_info(plain, addresses=["192.168.1.20"], ident=ident)
    assert info.properties[b"id"] == ident.hub_id.encode()
    assert b"fp" not in info.properties


def test_explicit_host_is_advertised(settings):
    s = settings.model_copy(update={"host": "10.0.0.5"})
    assert discovery.lan_addresses(s) == ["10.0.0.5"]


def test_wildcard_host_uses_non_loopback_ipv4(settings):
    addrs = discovery.lan_addresses(settings)
    assert all(not a.startswith("127.") and ":" not in a for a in addrs)
