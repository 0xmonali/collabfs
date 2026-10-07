"""
client.py

Collaborative Network File System - Client
Computer Networks Mini Project
Authors: Tanishq, Monali, Ananyaa

Two things happen at once on the client, which is why this file uses two
coroutines instead of one simple request/reply loop:

  1. the user typing commands and waiting for the server's reply
  2. the server pushing unsolicited "file_updated" / "file_deleted"
     broadcasts whenever another client saves or deletes a file we have
     open, which can arrive at any moment

Both kinds of message arrive on the same TCP connection, in the order the
server sent them. A background "listener" task reads every incoming
message and sorts it: a broadcast is handled (and printed) immediately;
anything else is handed to whichever command is currently waiting for a
reply, via an asyncio.Queue. Without this split, a broadcast that arrived
while we were waiting for, say, our own "edit_ok" could be mistaken for
that reply.
"""

import asyncio

from protocol import send_msg, recv_msg

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5555

# filename -> {"content": str, "revision": int} for files we currently
# have open. This is our local cache -- the server's copy is always the
# real one.
watched_files: dict[str, dict] = {}

reply_queue: asyncio.Queue = asyncio.Queue()


async def listener(reader: asyncio.StreamReader) -> None:
    """Runs for the whole life of the connection. Reads every incoming
    message and routes it: broadcasts get handled right here, anything
    else gets handed off to whichever command is waiting for a reply."""
    while True:
        msg = await recv_msg(reader)
        if msg is None:
            print("\n[!] server closed the connection")
            break

        if msg["type"] == "file_updated":
            handle_file_updated(msg)
        elif msg["type"] == "file_deleted":
            handle_file_deleted(msg)
        else:
            await reply_queue.put(msg)


def handle_file_updated(msg):
    filename = msg["filename"]
    if filename in watched_files:
        watched_files[filename] = {"content": msg["content"], "revision": msg["revision"]}
        print(f"\n[notice] '{filename}' was updated by {msg['edited_by']} "
              f"(now revision {msg['revision']})")
        print("> ", end="", flush=True)


def handle_file_deleted(msg):
    filename = msg["filename"]
    if filename in watched_files:
        del watched_files[filename]
        print(f"\n[notice] '{filename}' was deleted by {msg['deleted_by']}")
        print("> ", end="", flush=True)


async def request(writer: asyncio.StreamWriter, msg: dict) -> dict:
    """Send one message and wait for its reply. Broadcasts never land
    here -- the listener task already filters those out."""
    await send_msg(writer, msg)
    return await reply_queue.get()


# ---------------------------------------------------------------------------
# Commands -- each one builds a message, sends it, and prints the result
# ---------------------------------------------------------------------------

async def cmd_list(writer):
    reply = await request(writer, {"type": "list"})
    if not reply["files"]:
        print("(no files on server)")
        return
    for entry in reply["files"]:
        mark = " [open]" if entry["filename"] in watched_files else ""
        print(f"  {entry['filename']}  (revision {entry['revision']}){mark}")


async def cmd_create(writer, filename, username):
    reply = await request(writer, {"type": "create", "filename": filename, "username": username})
    if reply["type"] == "error":
        print(f"error: {reply['reason']}")
    else:
        print(f"created '{filename}' (revision 0)")


async def cmd_open(writer, filename, username):
    """Opening always fetches the server's current content and revision --
    never a locally cached guess. This is also what makes reconnecting
    safe: open() again after reconnecting and you're caught up, by
    construction, rather than needing separate "resync" logic."""
    reply = await request(writer, {"type": "open", "filename": filename, "username": username})
    if reply["type"] == "error":
        print(f"error: {reply['reason']}")
        return
    watched_files[filename] = {"content": reply["content"], "revision": reply["revision"]}
    print(f"--- {filename} (revision {reply['revision']}) ---")
    print(reply["content"] if reply["content"] else "(empty file)")
    print("--- end ---")


async def cmd_edit(writer, filename, username):
    if filename not in watched_files:
        print(f"'{filename}' isn't open -- use 'open {filename}' first")
        return

    # Capture the revision we're basing this edit on BEFORE we start
    # typing, not after. If someone else's update arrives mid-typing (the
    # listener task updates watched_files in the background), our save
    # attempt should still be judged against what we actually saw when we
    # started editing -- which is exactly the conflict case we want to
    # demo and catch, not paper over.
    base_revision = watched_files[filename]["revision"]

    print("Enter the new content. Finish with a line containing only EOF")
    lines = []
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, input)
        if line == "EOF":
            break
        lines.append(line)
    new_content = "\n".join(lines)

    reply = await request(writer, {
        "type": "edit",
        "filename": filename,
        "base_revision": base_revision,
        "content": new_content,
        "username": username,
    })

    if reply["type"] == "edit_ok":
        watched_files[filename] = {"content": new_content, "revision": reply["revision"]}
        print(f"saved '{filename}' (revision {reply['revision']})")

    elif reply["type"] == "edit_rejected":
        # The conflict case: someone else saved first. We refuse to
        # silently overwrite them -- show what's actually on the server
        # now, update our local copy to match it, and let the user redo
        # their edit on top of it instead of losing either version.
        print(f"REJECTED: {reply['reason']}")
        print(f"server is now at revision {reply['current_revision']}:")
        print("--- current content ---")
        print(reply["current_content"] if reply["current_content"] else "(empty file)")
        print("--- end ---")
        watched_files[filename] = {
            "content": reply["current_content"],
            "revision": reply["current_revision"],
        }
        print("local copy updated -- run 'edit' again to redo your change on top of this")

    else:
        print(f"error: {reply.get('reason', reply)}")


async def cmd_delete(writer, filename, username):
    reply = await request(writer, {"type": "delete", "filename": filename, "username": username})
    if reply["type"] == "error":
        print(f"error: {reply['reason']}")
    else:
        watched_files.pop(filename, None)
        print(f"deleted '{filename}'")


HELP = """\
Commands:
  list              list files on the server
  create <name>     create a new empty file
  open <name>       open (or refresh) a file -- needed before editing it
  edit <name>       replace an open file's content, with conflict checking
  delete <name>     delete a file
  help              show this message
  quit              disconnect
"""


async def main() -> None:
    username = input("Enter your username: ").strip() or "anonymous"

    reader, writer = await asyncio.open_connection(SERVER_HOST, SERVER_PORT)
    print(f"Connected to server at {SERVER_HOST}:{SERVER_PORT} as '{username}'")

    listener_task = asyncio.create_task(listener(reader))

    print(HELP)
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = (await loop.run_in_executor(None, input, "> ")).strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            cmd = parts[0]
            arg = parts[1] if len(parts) > 1 else ""

            if cmd == "quit":
                break
            elif cmd == "help":
                print(HELP)
            elif cmd == "list":
                await cmd_list(writer)
            elif cmd == "create" and arg:
                await cmd_create(writer, arg, username)
            elif cmd == "open" and arg:
                await cmd_open(writer, arg, username)
            elif cmd == "edit" and arg:
                await cmd_edit(writer, arg, username)
            elif cmd == "delete" and arg:
                await cmd_delete(writer, arg, username)
            else:
                print("unknown command or missing filename -- type 'help'")
    finally:
        listener_task.cancel()
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
