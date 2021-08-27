from __future__ import annotations

import asyncio
import datetime
import random
from enum import Enum
from typing import Callable, Any, Iterable, List, Union

import boardController
import chess
import chess.engine
import chess.pgn
from chess import Square, SquareSet

import StateManager
from State import State


def lsb(square_set):
    return Square(chess.lsb(int(square_set)))


def popcount(square_set):
    return chess.popcount(int(square_set))


def square_mask(square):
    return SquareSet(1 << square)


"""
Given two lists, returns the lowest index at which the lists have different values. 
If the lists are identical, returns their length.
"""


def index_of_difference(list1, list2):
    i = 0
    try:
        while list1[i] == list2[i]:
            i += 1
    except IndexError:
        # Ignore index out of bounds errors, since that means we got the end of the list and just return.
        pass
    return i


def is_legal_move_list(move_list):
    board = chess.Board()
    for move in move_list:
        if not board.is_legal(move) or move == chess.Move.null():
            return False
        board.push(move)
    return True


def parse_move_list_string(moves: str) -> List[chess.Move]:
    if moves == "":
        return []
    return list(map(chess.Move.from_uci, moves.split(" ")))


STARTING_SQUARES = SquareSet(0xFFFF00000000FFFF)

class LedTestState(State):
    DURATION = 6
    # todo: ensure this does not conflict with anything
    def __init__(self, parent_state: Union[StateManager, ChessGame], prev_state: State):
        self.prev_state = prev_state
        self.parent_state = parent_state
        self.delay_handler = None

    def on_enter_state(self):
        self.delay_handler = asyncio.get_running_loop().call_later(delay=LedTestState.DURATION, callback=self.return_to_prev_state)

    def return_to_prev_state(self):
        self.parent_state.go_to_state(self.prev_state)

    def on_board_changed(self, board: chess.SquareSet):
        boardController.setLeds(const_leds=board)

    def on_leave_state(self):
        if self.delay_handler is not None:
            self.delay_handler.cancel()
        self.delay_handler = None


class WaitingForSetupState(State):
    def __init__(self, state_manager: StateManager.StateManager):
        self.state_manager = state_manager

        self.max_num_pieces = 0  # the maximum number of pieces that have been on the board,
        # used to determine how long to wait before powering off when there are no pieces left on the board

    def on_enter_state(self):
        pass

    def on_board_changed(self, board: chess.SquareSet):
        missing_pieces = STARTING_SQUARES & ~board
        extra_pieces = ~STARTING_SQUARES & board
        num_wrong_pieces = popcount(missing_pieces) + popcount(extra_pieces)
        num_pieces = popcount(board)

        if num_pieces > self.max_num_pieces:
            self.max_num_pieces = num_pieces

        if board == 0:
            self.state_manager.go_to_state(
                WaitingToPowerOffState(self, self.state_manager, self.should_have_long_power_off_delay()))
        elif num_wrong_pieces == 0:
            self.state_manager.start_game()
        else:
            boardController.setLeds(slow_blink_leds=extra_pieces, slow_blink_leds_2=missing_pieces)

    def should_have_long_power_off_delay(self):
        return self.max_num_pieces <= 4


class WaitingToPowerOffState(State):
    POWER_OFF_DELAY_SHORT = 10
    POWER_OFF_DELAY_LONG = 30
    state_manager: StateManager

    def __init__(self, on_cancel_state: State, state_manager: StateManager, is_long_delay):
        self.state_manager = state_manager
        self.on_cancel_state = on_cancel_state
        self.power_off_delay = WaitingToPowerOffState.POWER_OFF_DELAY_LONG if is_long_delay else WaitingToPowerOffState.POWER_OFF_DELAY_LONG
        self.shutdown_delay_handle = None
        self.cancel_delay_handle = None

    def on_enter_state(self):
        if self.shutdown_delay_handle is None:
            self.shutdown_delay_handle = asyncio.get_running_loop().call_later(self.power_off_delay,
                                                                               self.shutdown_system)

    def on_leave_state(self):
        if self.shutdown_delay_handle is not None:
            self.shutdown_delay_handle.cancel()
            self.shutdown_delay_handle = None

    def on_board_changed(self, board: chess.SquareSet):

        extra_pieces = board - STARTING_SQUARES
        missing_pieces = STARTING_SQUARES - board

        boardController.setLeds(slow_blink_leds=extra_pieces, slow_blink_leds_2=missing_pieces)

        if board != 0:
            # there are pieces on the board, the player has undone the shutdown
            if self.cancel_delay_handle is None:
                self.cancel_delay_handle = asyncio.get_running_loop().call_later(0.5,
                                                                                 lambda: self.state_manager.go_to_state(
                                                                                     self.on_cancel_state))
        else:
            if self.cancel_delay_handle is not None:
                self.cancel_delay_handle.cancel()
                self.cancel_delay_handle = None

    def shutdown_system(self):
        print("Exiting Program.. ")
        asyncio.get_running_loop().stop()


