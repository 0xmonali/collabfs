"""PHASE 1 (owner: Tanishq) - message framing, shared by server and client (that is why it lives in common/).

TCP is a stream of bytes with no message boundaries, so every message is:
    [4-byte big-endian length][UTF-8 JSON body]
Messages (V1):
  client -> server : hello{client_id} | edit{req_id,base_version,start_line,end_line,new_lines} | sync | ping
  server -> client : snapshot{version,lines} | ack{req_id,version} | reject{req_id,reason,detail,current_version}
                     | update{version,author,start_line,end_line,new_lines} | pong | error{reason}
An edit replaces lines [start_line, end_line) with new_lines (insert: start==end, delete: new_lines==[]).
"""
import asyncio
import json
import struct

MAX_FRAME = 1_048_576                 # 1 MiB cap per message
_HEADER = struct.Struct("!I")         # 4 bytes, network byte order

HELLO, EDIT, SYNC, PING = "hello", "edit", "sync", "ping"
SNAPSHOT, ACK, REJECT, UPDATE, PONG, ERROR = "snapshot", "ack", "reject", "update", "pong", "error"


class ProtocolError(Exception):
    """The peer sent bytes that are not a valid frame/message."""


def encode_message(msg: dict) -> bytes:
    body = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ProtocolError("message too large")
    return _HEADER.pack(len(body)) + body


async def read_message(reader: asyncio.StreamReader):
    """Read one message; None means the peer closed the connection cleanly."""
    try:
        header = await reader.readexactly(_HEADER.size)
    except asyncio.IncompleteReadError as e:
        if not e.partial:
            return None
        raise ProtocolError("connection closed mid-header")
    (length,) = _HEADER.unpack(header)
    if length == 0 or length > MAX_FRAME:
        raise ProtocolError(f"invalid frame length {length}")
    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        raise ProtocolError("connection closed mid-frame")
    try:
        msg = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError("body is not valid UTF-8 JSON")
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("message must be a JSON object with a string 'type'")
    return msg
