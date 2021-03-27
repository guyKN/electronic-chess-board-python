import abc

import chess


class State(metaclass=abc.ABCMeta):

    @abc.abstractmethod
    def on_enter_state(self):
        pass

    @abc.abstractmethod
    def on_board_changed(self, board: chess.SquareSet):
        pass