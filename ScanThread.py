from threading import Thread
from typing import Callable, Any

import boardController
from chess import SquareSet

class ScanThread:
    def __init__(self, callback: Callable[[SquareSet], Any]):
        self._callback = callback
        self._thread = Thread(name="ScanThread", target=self._scan_loop)
        self._should_quit = False
    def start(self):
        self._thread.start()

    def quit(self):
        self._should_quit = True

    def _scan_loop(self):

        while True:
            board = boardController.awaitBoardChange()
            self._callback(board)
