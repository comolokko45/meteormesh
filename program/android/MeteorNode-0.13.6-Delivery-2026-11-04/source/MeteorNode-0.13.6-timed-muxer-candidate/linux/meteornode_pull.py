#!/usr/bin/env python3
"""MeteorNode: pinned-HTTPS, crash-recoverable, checksum-verified file pull.

Never deletes anything on the phone or local disk. The pairing token must be in
an environment variable, not typed into a command line argument.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
from pathlib import Path


VALID_ID = re.compile(r"^[A-Za-z0-9_-]{3,100}$")


def normalize_fingerprint(value: str) -> str:
    candidate = value.replace(":", "").replace(" ", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise ValueError("Certificate SHA-256 fingerprint must have 64 hex characters")
    return candidate


class MeteorNode:
    def __init__(self, host: str, port: int, fingerprint: str, token: str):
        self.host = host
        self.port = port
        self.fingerprint = normalize_fingerprint(fingerprint)
        if len(token) < 32:
            raise ValueError("Pairing token is missing/too short")
        self.token = token

    def connection(self) -> http.client.HTTPSConnection:
        # Explicit certificate pinning is performed BEFORE the Bearer token is sent.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(self.host, self.port, context=context, timeout=30)
        connection.connect()
        peer = connection.sock.getpeercert(binary_form=True)
        actual = hashlib.sha256(peer).hexdigest()
        if actual != self.fingerprint:
            connection.close()
            raise ssl.SSLError("Certificate pin mismatch: aborting BEFORE authentication")
        return connection

    def get_json(self, path: str) -> dict:
        connection = self.connection()
        try:
            connection.request("GET", path, headers={"Authorization": f"Bearer {self.token}"})
            response = connection.getresponse()
            payload = response.read(8 * 1024 * 1024)
            if response.status != 200:
                raise RuntimeError(f"GET {path}: HTTP {response.status} {payload[:100]!r}")
            return json.loads(payload)
        finally:
            connection.close()

    @staticmethod
    def verify_metadata(path: Path, segment_id: str, size: int, digest: str) -> None:
        """A sidecar is authoritative only when it describes the exact verified MP4."""
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            valid = (isinstance(doc, dict)
                     and doc.get("segment_id") == segment_id
                     and doc.get("sha256") == digest
                     and type(doc.get("size_bytes")) is int
                     and doc["size_bytes"] == size)
        except (OSError, UnicodeError, json.JSONDecodeError):
            valid = False
        if not valid:
            raise RuntimeError(f"Metadata conflict/invalid file; refusing overwrite: {path}")

    @classmethod
    def ensure_metadata(cls, segment: dict, metadata: Path, temporary: Path,
                        size: int, digest: str) -> None:
        """Recover or atomically publish a verified sidecar, never overwrite one."""
        segment_id = segment["segment_id"]
        if metadata.exists():
            cls.verify_metadata(metadata, segment_id, size, digest)
            return
        if temporary.exists():
            cls.verify_metadata(temporary, segment_id, size, digest)
        else:
            # Exclusive creation means a stale/incomplete sidecar is never clobbered.
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(segment, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
        if metadata.exists():
            cls.verify_metadata(metadata, segment_id, size, digest)
            return
        os.replace(temporary, metadata)

    def pull_one(self, segment: dict, destination: Path,
                 min_free_bytes: int = 0) -> str:
        segment_id = segment["segment_id"]
        if not VALID_ID.fullmatch(segment_id):
            raise ValueError(f"Unsafe segment id: {segment_id!r}")
        expected_size = int(segment["size_bytes"])
        expected_hash = str(segment["sha256"]).lower()
        if expected_size < 1 or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            raise ValueError(f"Invalid metadata for {segment_id}")

        video = destination / f"{segment_id}.mp4"
        partial = destination / f"{segment_id}.mp4.part"
        metadata = destination / f"{segment_id}.json"
        temp_meta = destination / f"{segment_id}.json.part"

        # This downloader writes only its own scoped filenames, never local symlink targets.
        if destination.is_symlink() or any(path.is_symlink() for path in (video, partial, metadata, temp_meta)):
            raise RuntimeError(f"Refusing symlink target for segment {segment_id}")

        # Detect metadata conflicts even when the video is already complete.
        if metadata.exists():
            self.verify_metadata(metadata, segment_id, expected_size, expected_hash)
        if temp_meta.exists():
            self.verify_metadata(temp_meta, segment_id, expected_size, expected_hash)

        if video.exists():
            if video.is_file() and video.stat().st_size == expected_size and self.file_sha256(video) == expected_hash:
                self.ensure_metadata(segment, metadata, temp_meta, expected_size, expected_hash)
                return "already verified + sidecar reconciled"
            raise RuntimeError(f"Existing file does not match expected data, refusing overwrite: {video}")

        if partial.exists() and not partial.is_file():
            raise RuntimeError(f"Partial path is not a file: {partial}")
        have = partial.stat().st_size if partial.exists() else 0
        if have > expected_size:
            raise RuntimeError(f"Partial larger than expected, refusing overwrite: {partial}")
        if have < expected_size:
            if min_free_bytes < 0:
                raise ValueError("Minimum free space cannot be negative")
            free = shutil.disk_usage(destination).free
            if expected_size - have > free - min_free_bytes:
                raise RuntimeError(
                    f"Not enough disk space for {segment_id} while retaining "
                    f"{min_free_bytes} reserve bytes; phone original kept"
                )
            connection = self.connection()
            try:
                headers = {"Authorization": f"Bearer {self.token}"}
                if have:
                    headers["Range"] = f"bytes={have}-"
                connection.request("GET", f"/api/v1/segments/{segment_id}/file", headers=headers)
                response = connection.getresponse()
                want = 206 if have else 200
                if response.status != want:
                    raise RuntimeError(f"Resume status {response.status}, wanted {want}")
                if have and response.getheader("Content-Range", "") != (
                    f"bytes {have}-{expected_size - 1}/{expected_size}"
                ):
                    raise RuntimeError("Server returned an invalid resume offset")
                with partial.open("ab" if have else "wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                connection.close()

        if partial.stat().st_size != expected_size:
            raise RuntimeError(f"Incomplete transfer of {segment_id}; kept .part file")
        local_hash = self.file_sha256(partial)
        if local_hash != expected_hash:
            raise RuntimeError(f"SHA-256 mismatch: {segment_id}; original kept on phone")
        # Publish the sidecar first. A crash between MP4 rename and DB commit can
        # always be reconciled on the next run from the verified listing.
        self.ensure_metadata(segment, metadata, temp_meta, expected_size, expected_hash)
        if video.exists():
            raise RuntimeError(f"Video appeared during transfer; refusing overwrite: {video}")
        os.replace(partial, video)
        return "downloaded + SHA256 verified"

    @staticmethod
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Phone's LAN IPv4 address")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--fingerprint", required=True, help="TLS certificate SHA-256 from APK screen")
    parser.add_argument("--token-env", default="METEORNODE_PAIRING_TOKEN", help="Environment variable with pairing token")
    parser.add_argument("--destination", type=Path, required=True, help="Existing or new folder on the Linux PC")
    parser.add_argument("--list", action="store_true", help="Only query available completed segments")
    args = parser.parse_args()
    token = os.environ.get(args.token_env)
    if not token:
        parser.error(f"Set the environment variable {args.token_env} first")
    node = MeteorNode(args.host, args.port, args.fingerprint, token)
    health = node.get_json("/api/v1/health")
    response = node.get_json("/api/v1/segments")
    print(f"{health.get('device_id')}: {health.get('state')}, {len(response['segments'])} ready segments")
    if args.list:
        for item in response["segments"]:
            print(f"{item['segment_id']}  {item['size_bytes']} bytes")
        return
    args.destination.mkdir(parents=True, exist_ok=True)
    for item in response["segments"]:
        try:
            result = node.pull_one(item, args.destination)
            print(f"{item['segment_id']}: {result}")
        except Exception as exc:
            # A single corrupted file cannot cause existing originals to be deleted.
            print(f"{item.get('segment_id')}: ERROR {exc}")


if __name__ == "__main__":
    main()