class PlayerMoveBaseState(State):
    def __init__(self, chess_game: ChessGame):
        self._chess_game = chess_game

    def on_enter_state(self):
        pass

    def on_board_changed(self, physical_board_occupied: chess.SquareSet):
        wrong_pieces = self._chess_game.occupied() ^ physical_board_occupied
        extra_pieces = physical_board_occupied - self._chess_game.occupied()
        missing_pieces = self._chess_game.occupied() - physical_board_occupied

        active_player_missing_pieces = missing_pieces & self._chess_game.active_player_pieces()
        opponent_missing_pieces = missing_pieces & self._chess_game.inactive_player_pieces()

        if physical_board_occupied == self._chess_game.occupied():
            # the position on the physical board is exactly the same as the one on the board in memory. no leds needed.
            boardController.setLeds(0)
        elif popcount(active_player_missing_pieces) == 1:
            # the active player has picked up a piece
            # we also allow an opponent's piece to be picked up, if the player wants to
            # capture by picking up an enemy piece first and only then picking up his own piece
            src_square = lsb(active_player_missing_pieces)
            player_move_state = PlayerMoveFromSquareState(src_square=src_square,
                                                          on_cancel_state=self,
                                                          chess_game=self._chess_game)

            self._chess_game.go_to_state(player_move_state)
        else:
            # A piece has been placed or removed from the board without reason.
            # Alert the user, by blinking its led
            boardController.setLeds(fast_blink_leds=extra_pieces, fast_blink_leds_2=missing_pieces)


class PlayerMoveFromSquareState(State):
    def __init__(self, src_square: Square, on_cancel_state: State, chess_game: ChessGame):

        self._chess_game = chess_game
        self._on_cancel_state = on_cancel_state
        self._src_square = src_square
        self._src_square_mask = square_mask(src_square)
        self._legal_moves = chess_game.legal_moves_bb_from(src_square)

        self._capture_square = None

    def on_enter_state(self):
        # todo: should self.capture_square be reset here too?
        boardController.resetBlinkTimer()

    def on_board_changed(self, physical_board_occupied: chess.SquareSet):
        wrong_pieces = self._chess_game.occupied() ^ physical_board_occupied ^ self._src_square_mask

        extra_pieces = physical_board_occupied - self._chess_game.occupied()
        extra_legal_pieces = extra_pieces & self._legal_moves
        extra_illegal_pieces = extra_pieces & ~self._legal_moves

        missing_pieces = self._chess_game.occupied() - physical_board_occupied
        active_player_missing_pieces = missing_pieces & self._chess_game.active_player_pieces()

        opponent_missing_pieces = missing_pieces & self._chess_game.inactive_player_pieces()
        opponent_missing_pieces_legal = opponent_missing_pieces & self._legal_moves
        opponent_missing_pieces_illegal = opponent_missing_pieces & ~self._legal_moves

        if active_player_missing_pieces != self._src_square_mask:
            # the player has canceled the move
            self._chess_game.go_to_state(self._on_cancel_state)
        elif popcount(opponent_missing_pieces_legal) == 1 \
                and not opponent_missing_pieces_illegal and not extra_pieces:
            # the player has started picked up an enemy piece for capture
            self._capture_square = lsb(opponent_missing_pieces_legal)
            boardController.setLeds(const_leds=square_mask(self._capture_square), slow_blink_leds=self._src_square_mask)
        elif self._capture_square is not None and not wrong_pieces:
            # the player has made a legal capture
            move = self._chess_game.find_move(self._src_square, self._capture_square)
            self.complete_move(move)

        elif popcount(extra_legal_pieces) == 1 and not extra_illegal_pieces and not opponent_missing_pieces:
            # the player has made a legal non-capture move
            dst_square = lsb(extra_legal_pieces)
            move = self._chess_game.find_move(self._src_square, dst_square)
            self.complete_move(move)
        else:
            boardController.setLeds(
                const_leds=self._legal_moves if self._chess_game.learning_mode else self._src_square_mask,
                slow_blink_leds=self._src_square_mask if self._chess_game.learning_mode else 0,
                fast_blink_leds=extra_pieces,
                fast_blink_leds_2=missing_pieces ^ self._src_square_mask)

    def complete_move(self, move):
        complete_move_state = CompleteMoveState(self._chess_game, move, self)
        self._chess_game.go_to_state(complete_move_state)


