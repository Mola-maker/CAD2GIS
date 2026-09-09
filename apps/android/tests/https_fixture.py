"""Isolated HTTPS fixture for emulator checks; never deploy as the CAD2GIS server."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import ssl
from urllib.parse import parse_qs

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def certificates(directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    ca_path = directory / "ca.pem"
    if not ca_path.exists():
        now = datetime.now(timezone.utc)
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CAD2GIS isolated emulator test CA")])
        ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=7))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
              .sign(ca_key, hashes.SHA256()))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "10.0.2.2")]))
                .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=7))
                .add_extension(x509.SubjectAlternativeName([
                    x509.IPAddress(ipaddress.ip_address("10.0.2.2")),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        (directory / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (directory / "server-key.pem").write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
    # Android <= 13 uses OpenSSL's historical subject hash for system trust anchors.
    subject_hash = int.from_bytes(hashlib.md5(ca.subject.public_bytes()).digest()[:4], "little")
    name = f"{subject_hash:08x}.0"
    (directory / name).write_bytes(ca_path.read_bytes())
    return name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8943)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    ca_name = certificates(args.directory)
    print(json.dumps({"ca_file": str(args.directory / ca_name), "port": args.port}), flush=True)
    if args.generate_only:
        return

    def event(kind: str, **values: object) -> None:
        with (args.directory / f"events-{args.port}.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"event": kind, **values}) + "\n")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass

        def reply(self, status: int, body: bytes, mime: str = "text/html; charset=utf-8", **headers: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(body)

        def authed(self) -> bool:
            return "fixture_session=verified" in self.headers.get("Cookie", "")

        def do_GET(self) -> None:
            event("get", path=self.path, authenticated=self.authed())
            if self.path == "/_app/login":
                self.reply(200, b'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
                <style>body{font:20px sans-serif;padding:20px}input,button{font:20px sans-serif;padding:15px}</style>
                <h1>CAD2GIS test login</h1><form action="/_app/login" method="post">
                <label>Password <input name="password" type="password" autocomplete="off"></label>
                <button>Sign in</button></form>''')
            elif not self.authed():
                self.reply(303, b"", Location="/_app/login")
            elif self.path == "/download":
                data = b"CAD2GIS authenticated download fixture\n"
                event("download", sha256=hashlib.sha256(data).hexdigest())
                self.reply(200, data, "application/octet-stream", Content_Disposition='attachment; filename="cad2gis-fixture.txt"')
            else:
                self.reply(200, b'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
                <style>body{font:20px sans-serif;padding:20px}a,button,input{display:block;font:20px sans-serif;margin:20px 0;padding:15px}</style>
                <h1>CAD2GIS connected</h1><p id="state">Authenticated server session</p>
                <a href="/download">Download result</a>
                <a href="https://example.com/">Leave origin</a>
                <form action="/upload" method="post" enctype="multipart/form-data">
                <input type="file" name="drawing"><button>Upload drawing</button></form>''')

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path == "/_app/login" and parse_qs(body.decode()).get("password") == ["fixture-password"]:
                event("login", accepted=True)
                self.reply(303, b"", Location="/_app/", Set_Cookie="fixture_session=verified; HttpOnly; Secure; SameSite=Strict; Path=/")
            elif self.path == "/upload" and self.authed():
                event("upload", bytes=len(body), has_fixture=b"CAD2GIS authenticated download fixture" in body)
                self.reply(200, b"<h1>Upload received</h1>")
            else:
                self.reply(401, b"Login required")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.directory / "server.pem", args.directory / "server-key.pem")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
