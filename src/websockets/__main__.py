from __future__ import annotations

import argparse
import os
import signal
import sys
import threading


try:
    import readline  # noqa: F401
except ImportError:  # Windows has no `readline` normally
    pass

from .frames import Close
from .sync.client import ClientConnection, connect
from .version import version as websockets_version


if sys.platform == "win32":

    def win_enable_vt100() -> None:
        """
        Enable VT-100 for console output on Windows.

        See also https://github.com/python/cpython/issues/73245.

        """
        import ctypes

        STD_OUTPUT_HANDLE = ctypes.c_uint(-11)
        INVALID_HANDLE_VALUE = ctypes.c_uint(-1)
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x004

        handle = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle == INVALID_HANDLE_VALUE:
            raise RuntimeError("unable to obtain stdout handle")

        cur_mode = ctypes.c_uint()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(cur_mode)) == 0:
            raise RuntimeError("unable to query current console mode")

        # ctypes ints lack support for the required bit-OR operation.
        # Temporarily convert to Py int, do the OR and convert back.
        py_int_mode = int.from_bytes(cur_mode, sys.byteorder)
        new_mode = ctypes.c_uint(py_int_mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING)

        if ctypes.windll.kernel32.SetConsoleMode(handle, new_mode) == 0:
            raise RuntimeError("unable to set console mode")


def print_during_input(string: str) -> None:
    sys.stdout.write(
        # Save cursor position
        "\N{ESC}7"
        # Add a new line
        "\N{LINE FEED}"
        # Move cursor up
        "\N{ESC}[A"
        # Insert blank line, scroll last line down
        "\N{ESC}[L"
        # Print string in the inserted blank line
        f"{string}\N{LINE FEED}"
        # Restore cursor position
        "\N{ESC}8"
        # Move cursor down
        "\N{ESC}[B"
    )
    sys.stdout.flush()


def print_over_input(string: str) -> None:
    sys.stdout.write(
        # Move cursor to beginning of line
        "\N{CARRIAGE RETURN}"
        # Delete current line
        "\N{ESC}[K"
        # Print string
        f"{string}\N{LINE FEED}"
    )
    sys.stdout.flush()


def print_incoming_messages(websocket: ClientConnection, stop: threading.Event) -> None:
    for message in websocket:
        if isinstance(message, str):
            print_during_input("< " + message)
        else:
            print_during_input("< (binary) " + message.hex())
    if not stop.is_set():
        # When the server closes the connection, raise KeyboardInterrupt
        # in the main thread to exit the program.
        if sys.platform == "win32":
            ctrl_c = signal.CTRL_C_EVENT
        else:
            ctrl_c = signal.SIGINT
        os.kill(os.getpid(), ctrl_c)


def main() -> None:
    # Parse command line arguments.
    parser = argparse.ArgumentParser(
        prog="python -m websockets",
        description="Interactive WebSocket client.",
        add_help=False,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--version", action="store_true")
    group.add_argument("uri", metavar="<uri>", nargs="?")
    args = parser.parse_args()

    if args.version:
        print(f"websockets {websockets_version}")
        return

    if args.uri is None:
        parser.error("the following arguments are required: <uri>")

    # If we're on Windows, enable VT100 terminal support.
    if sys.platform == "win32":
        try:
            win_enable_vt100()
        except RuntimeError as exc:
            sys.stderr.write(
                f"Unable to set terminal to VT100 mode. This is only "
                f"supported since Win10 anniversary update. Expect "
                f"weird symbols on the terminal.\nError: {exc}\n"
            )
            sys.stderr.flush()

    try:
        websocket = connect(args.uri)
    except Exception as exc:
        print(f"Failed to connect to {args.uri}: {exc}.")
        sys.exit(1)
    else:
        print(f"Connected to {args.uri}.")

    stop = threading.Event()

    # Start the thread that reads messages from the connection.
    thread = threading.Thread(target=print_incoming_messages, args=(websocket, stop))
    thread.start()

    # Read from stdin in the main thread in order to receive signals.
    try:
        while True:
            # Since there's no size limit, put_nowait is identical to put.
            message = input("> ")
            websocket.send(message)
    except (KeyboardInterrupt, EOFError):  # ^C, ^D
        stop.set()
        websocket.close()

        assert websocket.close_code is not None and websocket.close_reason is not None
        close_status = Close(websocket.close_code, websocket.close_reason)
        print_over_input(f"Connection closed: {close_status}.")

    thread.join()


if __name__ == "__main__":
    main()