class CompleteMoveState(State):

    def __init__(self, chess_game: ChessGame, move: chess.Move, on_cancel_state: State):
        self._chess_game = chess_game
        self._move = move
        self._on_cancel_state = on_cancel_state

        self._src_mask = square_mask(move.from_square)
        self._dst_mask = square_mask(move.to_square)

        self._occupied_before_move = chess_game.occupied()
        self._occupied_after_move = chess_game.occupied_after_move(move)
        self._changed_squares = self._occupied_before_move ^ self._occupied_after_move
        self._changed_squares_indirect = self._changed_squares - (self._src_mask | self._dst_mask)

    def on_enter_state(self):
        pass

    def on_board_changed(self, physical_board_occupied: chess.SquareSet):
        wrong_pieces = self._occupied_after_move ^ physical_board_occupied
        missing_pieces = self._occupied_after_move - physical_board_occupied
        extra_pieces = physical_board_occupied - self._occupied_after_move

        if not wrong_pieces:
            self.confirm_move()
        elif not wrong_pieces.issuperset(self._changed_squares_indirect):
            # the player has moved another unrelated piece, meaning that the move was aborted
            self._chess_game.go_to_state(self._on_cancel_state)
        else:
            boardController.setLeds(const_leds=0,  # should there be constant leds?
                                    slow_blink_leds=extra_pieces, slow_blink_leds_2=missing_pieces)

    def confirm_move(self):
        confirm_move_state = ConfirmMoveState(self._chess_game, self._move, self)
        self._chess_game.go_to_state(confirm_move_state)


class ConfirmMoveState(State):
    def __init__(self, chess_game: ChessGame, move: chess.Move, on_cancel_state: State):
        self.chess_game = chess_game
        self.move = move
        self.on_cancel_state = on_cancel_state
        self._delay_handle = None
        self._board_after_move = chess_game.occupied_after_move(move)
        self._dst_mask = square_mask(move.to_square)

    def on_enter_state(self):
        boardController.setLeds(const_leds=self._dst_mask)
        if self._delay_handle is None:
            self._delay_handle = asyncio.get_event_loop().call_later(self.chess_game.confirm_move_delay, self._do_move)

    def on_board_changed(self, board: chess.SquareSet):
        if board != self._board_after_move:
            self.chess_game.go_to_state(self.on_cancel_state)

    def on_leave_state(self):
        if self._delay_handle is not None:
            self._delay_handle.cancel()
            self._delay_handle = None

    def _do_move(self):
        self.chess_game.do_move(self.move)
        self.chess_game.start_new_move()


class CalculateEngineMoveState(State):
    def __init__(self, chess_game: ChessGame):
        self.chess_game = chess_game
        self._is_active = False

    def on_enter_state(self):
        self._is_active = True
        asyncio.create_task(self.chess_game.engine_best_move(self.on_best_move_found))
        boardController.setLeds(0)

    def on_board_changed(self, board: chess.SquareSet):
        pass

    def on_leave_state(self):
        self._is_active = False

    def on_best_move_found(self, move: chess.Move):
        if self._is_active:
            do_engine_move_state = ForceMoveState(move, self.chess_game, self.chess_game.start_new_move)
            self.chess_game.go_to_state(do_engine_move_state)


