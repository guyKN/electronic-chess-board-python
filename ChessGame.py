from __future__ import annotations

import datetime
import random
import time
from enum import Enum
from typing import Union, Callable, Any

import asyncio
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


STARTING_SQUARES = SquareSet(0xFFFF00000000FFFF)


class WaitingForSetupState(State):
    def __init__(self, game_manager: StateManager.StateManager):
        self._game_manager = game_manager

    def on_enter_state(self):
        boardController.setLeds(0, reset_blink_timer=True)

    def on_board_changed(self, board: chess.SquareSet):
        missing_pieces = STARTING_SQUARES & ~board
        extra_pieces = ~STARTING_SQUARES & board
        num_wrong_pieces = popcount(missing_pieces) + popcount(extra_pieces)
        # if too many pieces are missing, don't blink any leds, because the play probably isn't setting up the board
        if num_wrong_pieces >= 31:
            boardController.setLeds(0)
        elif num_wrong_pieces == 0:
            self._game_manager.start_game()
        else:
            boardController.setLeds(slow_blink_leds=extra_pieces, slow_blink_leds_2=missing_pieces)


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
            boardController.setLeds(const_leds=0, # should there be constant leds?
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
            print("preparing delay handle")
            self._delay_handle = asyncio.get_event_loop().call_later(self.chess_game.confirm_move_delay, self._do_move)


    def on_board_changed(self, board: chess.SquareSet):
        if board != self._board_after_move:
            self.chess_game.go_to_state(self.on_cancel_state)
            print("move canceled")

    def on_leave_state(self):
        if self._delay_handle is not None:
            self._delay_handle.cancel()
            self._delay_handle = None
    def _do_move(self):
        self.chess_game.do_move(self.move)



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
            do_engine_move_state = ForceMoveState(move, self.chess_game)
            self.chess_game.go_to_state(do_engine_move_state)


class ForceMoveState(State):
    def __init__(self, engine_move: chess.Move, chess_game: ChessGame):
        self.chess_game = chess_game
        self.move = engine_move

        self.occupied_before_move = chess_game.occupied()
        self.occupied_after_move = chess_game.occupied_after_move(engine_move)

        self.src_mask = square_mask(engine_move.from_square)
        self.dst_mask = square_mask(engine_move.to_square)

        self.changed_squares = (self.occupied_before_move ^ self.occupied_after_move) | square_mask(engine_move.to_square)
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
            self.chess_game.do_move(self.move)
        elif wrong_pieces_direct or (self.is_capture and (not self.capture_picked_up)):
            # The player has not yet moved the piece from its source to its destination
            boardController.setLeds(slow_blink_leds=self.src_mask, slow_blink_leds_2=self.dst_mask,
                                    fast_blink_leds=extra_pieces_illegal, fast_blink_leds_2=missing_pieces_illegal)
        else:
            # the player has made the base move, but hasn't moved any of the indirectly changed squares (castling, en passant)
            boardController.setLeds(slow_blink_leds=self.pieces_to_remove_indirect, slow_blink_leds_2=self.pieces_to_add_indirect,
                                    fast_blink_leds=extra_pieces_illegal, fast_blink_leds_2=missing_pieces_illegal)

class GameEndIndicatorState(State):
    def __init__(self, leds_to_blink: chess.SquareSet, chess_game: ChessGame):
        self.chess_game = chess_game
        self.leds_to_blink = leds_to_blink

        self._delay_handler = None


    def on_enter_state(self):
        boardController.setLeds(fast_blink_leds=self.leds_to_blink)
        if self._delay_handler is None:
            self._delay_handler =  \
                asyncio.get_running_loop().call_later(ChessGame.GAME_END_DELAY, self.chess_game.finish_game)

    def on_leave_state(self):
        self._delay_handler.cancel()
    def on_board_changed(self, board: chess.SquareSet):
        pass

class AbortLaterState(State):

    def __init__(self, chess_game: ChessGame, on_cancel_state: State):
        self.on_cancel_state = on_cancel_state
        self.chess_game = chess_game
        self._delay_handler = None

    def on_enter_state(self):
        if self._delay_handler is None:
            self._delay_handler = asyncio.get_running_loop().call_later(ChessGame.WRONG_PIECES_ABORT_DELAY,
                                                                        self.chess_game.finish_game)

    def on_board_changed(self, board: chess.SquareSet):
        if not self.chess_game.should_abort(board):
            self.chess_game.go_to_state(self.on_cancel_state)
            return

        missing_pieces = self.chess_game.occupied() - board
        extra_pieces =  board - self.chess_game.occupied()

        boardController.setLeds(fast_blink_leds=extra_pieces, fast_blink_leds_2=missing_pieces)

    def on_leave_state(self):
        if self._delay_handler is not None:
            self._delay_handler.cancel()
            self._delay_handler = None

class PlayerType(Enum):
    HUMAN = 0
    ENGINE = 1
    BLUETOOTH = 2

class ChessGame(State):
    MAX_WRONG_PIECES_UNTIL_ABORT = 8
    WRONG_PIECES_ABORT_DELAY = 2.5
    GAME_END_DELAY = 4

    def __init__(self, *, start_fen=chess.STARTING_FEN, confirm_move_delay=0.3, learning_mode=True,
                 white_player_type=PlayerType.HUMAN, black_player_type=PlayerType.HUMAN,
                 engine_skill=20, opening_book=None, state_manager:StateManager, pgn_round=1):
        self.learning_mode = learning_mode
        self._board = chess.Board(start_fen)
        self.player_types = [black_player_type, white_player_type]
        self.confirm_move_delay = confirm_move_delay
        self._opening_book = opening_book
        self._pgn_game = chess.pgn.Game()
        self._pgn_node = self._pgn_game
        self.state_manager = state_manager
        self._pgn_round = pgn_round

        self.engine_skill = engine_skill
        self.engine_time = 1 if engine_skill <= 20 else engine_skill - 19  # engine skill beyond 20 gives the engine additional time to think
        self._setup_pgn()

        self.state = None

    def on_enter_state(self):
        self.start_new_move()

    def on_board_changed(self, board: chess.SquareSet):
        if board == STARTING_SQUARES and self.occupied() != STARTING_SQUARES:
            # the player has set the pieces back to their original positions, so the game is restarted immediately
            self.finish_game()
        elif self.should_abort(board) and not self.is_aborting():
            # Too many pieces are wrong, wait a short delay and then abort the game
            abort_later_state = AbortLaterState(self, self.state)
            self.go_to_state(abort_later_state)
        else:
            self.state.on_board_changed(board)

    def on_leave_state(self):
        self.state.on_leave_state()

    def go_to_state(self, state):
        if self.state is not None:
            self.state.on_leave_state()
        self.state = state
        self.state_manager.init_state(self.state)

    def do_move(self, move: chess.Move):
        self._board.push(move)
        self._pgn_node = self._pgn_node.add_variation(move)
        if self.state_manager is not None:
            self.state_manager.on_game_move(move)
        self.start_new_move()

    def start_new_move(self):
        if not self.check_game_end():
            if self.player_types[self._board.turn]:
                self.go_to_state(CalculateEngineMoveState(self))
            else:
                self.go_to_state(PlayerMoveBaseState(self))


    def check_game_end(self):
        if self._board.is_checkmate():
            self._pgn_game.headers["Result"] = self._board.result(claim_draw=True)
            loser_king = self._board.pieces(chess.KING, self._board.turn)
            game_end_indicator = GameEndIndicatorState(loser_king, self)
            self.go_to_state(game_end_indicator)
            return True

        elif self._board.is_stalemate() or self._board.is_insufficient_material() or self._board.can_claim_draw():
            self._pgn_game.headers["Result"] = self._board.result(claim_draw=True)
            kings = self._board.kings
            game_end_indicator = GameEndIndicatorState(kings, self)
            self.go_to_state(game_end_indicator)
            return True

        else:
            return False

    def is_aborting(self):
        return isinstance(self.state, AbortLaterState)

    def should_abort(self, board):
        return popcount(board ^ self.occupied()) > ChessGame.MAX_WRONG_PIECES_UNTIL_ABORT

    def finish_game(self):
        self.state_manager.on_game_end()

    def _pop_pgn(self):
        parent = self._pgn_node.parent
        if parent is None:
            raise ValueError("tried to pop pgn while at the root node")

        parent.remove_variation(self._pgn_node)
        self._pgn_node = parent

    def get_pgn_string(self):
        return str(self._pgn_game)

    def get_pgn(self):
        return self._pgn_game

    def _player_name(self, player):
        if self.player_types[player]:
            return "Stockfish Level " + str(self.engine_skill)
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
        return self._board.is_game_over(claim_draw=True) or self._board.ply() >= 8
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


    async def load_engine(self):
        if self.state_manager.engine is None:
            transport, self.state_manager.engine = await chess.engine.popen_uci("/home/pi/chess-engine/stockfish3/Stockfish-sf_13/src/stockfish")
        await self.state_manager.engine.configure({"Skill Level": min(self.engine_skill, 20)})
        return self.state_manager.engine

    async def engine_best_move(self, callback: Callable[[chess.Move], Any]):
        # randomly decide whether to use opening book or not
        if (self._opening_book is not None) and (random.uniform(1, 20) <= self.engine_skill):
            try:
                entry = self._opening_book.choice(self._board)
                await asyncio.sleep(self.engine_time / 4)  # todo: make a better delay
                print("Engine move from Opening book: ", str(entry.move))
                callback(entry.move)
            except IndexError:
                # there is no stored entry in the opening book. Use the engine normally
                pass
        engine = await self.load_engine()
        result = await engine.play(self._board, chess.engine.Limit(time=self.engine_time),
                                   info=chess.engine.Info(chess.engine.INFO_BASIC | chess.engine.INFO_SCORE))
        print("\nengine move: ", result.move, ".\ntime: ", result.info["time"], "\nnps: ", result.info["nps"],
              "\nscore: ", result.info["score"], "\ndepth: ", result.info["depth"], "\nseldepth",
              result.info["seldepth"])

        callback(result.move)


    # todo: allow a player to resign by illlegally moving their king
    # todo: allow takebacks

