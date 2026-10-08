"""The lines `install` prints, and the one animated line it shows on a terminal while it works.

The animated line is decoration: off a terminal nothing of it is written, and an error in it only switches it
off. It is redrawn with the carriage return alone (no escape sequence, no hidden cursor), so nothing has to be
restored however the program ends.
"""

import os
import threading

DOTS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # "dots" spinner of the cli-spinners collection
INTERVAL = 0.08  # seconds per frame
ASCII = "|/-\\"  # fallback frames


def animates(stream, environ):
    """Only a terminal gets the animated line: no pipe, no file, no log of a build or of a CI system."""
    return stream.isatty() and environ.get("TERM") != "dumb" and "CI" not in environ


def frames(stream, environ, system=os.name):
    """The dots where they can be shown, plain ASCII otherwise.

    An encoding without the dots rules them out. So do the Linux console and Windows consoles: their fonts may
    lack the dots, which the encoding does not tell.
    """
    try:
        DOTS.encode(stream.encoding)
    except (UnicodeError, LookupError, TypeError, AttributeError):
        return ASCII
    return ASCII if system == "nt" or environ.get("TERM") == "linux" else DOTS


def render(frame, text, columns, drawn):
    """What to write for one frame: (the characters, how many cells of the line are then in use).

    The line is cut so that the last column stays free (a character there makes some consoles start a new
    line). If the line it replaces was longer (`drawn` cells), that one is wiped first.
    """
    room = max(columns - 1, 0)
    line = f"{frame} {text}"
    if len(line) > room:
        line = line[: room - 3] + "..." if room > 3 else line[:room]
    wipe = "\r" + " " * min(drawn, room) if drawn > len(line) else ""
    return wipe + "\r" + line, len(line)


class Progress:
    """Prints the lines that stay; on a terminal it keeps one animated line below them.

    `line` prints a line that stays. `text` says what is being done now. Used with `with`, a thread draws a new
    frame every INTERVAL seconds; without it, each call of `tick` draws one.
    """

    def __init__(self, stream, animate=True, environ=os.environ, columns=None, pause=None):
        self._stream = stream
        self._columns = columns or self._terminal_columns
        self._lock = threading.Lock()  # a frame never lands inside a permanent line
        self._stopped = threading.Event()
        self._pause = pause or self._stopped.wait  # true when the animation must end
        self._thread = None
        self._text = ""
        self._frame = 0
        self._drawn = 0  # cells currently drawn
        try:
            self.animated = bool(animate and animates(stream, environ))
            self._frames = frames(stream, environ)
        except Exception:  # noqa: BLE001  (no animation on a stream that cannot be queried)
            self.animated, self._frames = False, ASCII

    def __enter__(self):
        if self.animated:
            self._thread = threading.Thread(target=self._animate, name="progress line", daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exception):
        self.close()

    def line(self, text):
        """Print a line that stays. On a terminal it takes the place of the animated line."""
        with self._lock:
            self._text = ""
            self._safely(self._wipe)
            self._stream.write(text + "\n")
            self._stream.flush()

    def text(self, text):
        """Say what is being done now. A terminal shows it from the next frame on."""
        try:  # printable ASCII only: one cell per character, no cursor movement
            self._text = "".join(character if " " <= character <= "~" else "?" for character in text)
        except TypeError:
            self._text = ""

    def tick(self):
        """Draw the next frame."""
        with self._lock:
            if self.animated and self._text:
                self._safely(self._draw)

    def close(self):
        """End the animation and leave its row empty. Harmless when called again."""
        self._stopped.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(1.0)
        with self._lock:
            self._text = ""
            self._safely(self._wipe, True)  # the whole row: an interrupt leaves ^C on it

    def _animate(self):
        while not self._pause(INTERVAL):
            self.tick()

    def _draw(self):
        columns = self._columns()
        if columns < 2:  # unknown width: draw nothing
            return
        frame = self._frames[self._frame % len(self._frames)]
        self._frame += 1
        characters, cells = render(frame, self._text, columns, self._drawn)
        self._drawn = max(self._drawn, cells)  # so an interrupted write is still wiped
        self._stream.write(characters)
        self._stream.flush()
        self._drawn = cells

    def _wipe(self, row=False):
        if self._drawn:
            room = max(self._columns() - 1, 0)
            self._stream.write("\r" + " " * (room if row else min(self._drawn, room)) + "\r")
            self._stream.flush()
            self._drawn = 0

    def _safely(self, action, *arguments):
        """Decoration must not fail the work it decorates: after any error the animation is off for good."""
        try:
            action(*arguments)
        except Exception:  # noqa: BLE001  (must never reach the installation)
            self.animated = False
            self._drawn = 0

    def _terminal_columns(self):
        try:
            return os.get_terminal_size(self._stream.fileno()).columns
        except (OSError, ValueError, AttributeError):
            return 0