class ForceMoveState(State):
    def __init__(self, engine_move: chess.Move, chess_game: ChessGame, on_complete_callback: Callable[[], Any]):
        self.on_complete_callback = on_complete_callback
        self.chess_game = chess_game
        self.move = engine_move

        self.occupied_before_move = chess_game.occupied()
        self.occupied_after_move = chess_game.occupied_after_move(engine_move)

        self.src_mask = square_mask(engine_move.from_square)
        self.dst_mask = square_mask(engine_move.to_square)

        self.changed_squares = (self.occupied_before_move ^ self.occupied_after_move) | square_mask(
            engine_move.to_square)
        self.changed_squares_direct = self.src_mask | self.dst_mask
        self.changed_squares_indirect = self.changed_squares - self.changed_squares_direct
        self.pieces_to_remove_indirect = self.changed_squares_indirect & self.occupied_before_move
        self.pieces_to_add_indirect = self.changed_squares_indirect & self.occupied_after_move

        self.is_capture = engine_move.to_square in self.occupied_before_move
        self.capture_picked_up = False

    def on_enter_state(self):
        boardController.resetBlinkTimer()

    def on_board_changed(self, physical_board_occupied: chess.SquareSet):
        wrong_pieces = physical_board_occupied ^ self.occupied_after_move
        extra_pieces = physical_board_occupied - self.occupied_after_move
        missing_pieces = self.occupied_after_move - physical_board_occupied

        wrong_pieces_illegal = wrong_pieces - self.changed_squares
        extra_pieces_illegal = extra_pieces - self.changed_squares
        missing_pieces_illegal = missing_pieces - self.changed_squares

        wrong_pieces_direct = wrong_pieces & self.changed_squares_direct
        extra_pieces_direct = extra_pieces & self.changed_squares_direct
        missing_pieces_direct = missing_pieces & self.changed_squares_direct

        wrong_pieces_indirect = wrong_pieces & self.changed_squares_indirect
        extra_pieces_indirect = extra_pieces & self.changed_squares_indirect
        missing_pieces_indirect = missing_pieces & self.changed_squares_indirect

        if self.move.to_square not in physical_board_occupied:
            self.capture_picked_up = True

        if physical_board_occupied == self.occupied_after_move and ((not self.is_capture) or self.capture_picked_up):
            # The player has made the move
            self.chess_game.do_move(self.move, is_forced_move=True)
            self.on_complete_callback()
        elif wrong_pieces_direct or (self.is_capture and (not self.capture_picked_up)):
            # The player has not yet moved the piece from its source to its destination
            boardController.setLeds(slow_blink_leds=self.src_mask, slow_blink_leds_2=self.dst_mask,
                                    fast_blink_leds=extra_pieces_illegal, fast_blink_leds_2=missing_pieces_illegal)
        else:
            # the player has made the base move, but hasn't moved any of the indirectly changed squares (castling, en passant)
            boardController.setLeds(slow_blink_leds=self.pieces_to_remove_indirect,
                                    slow_blink_leds_2=self.pieces_to_add_indirect,
                                    fast_blink_leds=extra_pieces_illegal, fast_blink_leds_2=missing_pieces_illegal)


class ForceMultipleMovesState(State):
    def __init__(self, chess_game: ChessGame, moves: Iterable[chess.Move], forced_winner: str):
        print(f"inside ForceMultipleMovesState.__init__, forced_winner={forced_winner}")
        self.move_iterator = iter(moves)
        self.chess_game = chess_game
        self.forced_winner = forced_winner

    def on_enter_state(self):
        try:
            move = next(self.move_iterator)
            force_move_state = ForceMoveState(move, self.chess_game, lambda: self.chess_game.go_to_state(self))
            self.chess_game.go_to_state(force_move_state)
        except StopIteration:
            # done going through all the moves
            self.chess_game.start_new_move(self.forced_winner)

    def on_board_changed(self, board: chess.SquareSet):
        pass


class GameEndIndicatorState(State):
    def __init__(self, leds_to_blink: chess.SquareSet, chess_game: ChessGame):
        self.chess_game = chess_game
        self.leds_to_blink = leds_to_blink

        self._delay_handler = None

    def on_enter_state(self):
        boardController.setLeds(fast_blink_leds=self.leds_to_blink)
        if self._delay_handler is None:
            self._delay_handler = \
                asyncio.get_running_loop().call_later(ChessGame.GAME_END_DELAY, self.chess_game.finish_and_restart_game)

    def on_leave_state(self):
        self._delay_handler.cancel()

    def on_board_changed(self, board: chess.SquareSet):
        pass


