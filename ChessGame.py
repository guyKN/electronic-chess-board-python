import time

import boardController
import chess
from chess import Square, SquareSet, engine, polyglot
import random


def lsb(square_set):
    return Square(chess.lsb(int(square_set)))


def popcount(square_set):
    return chess.popcount(int(square_set))


def square_mask(square):
    return SquareSet(1 << square)


class ChessGame:
    def __init__(self, *, board=chess.Board(), confirm_move_delay=0.35,
                 white_is_engine=False, black_is_engine=True, engine_path="stockfish", engine_skill=21, use_opening_book= True, opening_book_path = "/home/pi/chess-engine/opening-book/Perfect2021.bin"):
        self.board = board
        self.board.reset()
        self.is_engine = [black_is_engine, white_is_engine]
        self.confirm_move_delay = confirm_move_delay
        self.use_opening_book = use_opening_book
        if use_opening_book:
            self.opening_book = chess.polyglot.open_reader(opening_book_path)
        self.engine_time = 1 if engine_skill <= 20 else engine_skill - 19 # engine skill beyond 20 gives the engine additional tim to think
        self.engine_skill = engine_skill
        self.game_aborted = False

        if white_is_engine or black_is_engine:
            self.engine = engine.SimpleEngine.popen_uci(engine_path)
            self.engine.configure({"Hash": 16})
            if engine_skill < 20:
                self.engine.configure({"Skill Level": engine_skill})

    def close(self):
        self.opening_book.close()
        self.engine.close()

    def play(self):
        self._wait_for_piece_setup()
        while not self.game_aborted:
            if self.is_engine[self.board.turn]:
                self._do_engine_move()
            else:
                self._read_player_move()
            if self.check_game_end():
                break
        self.close()

    def check_game_end(self):
        # noinspection PyPep8Naming
        GAME_END_DELAY = 4
        if self.board.is_checkmate():
            loser_king = self.board.pieces_mask(piece_type=chess.KING, color=self.board.turn)
            boardController.setLeds(fast_blink_leds=loser_king)
            time.sleep(GAME_END_DELAY)
            boardController.setLeds(0)
            return True
        elif self.board.is_stalemate() or self.board.is_insufficient_material() or self.board.can_claim_draw():
            kings = self.board.kings
            boardController.setLeds(fast_blink_leds=kings)
            time.sleep(GAME_END_DELAY)
            boardController.setLeds(0)
            return True
        else:
            return False

    def _engine_best_move(self):
        # randomly decide whether to use opening book or not
        if self.use_opening_book and ( self.engine_skill == -1 or random.uniform(1,20) <= self.engine_skill):
            try:
                entry = self.opening_book.choice(self.board)
                time.sleep(self.engine_time/4) # todo: make a better delay
                print("Engine move from Opening book: ", str(entry.move))
                return entry.move
            except IndexError:
                # there is not stored entry in the opening book. Use the engine normally
                pass
        result = self.engine.play(self.board, engine.Limit(time=self.engine_time),
                                  info=engine.Info(engine.INFO_BASIC | engine.INFO_SCORE))
        print("engine move: ", result.move, ". nps: ", result.info["nps"], ". score: ", result.info["score"])

        return result.move

    def _do_engine_move(self):
        boardController.setLeds(0)
        engine_move = self._engine_best_move()

        src = engine_move.from_square
        src_mask = square_mask(src)
        dst = engine_move.to_square
        dst_mask = square_mask(dst)
        print(engine_move)
        occpied_before_move = self._occupied()
        is_capture = dst in occpied_before_move
        self.board.push(engine_move)
        occupied_after_move = self._occupied()

        changed_squares = (occpied_before_move ^ occupied_after_move) | square_mask(dst)

        capture_picked_up = False
        prev_occupied = None
        boardController.resetBlinkTimer()
        while True:
            physical_board_occupied = boardController.scanBoard()
            if physical_board_occupied == chess.Board().occupied and\
                    physical_board_occupied != self._occupied() and\
                    physical_board_occupied != occpied_before_move:
                # the player has restarted the game
                self.game_aborted = True
                return False
            elif physical_board_occupied != prev_occupied:
                prev_occupied = physical_board_occupied

                extra_pieces = physical_board_occupied - self._occupied() - changed_squares
                missing_pieces = self._occupied() - physical_board_occupied - changed_squares
                if physical_board_occupied == self._occupied() and (not is_capture or capture_picked_up):
                    # the player has successfully made the engine move
                    return
                elif dst not in physical_board_occupied:
                    capture_picked_up = True
                boardController.setLeds(slow_blink_leds=src_mask, slow_blink_leds_2=dst_mask,
                                        fast_blink_leds=extra_pieces, fast_blink_leds_2=missing_pieces)

    def _legal_moves_from(self, square):
        for move in self.board.legal_moves:
            if move.from_square == square:
                yield move

    def _legal_moves_bb_from(self, square):
        bb = 0
        for move in self._legal_moves_from(square):
            bb |= 1 << move.to_square

        return bb

    # todo: find better name
    def _occupied(self):
        return SquareSet(self.board.occupied)

    def _active_player_pieces(self):
        return SquareSet(self.board.occupied_co[self.board.turn])

    def _inactive_player_pieces(self):
        return SquareSet(self.board.occupied_co[not self.board.turn])

    def _wait_for_piece_setup(self):
        prev_occupied = None
        while True:
            physical_board_occupied = boardController.scanBoard()
            if physical_board_occupied != prev_occupied:
                prev_occupied = physical_board_occupied
                missing_pieces = self._occupied() & ~physical_board_occupied
                extra_pieces = ~self._occupied() & physical_board_occupied
                num_wrong_pieces = popcount(missing_pieces) + popcount(extra_pieces)
                # if too many pieces are missing, don't blink any leds, because the play probably isn't setting up the board
                if num_wrong_pieces > 6:
                    boardController.setLeds(0)
                else:
                    boardController.setLeds(slow_blink_leds=extra_pieces, slow_blink_leds_2=missing_pieces)
                if missing_pieces == 0 and extra_pieces == 0:
                    break

    def _read_player_move(self):
        prev_occupied = None
        while True:
            physical_board_occupied = boardController.scanBoard()
            if physical_board_occupied != prev_occupied:
                prev_occupied = physical_board_occupied
                wrong_pieces = self._occupied() ^ physical_board_occupied
                extra_pieces = physical_board_occupied - self._occupied()
                missing_pieces = self._occupied() - physical_board_occupied

                active_player_missing_pieces = missing_pieces & self._active_player_pieces()
                opponent_missing_pieces = missing_pieces & self._inactive_player_pieces()

                if physical_board_occupied == self._occupied():
                    # the position on the physical board is exactly the same as the one on the board in memory. no leds needed.
                    boardController.setLeds(0)
                elif physical_board_occupied == chess.Board().occupied or self.game_aborted:
                    # the player has set up the pieces back up to the starting position.
                    self.game_aborted = True
                    return
                elif popcount(active_player_missing_pieces) == 1:
                    # the active player has picked up a piece
                    # we also allow an opponent's piece to be picked up, if the player wants to
                    # capture by picking up an enemy piece first and only then picking up his own piece
                    prev_occupied = None  # to ensure that when play_move_from() returns, then
                    square = Square(lsb(active_player_missing_pieces))
                    if self._read_player_move_from(square):
                        return
                else:
                    # A piece has been placed or removed from the board without reason.
                    # Alert the user, by blinking its led
                    boardController.setLeds(fast_blink_leds=missing_pieces, fast_blink_leds_2=extra_pieces)

    # returns true and updates the board if the move was completed, returns false if the move was aborted
    def _read_player_move_from(self, src_square):
        prev_occupied = None
        src_square_mask = square_mask(src_square)
        legal_moves = self._legal_moves_bb_from(src_square)
        capture_square = None
        boardController.resetBlinkTimer()
        while True:
            physical_board_occupied = boardController.scanBoard()
            if physical_board_occupied != prev_occupied:
                prev_occupied = physical_board_occupied

                wrong_pieces = self._occupied() ^ physical_board_occupied ^ src_square_mask

                extra_pieces = physical_board_occupied - self._occupied()
                extra_legal_pieces = extra_pieces & legal_moves
                extra_illegal_pieces = extra_pieces & ~legal_moves

                missing_pieces = self._occupied() - physical_board_occupied
                active_player_missing_pieces = missing_pieces & self._active_player_pieces()

                opponent_missing_pieces = missing_pieces & self._inactive_player_pieces()
                opponent_missing_pieces_legal = opponent_missing_pieces & legal_moves
                opponent_missing_pieces_illegal = opponent_missing_pieces & ~legal_moves
                if physical_board_occupied == chess.Board().occupied and physical_board_occupied != self._occupied():
                    # the player has restarted the game
                    self.game_aborted = True
                    return False
                elif active_player_missing_pieces != src_square_mask:
                    # the player has put the piece back, or picked up another piece
                    return False
                elif popcount(opponent_missing_pieces_legal) == 1 \
                        and not opponent_missing_pieces_illegal and not extra_pieces:
                    # the player has started picked up an enemy piece for capture
                    capture_square = lsb(opponent_missing_pieces_legal)
                    boardController.setLeds(const_leds=square_mask(capture_square), slow_blink_leds=src_square_mask)
                elif capture_square is not None and not wrong_pieces:
                    # the player has made a legal capture
                    move = self.board.find_move(src_square, capture_square)
                    if self._complete_move(move):
                        return True
                elif popcount(extra_legal_pieces) == 1 and not extra_illegal_pieces and not opponent_missing_pieces:
                    # the player has made a legal non-capture move
                    dst_square = lsb(extra_legal_pieces)
                    move = self.board.find_move(src_square, dst_square)
                    prev_occupied = None
                    if self._complete_move(move):
                        return True

                else:
                    boardController.setLeds(const_leds=legal_moves,
                                            slow_blink_leds=src_square_mask,
                                            fast_blink_leds=extra_pieces,
                                            fast_blink_leds_2=missing_pieces ^ src_square_mask)

    # For castling and en-passant moves, this function waits for the player to move or remove all extra pieces needed for the move, and than calls _confirm_move()
    # For other moves, this just calls confirm_move()
    def _complete_move(self, move):

        src_mask = square_mask(move.from_square)
        dst_mask = square_mask(move.to_square)

        occupied_before_move = self._occupied()
        self.board.push(move)
        occupied_after_move = self._occupied()

        changed_squares = occupied_before_move ^ occupied_after_move
        changed_squares_indirect = changed_squares - (src_mask | dst_mask)

        if not changed_squares_indirect:
            # this move is not a castling move or an en-passant move, no need to complete it
            # Directly go to _confirm_move()
            if self._confirm_move(move):
                return True
            else:
                self.board.pop()
                return False

        prev_occupied = None
        while True:
            physical_board_occupied = boardController.scanBoard()
            if physical_board_occupied != prev_occupied:
                prev_occupied = physical_board_occupied
                print("wrong pieces: ")
                wrong_pieces = self._occupied() ^ physical_board_occupied
                missing_pieces = self._occupied() - physical_board_occupied
                extra_pieces = physical_board_occupied - self._occupied()

                print(wrong_pieces)
                if not wrong_pieces:
                    # all pieces are in the correct position
                    if self._confirm_move(move):
                        return True
                    else:
                        self.board.pop()
                        return False
                elif not wrong_pieces.issuperset(changed_squares_indirect):
                    # the player has moved another unrelated piece, meaning that the move was aborted
                    self.board.pop()
                    return False
                else:
                    boardController.setLeds(const_leds=dst_mask,
                                            slow_blink_leds=extra_pieces, slow_blink_leds_2=missing_pieces)

    def _confirm_move(self, move):
        boardController.setLeds(const_leds=square_mask(move.to_square))
        start_time = time.time()
        while time.time() - start_time < self.confirm_move_delay:
            physical_board_occupied = boardController.scanBoard()
            if physical_board_occupied != self._occupied():
                return False
        print("player move: ", str(move))
        return True