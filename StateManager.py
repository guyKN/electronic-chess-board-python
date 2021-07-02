from __future__ import annotations

import asyncio

import boardController
import chess
import chess.engine
import chess.polyglot

import FileManager
from ScanThread import ScanThread
from BluetoothManager import BluetoothManager
from ChessGame import ChessGame, WaitingForSetupState, PlayerType, LedTestState
from State import State
import random
import string
# engine path: "/home/pi/chess-engine/stockfish3/Stockfish-sf_13/src/stockfish"

def open_opening_book(path="/home/pi/chess-engine/opening-book/Perfect2021.bin"):
    return chess.polyglot.open_reader(path)

legal_setting_keys = {"learningMode"}

def generate_game_id():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=15))


def exception_handler(loop, context):
    # first, handle with default handler
    loop.default_exception_handler(context)
    print(context)
    print("exception occurred")
    loop.stop()

class StateManager:
    def __init__(self, is_test = False):
        self.is_test = is_test
        self.board = boardController.scanBoard()
        self.engine = None
        self.event_loop = asyncio.get_event_loop()
        asyncio.set_event_loop_policy(chess.engine.EventLoopPolicy())
        asyncio.set_event_loop(self.event_loop)
        self.event_loop.set_exception_handler(exception_handler)
        self.event_loop.run_until_complete(self.open_engine())

        self.scan_thread = ScanThread(
            callback=lambda board:
                self.event_loop.call_soon_threadsafe(self.on_board_change, board))
        self._settings = FileManager.read_settings()
        self._engine_settings = FileManager.read_engine_settings()
        self._opening_book = open_opening_book()
        self.bluetooth_manager = BluetoothManager(self)
        self.game = self.create_game()
        self.waiting_for_piece_setup_state = WaitingForSetupState(self)
        self.state = self.waiting_for_piece_setup_state
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
            print("Error updating settings: Illegal Key Given")
            return False
        for key, value in new_settings.items():
            self._settings[key] = value

        self.game.learning_mode = self._settings["learningMode"]
        self.state.on_board_changed(self.board) # refresh the board so that the leds changed to the new settings.

        FileManager.write_settings(self._settings)
        return True

    def force_bluetooth_moves(self, game_id: str, bluetooth_player: chess.Color, moves: str):
        if not self.is_game_active() or game_id is None or game_id != self.game.game_id:
            # the requested game has a different id than the current game, so we need to create a new game
            self.game = self.create_game(bluetooth_player=bluetooth_player, game_id=game_id)
            self.go_to_state(self.waiting_for_piece_setup_state)
        try:
            self.game.force_moves(moves)
        except ValueError as e:
            print(f"Error trying to force game moves: {str(e)}")

    def on_game_start_request(self, enable_engine, engine_color, engine_level, game_id=None, start_fen = None):
        if self.game is not None and self.game.game_id is not None and self.game.game_id == game_id:
            print("request to start game with the same id. Skipping. ")
            return
        if engine_color != "white" and engine_color != "black":
            raise ValueError("Invalid Engine Color")
        if engine_level > 20 or engine_level < 1:
            raise ValueError("Invalid Engine Level")
        self._engine_settings["enableEngine"] = enable_engine
        self._engine_settings["engineColor"] = engine_color
        self._engine_settings["engineLevel"] = engine_level
        FileManager.write_engine_settings(self._engine_settings)
        if start_fen is None:
            start_fen = chess.STARTING_FEN
        self.game = self.create_game(game_id=game_id, start_fen=start_fen)
        self.go_to_state(self.waiting_for_piece_setup_state)



    # todo: bring back pgn round, or replace it with another form of id
    def create_game(self, bluetooth_player = None, game_id = None, start_fen = chess.STARTING_FEN):
        print(f"creating game: bluetoothPlayer: {bluetooth_player}")
        if game_id is None:
            game_id = generate_game_id()
        if bluetooth_player is None:
            white_is_engine = self._engine_settings["enableEngine"] and self._engine_settings["engineColor"] == "white"
            white_player_type = PlayerType.ENGINE if white_is_engine else PlayerType.HUMAN

            black_is_engine = self._engine_settings["enableEngine"] and self._engine_settings["engineColor"] == "black"
            black_player_type = PlayerType.ENGINE if black_is_engine else PlayerType.HUMAN
        else:
            white_player_type = PlayerType.BLUETOOTH if bluetooth_player == chess.WHITE else PlayerType.HUMAN
            black_player_type = PlayerType.BLUETOOTH if bluetooth_player == chess.BLACK else PlayerType.HUMAN

        return ChessGame(learning_mode=self._settings["learningMode"],
                         white_player_type=white_player_type,
                         black_player_type=black_player_type,
                         engine_skill=int(self._engine_settings["engineSkill"]),
                         opening_book=self._opening_book,
                         state_manager=self,
                         start_fen=start_fen,
                         engine=self.engine,
                         game_id=game_id)
    def start_game(self):
        self.go_to_state(self.game)
        self.bluetooth_manager.send_game()
        self.bluetooth_manager.send_board_state()

    def is_game_active(self):
        return self.state is self.game

    def is_game_started(self):
        return self.is_game_active() and self.game.is_started()

    def on_game_move(self):
        self.bluetooth_manager.send_board_state()


    def wait_for_piece_setup(self):
        self.game = self.create_game()
        self.go_to_state(self.waiting_for_piece_setup_state)

    def on_game_end(self):
        if self.game.should_save_game():
            FileManager.write_pgn(self.game.get_pgn())
            self.bluetooth_manager.send_num_games_to_upload()
        self.bluetooth_manager.send_is_game_active()

    def test_leds(self):
        if isinstance(self.state, ChessGame):
            self.state.test_leds()
        elif isinstance(self.state, LedTestState):
            self.go_to_state(self.state)
        else:
            self.go_to_state(LedTestState(self, self.state))

    async def open_engine(self):
        print("opening engine")
        if self.engine is None:
            transport, self.engine = await chess.engine.popen_uci("/home/pi/chess-engine/stockfish3/Stockfish-sf_13/src/stockfish")
        print("done opening engine")
        return self.engine


    async def close_engine(self):
        if self.engine is not None:
            await self.engine.quit()

    def cleanup(self):
        self.event_loop.run_until_complete(self.close_engine())
        self._opening_book.close()
        FileManager.write_settings(self._settings)