class IdleState(State):
    def __init__(self, chess_game: ChessGame):
        self.chess_game = chess_game

    def on_enter_state(self):
        pass

    def on_board_changed(self, board: chess.SquareSet):
        missing_pieces = self.chess_game.occupied() - board
        extra_pieces = board - self.chess_game.occupied()
        boardController.setLeds(fast_blink_leds=extra_pieces, fast_blink_leds_2=missing_pieces)


class AbortLaterState(State):
    def __init__(self, chess_game: ChessGame, on_cancel_state: State):
        self.on_cancel_state = on_cancel_state
        self.chess_game = chess_game
        self._delay_handler = None

    def on_enter_state(self):
        if self._delay_handler is None:
            self._delay_handler = asyncio.get_running_loop().call_later(ChessGame.WRONG_PIECES_ABORT_DELAY,
                                                                        self.chess_game.finish_and_restart_game)

    def on_board_changed(self, board: chess.SquareSet):
        if not self.chess_game.should_abort(board):
            self.chess_game.go_to_state(self.on_cancel_state)
            return

        missing_pieces = self.chess_game.occupied() - board
        extra_pieces = board - self.chess_game.occupied()

        boardController.setLeds(fast_blink_leds=extra_pieces, fast_blink_leds_2=missing_pieces)

    def on_leave_state(self):
        if self._delay_handler is not None:
            self._delay_handler.cancel()
            self._delay_handler = None


class PlayerType(Enum):
    HUMAN = 0
    ENGINE = 1
    BLUETOOTH = 2

    def simple_name(self):
        names = ["human", "engine", "bluetooth"]
        return names[self.value]


