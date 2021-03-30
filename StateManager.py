from __future__ import annotations

import asyncio

import boardController
import chess
import chess.engine
import chess.polyglot

import FileManager
from AsyncScan import ScanThread
from BluetoothManager import BluetoothManager
from ChessGame import ChessGame, WaitingForSetupState, PlayerType
from State import State

# engine path: "/home/pi/chess-engine/stockfish3/Stockfish-sf_13/src/stockfish"

def open_opening_book(path="/home/pi/chess-engine/opening-book/Perfect2021.bin"):
    return chess.polyglot.open_reader(path)

legal_setting_keys = {"enable_engine", "engine_skill", "engine_color", "learning_mode"}
setting_keys_requiring_game_restart = {"enable_engine", "engine_skill", "engine_color"}


def exception_handler(loop, context):
    # first, handle with default handler
    loop.default_exception_handler(context)
    print(context)
    print("exception occurred")
    loop.stop()

class StateManager:
    def __init__(self):
        self.board = boardController.scanBoard()
        self._settings = FileManager.read_settings()
        self.engine = None
        self._opening_book = open_opening_book()
        self.bluetooth_manager = BluetoothManager(self)
        self.game = self.create_game()

        self.event_loop = asyncio.get_event_loop()
        asyncio.set_event_loop_policy(chess.engine.EventLoopPolicy())
        asyncio.set_event_loop(self.event_loop)
        self.event_loop.set_exception_handler(exception_handler)
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
        self.state.on_leave_state()
        self.state = state
        self.init_state(self.state)

    def on_board_change(self, board):
        self.board = board
        self.state.on_board_changed(board)

    """
    Called by other parents of states when they want to switch their subStates, but still remain the state visible to the stateManager.
    Should not be called by non-parent states. those use go_to_state()
    """
    def init_state(self, state):
        state.on_enter_state()
        state.on_board_changed(self.board)

    def get_settings(self):
        return self._settings

    def update_settings(self, new_settings):
        if not legal_setting_keys.issuperset(new_settings.keys()):
            print("Illegal Key Given")
            return False
        if "engine_color" in new_settings.keys() and new_settings["engine_color"] != "white" and \
                new_settings["engine_color"] != "black":
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

        if not setting_keys_requiring_game_restart.isdisjoint(new_settings.keys()):
            # the player has changed a setting that requires the game to be restarted
            self.game.finish_and_restart_game()

        self.game.learning_mode = self._settings["learning_mode"]
        self.state.on_board_changed(self.board) # refresh the board so that the leds changed to the new settings.

        FileManager.write_settings(self._settings)
        return True

    def request_bluetooth_game(self, bluetooth_player_color):
        self.game.finish_game()
        self.game = self.create_game(bluetooth_player_color)
        self.go_to_state(WaitingForSetupState(self))

    # todo: implement
    def force_game_pgn(self, pgn):
        pass



    def create_game(self, bluetooth_player = None):
        if bluetooth_player is None:
            white_is_engine = self._settings["enable_engine"] and self._settings["engine_color"] == "white"
            white_player_type = PlayerType.ENGINE if white_is_engine else PlayerType.HUMAN

            black_is_engine = self._settings["enable_engine"] and self._settings["engine_color"] == "black"
            black_player_type = PlayerType.ENGINE if black_is_engine else PlayerType.HUMAN
        else:
            white_player_type = PlayerType.BLUETOOTH if bluetooth_player == chess.WHITE else PlayerType.HUMAN
            black_player_type = PlayerType.BLUETOOTH if bluetooth_player == chess.BLACK else PlayerType.HUMAN

        return ChessGame(
            learning_mode=self._settings["learning_mode"],
            white_player_type=white_player_type,
            black_player_type=black_player_type,
            engine_skill=int(self._settings["engine_skill"]),
            opening_book=self._opening_book,
            state_manager=self,
            pgn_round=self._settings["round"]
        )
    def start_game(self):
        self.go_to_state(self.game)

    def is_game_active(self):
        return self.state is self.game

    def on_game_move(self, move):
        self.bluetooth_manager.write_pgn()

    def wait_for_piece_setup(self):
        self.game = self.create_game()
        self.go_to_state(WaitingForSetupState(self))

    def on_game_end(self):
        if self.game.should_save_game():
            FileManager.write_pgn(self.game.get_pgn())
            self.bluetooth_manager.write_pgn_file_count()
            self._settings["round"] += 1
            FileManager.write_settings(self._settings)

    async def close_engine(self):
        await self.engine.quit()

    def cleanup(self):
        asyncio.run(self.close_engine())
        self._opening_book.close()
        FileManager.write_settings(self._settings)
