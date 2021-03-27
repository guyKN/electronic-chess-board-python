from __future__ import annotations

import asyncio

import boardController
import chess
import chess.engine
import chess.polyglot

import FileManager
from AsyncScan import ScanThread
from BluetoothManager import BluetoothManager
from ChessGame import ChessGame, WaitingForSetupState
from State import State


def open_engine(path="/home/pi/chess-engine/stockfish3/Stockfish-sf_13/src/stockfish"):
    engine = chess.engine.SimpleEngine.popen_uci(path)
    engine.configure({"Hash": 16, "Use NNUE": False})
    return engine

def open_opening_book(path="/home/pi/chess-engine/opening-book/Perfect2021.bin"):
    return chess.polyglot.open_reader(path)

legal_setting_keys = {"enable_engine", "engine_skill", "engine_color", "learning_mode"}
"""
State machine structure: 
Base class state, which represents a state of the chessboard. Has method enter, which is called when the 
"""

class GameManager:
    def __init__(self):
        self.board = boardController.scanBoard()
        self.game = None
        self._settings = FileManager.read_settings()
        self._engine = open_engine()
        self._opening_book = open_opening_book()
        self.bluetooth_manager = BluetoothManager(self)
        self.event_loop = asyncio.new_event_loop()
        self.scan_thread = ScanThread(
            callback=lambda board:
                self.event_loop.call_soon_threadsafe(self.on_board_change, board)
        )

        self.state = WaitingForSetupState(self)
        self.state.on_enter_state()


    def game_loop(self):
        self.scan_thread.start()
        self.event_loop.run_forever()

    def go_to_state(self, state: State):
        self.state = state
        self.state.on_enter_state()
        self.state.on_board_changed(self.board)

    def on_board_change(self, board):
        print("on board change")
        self.board = board
        self.state.on_board_changed(board)


    def get_settings(self):
        return self._settings

    def update_settings(self, new_settings):
        if not legal_setting_keys.issuperset(new_settings.keys()):
            print("Illegal Key Given")
            # illegal key given
            return False
        if "engine_color" in new_settings.keys() and new_settings["engine_color"] != "white" and \
                new_settings["engine_color"] != "black":
            # engine color must be white or black
            print("engine color must be white or black")
            return False

        if "engine_skill" in new_settings.keys():
            try:
                engine_skill = int(new_settings["engine_skill"])
            except ValueError:
                print("Engine skill must be a number")
                return False
            if engine_skill < 1:
                print("Engine skill must be positive")
                return False

        for key, value in new_settings.items():
            self._settings[key] = value

        FileManager.write_settings(self._settings)
        if self.game is not None:
            self.game.learning_mode = self._settings["learning_mode"]

        return True

    def game_active(self):
        return self.game is not None

    def cleanup(self):
        self._engine.close()
        self._opening_book.close()
        FileManager.write_settings(self._settings)

    def create_game(self):
        return ChessGame(
            learning_mode=self._settings["learning_mode"],
            white_is_engine=self._settings["enable_engine"] and self._settings["engine_color"] == "white",
            black_is_engine=self._settings["enable_engine"] and self._settings["engine_color"] == "black",
            engine_skill=int(self._settings["engine_skill"]),
            engine=self._engine if self._settings["enable_engine"] else None,
            opening_book=self._opening_book,
            game_manger=self,
            pgn_round=self._settings["round"]
        )

    def on_game_move(self, move):
        pgn = self.game.get_pgn_string()
        self.bluetooth_manager.write_pgn(pgn)

    def on_game_end(self):
        FileManager.write_pgn(self.game.get_pgn())
        self.bluetooth_manager.write_pgn_file_count()
        self._settings["round"] += 1
        FileManager.write_settings(self._settings)
        self.game = None