class ChessGame(State):
    MAX_NORMAL_ENGINE_SKILL = 8 # when the engine skill goes beyond this number, instead of getting smarter, stockfish simply gets more time

    MAX_WRONG_PIECES_UNTIL_ABORT = 8
    WRONG_PIECES_ABORT_DELAY = 2.5
    GAME_END_DELAY = 4

    def __init__(self, state_manager: StateManager, *, start_fen=chess.STARTING_FEN, confirm_move_delay=0.3,
                 learning_mode=True,
                 white_player_type=PlayerType.HUMAN, black_player_type=PlayerType.HUMAN,
                 engine_skill=8, opening_book=None, pgn_round=1, engine=None, game_id=None):
        self.learning_mode = learning_mode
        self._board = chess.Board(start_fen)
        self.player_types = [black_player_type, white_player_type]
        self.is_bluetooth_game = white_player_type == PlayerType.BLUETOOTH or black_player_type == PlayerType.BLUETOOTH
        self.confirm_move_delay = confirm_move_delay
        self._opening_book = opening_book
        self._pgn_game = chess.pgn.Game()
        self._pgn_node = self._pgn_game
        self.state_manager = state_manager
        self._pgn_round = pgn_round
        self.is_active = False
        self.engine = engine
        self.game_id = game_id
        self.is_game_over = False
        self.was_last_move_forced = False

        self.engine_skill = engine_skill
        self._setup_pgn()
        self.state = self.state_for_next_move()

    def basic_info(self):
        return {
            "gameId": self.game_id,
            "engineLevel": self.engine_skill,
            "white": self.player_types[chess.WHITE].simple_name(),
            "black": self.player_types[chess.BLACK].simple_name()
        }

    def board_state_info(self):
        return {
            "fen": self.get_fen(),
            "pgn": str(self._pgn_game),
            "lastMove": None if (len(self._board.move_stack) == 0) else str(self._board.move_stack[-1]),
            "moveCount": len(self._board.move_stack),
            "shouldSendMove": self.should_send_last_move()
        }

    def on_enter_state(self):
        self.is_active = True
        self.state.on_enter_state()

    def on_board_changed(self, board: chess.SquareSet):
        if board == STARTING_SQUARES and self.occupied() != STARTING_SQUARES:
            # todo: allow players to move to the starting position with a legal move
            # the player has set the pieces back to their original positions, so the game is restarted immediately
            self.finish_and_restart_game()
        elif self.should_abort(board) and not self.is_aborting():
            # Too many pieces are wrong, wait a short delay and then abort the game
            abort_later_state = AbortLaterState(self, self.state)
            self.go_to_state(abort_later_state)
        else:
            self.state.on_board_changed(board)

    def on_leave_state(self):
        self.is_active = False
        self.state.on_leave_state()

    def go_to_state(self, state):
        if self.state is not None:
            self.state.on_leave_state()
        self.state = state
        if self.is_active:
            self.state_manager.init_state(self.state)

    # todo: handle ValueErrors when calling this method
    def force_moves(self, moves_string: str, forced_winner: str):
        print(f"inside force_moves, forced_winner={forced_winner}")
        if not self.is_bluetooth_game:
            raise ValueError("Moves may only be forced from an external source when playing a bluetooth game.")

        new_moves = parse_move_list_string(moves_string)

        if not is_legal_move_list(new_moves):
            raise ValueError("Illegal moves provided")
        old_moves = self._board.move_stack

        if new_moves == old_moves and forced_winner is None:
            # no change was made to the moves, no action needed
            return

        move_number_of_difference = index_of_difference(new_moves, old_moves)
        new_moves = new_moves[move_number_of_difference:]
        self._pop_board_to_move_number(move_number_of_difference)

        force_multiple_moves_state = ForceMultipleMovesState(self, new_moves, forced_winner)
        self.go_to_state(force_multiple_moves_state)



    def do_move(self, move: chess.Move, is_forced_move = False):
        self.was_last_move_forced = is_forced_move
        self._board.push(move)
        self._pgn_node = self._pgn_node.add_variation(move)
        if self.state_manager is not None:
            self.state_manager.on_game_move()

    def start_new_move(self, forced_winner = None):
        self.go_to_state(self.state_for_next_move(forced_winner))

    def state_for_next_move(self, forced_winner = None):
        print(f"inside state_for_next_move, forced_winner={forced_winner}")
        if forced_winner == "white":
            self._pgn_game.headers["Result"] = "1-0"
            self.is_game_over = True
            loser_king = self._board.pieces(chess.KING, chess.BLACK)
            game_end_indicator = GameEndIndicatorState(loser_king, self)
            return game_end_indicator
        elif forced_winner == "black":
            self._pgn_game.headers["Result"] = "0-1"
            self.is_game_over = True
            loser_king = self._board.pieces(chess.KING, chess.WHITE)
            game_end_indicator = GameEndIndicatorState(loser_king, self)
            return game_end_indicator
        if self._board.is_checkmate():
            self._pgn_game.headers["Result"] = self._board.result()
            self.is_game_over = True
            loser_king = self._board.pieces(chess.KING, self._board.turn)
            game_end_indicator = GameEndIndicatorState(loser_king, self)
            return game_end_indicator
        elif self._board.is_stalemate() or self._board.is_insufficient_material() or self._board.can_claim_draw() or forced_winner == "draw":
            self._pgn_game.headers["Result"] = self._board.result(claim_draw=True)
            self.is_game_over = True
            kings = self._board.kings
            game_end_indicator = GameEndIndicatorState(kings, self)
            return game_end_indicator
        elif self.player_types[self._board.turn] is PlayerType.ENGINE:
            return CalculateEngineMoveState(self)
        elif self.player_types[self._board.turn] is PlayerType.HUMAN:
            return PlayerMoveBaseState(self)
        elif self.player_types[self._board.turn] is PlayerType.BLUETOOTH:
            return IdleState(self)

    def is_started(self):
        return len(self._board.move_stack) != 0 and not self.is_game_over

    def is_aborting(self):
        return isinstance(self.state, AbortLaterState)

    def should_abort(self, board):
        return board == 0 or popcount(board ^ self.occupied()) > ChessGame.MAX_WRONG_PIECES_UNTIL_ABORT

    def finish_and_restart_game(self):
        self.finish_game()
        self.state_manager.wait_for_piece_setup()

    def finish_game(self):
        self.is_game_over = True
        self.state_manager.on_game_end()

    def _pop_board(self):
        self._board.pop()

        parent = self._pgn_node.parent
        if parent is None:
            raise IndexError("tried to pop pgn while at the root node")
        parent.remove_variation(self._pgn_node)
        self._pgn_node = parent

    def _pop_board_to_move_number(self, index):
        while len(self._board.move_stack) > index:
            self._pop_board()

    def get_pgn_string(self):
        return str(self._pgn_game)

    def get_pgn(self):
        return self._pgn_game

    def _player_name(self, player):
        if self.player_types[player] == PlayerType.ENGINE:
            return "Stockfish Level " + str(self.engine_skill)
        elif self.player_types[player] == PlayerType.BLUETOOTH:
            return "Online Opponent"
        else:
            return "Human"

    def _setup_pgn(self):
        self._pgn_game.headers["Event"] = "Electronic Chess Board"
        self._pgn_game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
        self._pgn_game.headers["Round"] = str(self._pgn_round)
        self._pgn_game.headers["White"] = self._player_name(chess.WHITE)
        self._pgn_game.headers["Black"] = self._player_name(chess.BLACK)
        self._pgn_game.headers["Result"] = "*"



    """
    Returns true if the game is significant enough to save after the game ends. 
    Is used to filter out short incomplete unnecessary games from clogging the storage. 
    """

    def should_save_game(self):
        return not self.is_bluetooth_game and (self._board.is_game_over(claim_draw=True) or self._board.ply() >= 8)
    """
    When playing a bluetooth game, the bluetooth client needs to send moves to the server when a move is made on the physical chessboard.
    Returns true if the last move made on the physical chessboard needs to be sent. 
    """
    def should_send_last_move(self):
        return self.is_bluetooth_game and self.player_types[self._board.turn] == PlayerType.BLUETOOTH and not self.was_last_move_forced

    def get_fen(self):
        return self._board.fen()

    def find_move(self, src_square, dst_square):
        return self._board.find_move(src_square, dst_square)

    def legal_moves_from(self, square):
        for move in self._board.legal_moves:
            if move.from_square == square:
                yield move

    def legal_moves_bb_from(self, square):
        bb = 0
        for move in self.legal_moves_from(square):
            bb |= 1 << move.to_square
        return bb

    def occupied(self):
        return SquareSet(self._board.occupied)

    def occupied_after_move(self, move: chess.Move):
        self._board.push(move)
        ret = self.occupied()
        self._board.pop()
        return ret

    def active_player_pieces(self):
        return SquareSet(self._board.occupied_co[self._board.turn])

    def inactive_player_pieces(self):
        return SquareSet(self._board.occupied_co[not self._board.turn])

    def get_stockfish_level(self):
        engine_skill_to_stockfish_level = [1, 4, 7, 10, 13, 16, 18, 20]
        return engine_skill_to_stockfish_level[min(self.engine_skill, ChessGame.MAX_NORMAL_ENGINE_SKILL) - 1]

    def get_stockfish_time(self):
        return max(1, self.engine_skill - ChessGame.MAX_NORMAL_ENGINE_SKILL + 1)



    async def engine_best_move(self, callback: Callable[[chess.Move], Any]):
        # randomly decide whether to use opening book or not
        if (self._opening_book is not None) and (random.uniform(1, ChessGame.MAX_NORMAL_ENGINE_SKILL) <= self.engine_skill):
            try:
                entry = self._opening_book.choice(self._board)
                await asyncio.sleep(0.2)  # todo: make a better delay
                print("Engine move from opening book: ", str(entry.move))
                callback(entry.move)
            except IndexError:
                # there is no stored entry in the opening book. Use the engine normally
                pass

        stockfish_skill_level = self.get_stockfish_level()
        stockfish_time = self.get_stockfish_time()
        print(f"stockfish level: {stockfish_skill_level}, stockfish time: {stockfish_time}")
        result = await self.engine.play(self._board, chess.engine.Limit(time=stockfish_time),
                                        info=chess.engine.Info(chess.engine.INFO_BASIC | chess.engine.INFO_SCORE),
                                        options={"Skill Level": stockfish_skill_level})
        print()
        print("engine move: ", result.move)
        print("time: ", result.info["time"])
        print("nps: ", result.info["nps"])
        print("score: ", result.info["score"])
        print("depth: ", result.info["depth"])
        print()

        callback(result.move)

    def test_leds(self):
        if isinstance(self.state, LedTestState):
            self.go_to_state(self.state)
        else:
            self.go_to_state(LedTestState(self, self.state))

    # todo: allow a player to resign by illlegally moving their king
    # todo: allow takebacks